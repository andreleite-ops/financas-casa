"""Testes de core.repo: contas, plano de contas, metas, busca e uploads.

Os testes usam so a fixture `engine` (nao `conn`): varias funcoes de repo
abrem sua propria transacao via `engine.begin()`, entao qualquer escrita feita
antes por uma conexao com transacao aberta (a fixture `conn`) travaria o
SQLite (arquivo unico, sem escrita concorrente).
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from core.dedup import hash_lancamento
from core.texto import normalizar
from parsers.base import Lancamento


def _categoria_id(conn, nome: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _conta_id(conn, nome: str) -> int:
    return conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar_one()


def _inserir(conn, conta_id, dia, descricao, valor_centavos, categoria_id=None, ativo=True):
    descricao_norm = normalizar(descricao)
    return conn.execute(
        sa.insert(db.transacoes).values(
            data=dia,
            competencia=dia.strftime("%Y-%m"),
            descricao=descricao,
            descricao_norm=descricao_norm,
            valor_centavos=valor_centavos,
            conta_id=conta_id,
            categoria_id=categoria_id,
            pessoa="Casal",
            status="manual",
            confianca=1.0,
            origem="extrato",
            hash_dedup=hash_lancamento(conta_id, dia, valor_centavos, descricao_norm),
            ativo=ativo,
        )
    ).inserted_primary_key[0]


# --------------------------------------------------------------------------
# salvar_conta: criar e editar
# --------------------------------------------------------------------------
def test_salvar_conta_cria(engine):
    conta_id = repo.salvar_conta(
        engine, nome="Conta Teste", tipo="corrente", titular="André",
        instituicao="Banco Teste", parser="generico",
    )
    with engine.begin() as conn:
        conta = repo.conta_por_id(conn, conta_id)
    assert conta["nome"] == "Conta Teste"
    assert conta["tipo"] == "corrente"
    assert conta["titular"] == "André"
    assert conta["instituicao"] == "Banco Teste"
    assert conta["ativa"] is True


def test_salvar_conta_edita(engine):
    conta_id = repo.salvar_conta(
        engine, nome="Conta Original", tipo="corrente", titular="André",
        instituicao="Banco A", parser="generico",
    )
    conta_id_2 = repo.salvar_conta(
        engine, conta_id=conta_id, nome="Conta Renomeada", tipo="cartao", titular="Rô",
        instituicao="Banco B", parser="nubank",
    )
    assert conta_id_2 == conta_id

    with engine.begin() as conn:
        conta = repo.conta_por_id(conn, conta_id)
    assert conta["nome"] == "Conta Renomeada"
    assert conta["tipo"] == "cartao"
    assert conta["titular"] == "Rô"
    assert conta["instituicao"] == "Banco B"
    assert conta["parser"] == "nubank"


# --------------------------------------------------------------------------
# alternar_conta: some de so_ativas, mas o historico continua em analytics
# --------------------------------------------------------------------------
def test_alternar_conta_desativa_mas_mantem_historico_de_transacoes(engine):
    with engine.begin() as conn:
        conta_id = _conta_id(conn, "Bradesco C/C")
        _inserir(conn, conta_id, date(2026, 7, 5), "COMPRA ANTIGA", -5000)

    repo.alternar_conta(engine, conta_id, False)

    with engine.begin() as conn:
        ativas = repo.listar_contas(conn, so_ativas=True)
        assert conta_id not in [c["id"] for c in ativas]

        todas = repo.listar_contas(conn, so_ativas=False)
        conta = next(c for c in todas if c["id"] == conta_id)
        assert conta["ativa"] is False

        historico = analytics.lancamentos(conn, competencia="2026-07")
        assert any(l["descricao"] == "COMPRA ANTIGA" for l in historico)


# --------------------------------------------------------------------------
# salvar_categoria + salvar_subcategoria aparecem no plano de contas
# --------------------------------------------------------------------------
def test_salvar_categoria_e_subcategoria_aparecem_no_plano_de_contas(engine):
    categoria_id = repo.salvar_categoria(engine, nome="Categoria Teste", natureza="despesa")
    subcategoria_id = repo.salvar_subcategoria(engine, categoria_id=categoria_id, nome="Sub Teste")

    with engine.begin() as conn:
        plano = repo.plano_de_contas(conn, natureza="despesa")
    categoria = next(c for c in plano if c["id"] == categoria_id)
    assert categoria["nome"] == "Categoria Teste"
    assert any(s["id"] == subcategoria_id and s["nome"] == "Sub Teste" for s in categoria["subcategorias"])


def test_salvar_categoria_edita_nome_e_ativa(engine):
    categoria_id = repo.salvar_categoria(engine, nome="Categoria X", natureza="despesa")
    categoria_id_2 = repo.salvar_categoria(
        engine, categoria_id=categoria_id, nome="Categoria Y", natureza="despesa", ativa=False
    )
    assert categoria_id_2 == categoria_id

    with engine.begin() as conn:
        plano = repo.plano_de_contas(conn)
    categoria = next(c for c in plano if c["id"] == categoria_id)
    assert categoria["nome"] == "Categoria Y"
    assert categoria["ativa"] is False


# --------------------------------------------------------------------------
# salvar_metas / listar_metas: upsert, nao duplica
# --------------------------------------------------------------------------
def test_salvar_metas_upsert_atualiza_em_vez_de_duplicar(engine):
    with engine.begin() as conn:
        categoria_id = _categoria_id(conn, "Alimentação")
    ano = 2027  # ano sem metas semeadas por padrao

    repo.salvar_metas(engine, ano, {categoria_id: 10.0})
    with engine.begin() as conn:
        qtd_apos_primeira = conn.execute(
            sa.select(sa.func.count()).select_from(db.metas).where(
                db.metas.c.ano == ano, db.metas.c.categoria_id == categoria_id
            )
        ).scalar()
        assert qtd_apos_primeira == 1
        assert repo.listar_metas(conn, ano)[categoria_id] == 10.0

    repo.salvar_metas(engine, ano, {categoria_id: 15.0})
    with engine.begin() as conn:
        qtd_apos_segunda = conn.execute(
            sa.select(sa.func.count()).select_from(db.metas).where(
                db.metas.c.ano == ano, db.metas.c.categoria_id == categoria_id
            )
        ).scalar()
        assert qtd_apos_segunda == 1  # nao duplicou
        assert repo.listar_metas(conn, ano)[categoria_id] == 15.0


# --------------------------------------------------------------------------
# buscar_transacoes: com e sem acento acham o mesmo lancamento
# --------------------------------------------------------------------------
def test_buscar_transacoes_com_e_sem_acento_acha_o_mesmo_lancamento(engine):
    with engine.begin() as conn:
        conta_id = _conta_id(conn, "Nubank Mastercard")
        transacao_id = _inserir(conn, conta_id, date(2026, 7, 8), "PADARIA SÃO JOSÉ", -1500)

    with engine.begin() as conn:
        com_acento = repo.buscar_transacoes(conn, termo="são josé")
        sem_acento = repo.buscar_transacoes(conn, termo="sao jose")

    assert [t["id"] for t in com_acento] == [transacao_id]
    assert [t["id"] for t in sem_acento] == [transacao_id]


# --------------------------------------------------------------------------
# apagar_upload: remove as transacoes e as duplicidades associadas, sem
# violar a FK que aponta transacao_nova_id -> transacoes.id
# --------------------------------------------------------------------------
def test_apagar_upload_remove_transacoes_e_duplicidades(engine):
    with engine.begin() as conn:
        conta_id = _conta_id(conn, "Nubank Mastercard")
    competencia = "2026-07"

    primeiro = repo.importar(
        engine, conta_id=conta_id, origem="extrato", competencia=competencia, usuario="andre",
        arquivo="1.csv", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 7, 5), descricao="PADARIA CENTRAL", valor_centavos=-3000,
                       competencia=competencia, origem="extrato"),
        ],
    )
    segundo = repo.importar(
        engine, conta_id=conta_id, origem="extrato", competencia=competencia, usuario="andre",
        arquivo="2.csv", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 7, 5), descricao="PADARIA CENTRAL", valor_centavos=-3000,
                       competencia=competencia, origem="extrato"),
        ],
    )
    assert segundo["duplicados_exatos"] == 1
    upload_id_2 = segundo["upload_id"]

    with engine.begin() as conn:
        duplicata_id = conn.execute(
            sa.select(db.transacoes.c.id).where(db.transacoes.c.upload_id == upload_id_2)
        ).scalar_one()
        duplicidades_antes = conn.execute(
            sa.select(sa.func.count()).select_from(db.duplicidades).where(
                db.duplicidades.c.transacao_nova_id == duplicata_id
            )
        ).scalar()
        assert duplicidades_antes == 1

    apagados = repo.apagar_upload(engine, upload_id_2)
    assert apagados == 1

    with engine.begin() as conn:
        restantes = conn.execute(
            sa.select(sa.func.count()).select_from(db.transacoes).where(
                db.transacoes.c.upload_id == upload_id_2
            )
        ).scalar()
        assert restantes == 0

        duplicidades_depois = conn.execute(
            sa.select(sa.func.count()).select_from(db.duplicidades).where(
                sa.or_(
                    db.duplicidades.c.transacao_nova_id == duplicata_id,
                    db.duplicidades.c.transacao_existente_id == duplicata_id,
                )
            )
        ).scalar()
        assert duplicidades_depois == 0

        upload_ainda_existe = conn.execute(
            sa.select(sa.func.count()).select_from(db.uploads).where(db.uploads.c.id == upload_id_2)
        ).scalar()
        assert upload_ainda_existe == 0

        # a transacao original do primeiro upload continua intacta
        original_ativa = conn.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.upload_id == primeiro["upload_id"])
        ).scalar_one()
        assert original_ativa is True


# --------------------------------------------------------------------------
# competencias_disponiveis
# --------------------------------------------------------------------------
def test_competencias_disponiveis_lista_distintas_ativas_desc(engine):
    with engine.begin() as conn:
        conta_id = _conta_id(conn, "Nubank Mastercard")
        _inserir(conn, conta_id, date(2026, 7, 5), "LANCAMENTO A", -1000)
        _inserir(conn, conta_id, date(2026, 7, 20), "LANCAMENTO B", -2000)  # mesma competencia
        _inserir(conn, conta_id, date(2026, 5, 5), "LANCAMENTO C", -3000)
        _inserir(conn, conta_id, date(2026, 1, 5), "LANCAMENTO INATIVO", -4000, ativo=False)

    with engine.begin() as conn:
        competencias = repo.competencias_disponiveis(conn)
    assert competencias == ["2026-07", "2026-05"]


def test_plano_de_contas_nao_consulta_uma_vez_por_categoria(engine):
    """A tela de classificação relê o plano a cada toque de campo.

    Uma consulta por categoria custava dezoito idas ao banco por interação —
    com o Supabase em São Paulo e o app nos Estados Unidos, quase três segundos
    entre um campo e o seguinte. Este teste é o que impede a volta do N+1.
    """
    from sqlalchemy import event

    from core import repo

    consultas = []
    event.listen(
        engine, "before_cursor_execute",
        lambda conn, cur, stmt, par, ctx, many: consultas.append(stmt),
    )
    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)

    assert len(consultas) <= 2, f"{len(consultas)} consultas — o N+1 voltou"
    # e continua devolvendo o plano inteiro, com as subcategorias no lugar
    assert len(plano) > 10
    alimentacao = next(c for c in plano if c["nome"] == "Alimentação")
    assert {s["nome"] for s in alimentacao["subcategorias"]} == {
        "No Domicílio", "Fora do Domicílio"
    }


def test_fila_pendentes_corta_por_mes_e_por_texto(engine):
    """O André classifica mês a mês, e o gasto esporádico está espalhado.

    Sem cortar por mês, achar as três linhas de agosto entre centenas é rolar a
    lista inteira. O filtro de texto olha também o rótulo da origem — é ele que
    diz o que a linha é quando a descrição não diz.
    """
    import sqlalchemy as sa

    from core import db, repo

    conta = repo.conta_da_planilha(engine)
    linhas = [
        ("2026-08-05", "COLEGIO SANTA CRUZ", -350_000, "EXTRA"),
        ("2026-08-20", "PADARIA", -5_000, "ALIMENTAÇÃO"),
        ("2026-07-10", "MATERIAL ESCOLAR", -20_000, "EXTRA"),
    ]
    with engine.begin() as conn:
        for data, descricao, valor, rotulo in linhas:
            conn.execute(
                sa.insert(db.transacoes).values(
                    data=date.fromisoformat(data), competencia=data[:7],
                    descricao=descricao, descricao_norm=descricao,
                    valor_centavos=valor, conta_id=conta, pessoa="Casal",
                    status="pendente", origem="planilha", ativo=True,
                    classificacao_origem=rotulo, hash_dedup=descricao + data,
                )
            )

    with engine.connect() as conn:
        assert repo.pendentes_por_competencia(conn) == {"2026-08": 2, "2026-07": 1}
        so_agosto = repo.fila_pendentes(conn, competencia="2026-08")
        assert {i["descricao"] for i in so_agosto} == {"COLEGIO SANTA CRUZ", "PADARIA"}
        # o texto acha pela descrição...
        assert len(repo.fila_pendentes(conn, termo="colegio")) == 1
        # ...e também pelo rótulo que a planilha usou
        assert len(repo.fila_pendentes(conn, termo="EXTRA")) == 2
        # os dois filtros somam
        assert len(repo.fila_pendentes(conn, competencia="2026-08", termo="EXTRA")) == 1


def test_dono_sai_da_descricao_quando_ela_diz(engine):
    """"RO" é a Rô, não Rondônia.

    A carga inicial atribui tudo ao dono do arquivo, porque a planilha da casa
    mistura as contas do casal. Mas a Rô escreve de quem é o gasto no fim da
    descrição, e isso vale mais: são 233 lançamentos só em 2026.
    """
    import sqlalchemy as sa

    from core import db, repo
    from core.texto import chave_estabelecimento, pessoa_na_descricao

    # a chave da memória não pode comer o "RO" achando que é sigla de estado
    assert chave_estabelecimento("INSS RO") == "INSS RO"
    assert chave_estabelecimento("ALMOÇO RO") == "ALMOCO RO"
    assert chave_estabelecimento("INSS ANDRE") == "INSS ANDRE"
    # e a sigla de estado de verdade continua saindo
    assert chave_estabelecimento("EC *CLINICA VERTEX RJ") == "CLINICA VERTEX"
    assert chave_estabelecimento("RESTAURANTE DO ZE SP") == "RESTAURANTE DO ZE"

    assert pessoa_na_descricao("ALMOÇO ANDRÉ") == "André"
    assert pessoa_na_descricao("CONSULTA RO") == "Rô"
    assert pessoa_na_descricao("PADARIA") is None

    conta = repo.conta_da_planilha(engine)
    linhas = [("INSS ANDRE", "André"), ("INSS RO", "Rô"), ("PADARIA", None)]
    with engine.begin() as conn:
        for descricao, _ in linhas:
            conn.execute(
                sa.insert(db.transacoes).values(
                    data=date(2026, 3, 10), competencia="2026-03", descricao=descricao,
                    descricao_norm=descricao, valor_centavos=-50_000, conta_id=conta,
                    pessoa="Rô", status="pendente", origem="planilha", ativo=True,
                    hash_dedup=descricao,
                )
            )

    with engine.connect() as conn:
        # só o do André está com o dono errado; PADARIA não diz nada
        assert repo.dono_pela_descricao(conn) == {"André": 1}
    assert repo.corrigir_dono_pela_descricao(engine) == 1

    with engine.connect() as conn:
        donos = {
            linha.descricao: linha.pessoa
            for linha in conn.execute(
                sa.select(db.transacoes.c.descricao, db.transacoes.c.pessoa)
            )
        }
        assert repo.dono_pela_descricao(conn) == {}   # idempotente
    assert donos == {"INSS ANDRE": "André", "INSS RO": "Rô", "PADARIA": "Rô"}


def test_gasto_da_casa_nao_pode_ficar_com_uma_pessoa_so(engine):
    """O upload pergunta "de quem é este arquivo", e a resposta valia para tudo.

    Na planilha da casa, que junta as contas do casal, a maioria das linhas não
    diz de quem é o gasto. Atribuir todas a uma pessoa fazia o relatório por
    pessoa errar por mais de vinte vezes: quase tudo o que a casa gastou no
    ano aparecia como despesa de uma pessoa só.
    """
    import sqlalchemy as sa

    from core import db, repo

    conta = repo.conta_da_planilha(engine)
    linhas = [("ALMOÇO ANDRÉ", -10_000), ("CONSULTA RO", -20_000),
              ("MERCADO", -50_000), ("PADARIA", -3_000)]
    with engine.begin() as conn:
        for descricao, valor in linhas:
            conn.execute(
                sa.insert(db.transacoes).values(
                    data=date(2026, 3, 10), competencia="2026-03", descricao=descricao,
                    descricao_norm=descricao, valor_centavos=valor, conta_id=conta,
                    pessoa="Rô", status="pendente", origem="planilha", ativo=True,
                    hash_dedup=descricao,
                )
            )

    with engine.connect() as conn:
        orfaos = repo.sem_dono_declarado(conn)
    # só MERCADO e PADARIA: os outros dois dizem de quem são
    assert orfaos == {"quantidade": 2, "despesas": 53_000}

    assert repo.atribuir_ao_casal(engine) == 2
    with engine.connect() as conn:
        donos = {
            linha.descricao: linha.pessoa
            for linha in conn.execute(
                sa.select(db.transacoes.c.descricao, db.transacoes.c.pessoa)
            )
        }
        assert repo.sem_dono_declarado(conn)["quantidade"] == 0   # idempotente
    assert donos == {
        "ALMOÇO ANDRÉ": "Rô",     # ainda não corrigido: é o outro botão
        "CONSULTA RO": "Rô",
        "MERCADO": "Casal",
        "PADARIA": "Casal",
    }


def test_despesa_sem_dono_declarado_e_sempre_do_casal(engine):
    """Regra do André: qualquer despesa sem classificação é do casal.

    Herdar o titular da conta, ou a resposta de "de quem é este arquivo",
    fazia o gasto comum inteiro virar dívida de uma pessoa só.
    """
    import sqlalchemy as sa

    from core import db, repo
    from parsers.base import Lancamento

    # conta de uma pessoa só, e o upload dizendo que o arquivo é dela:
    # nem isso torna a despesa comum dela
    with engine.connect() as conn:
        conta = next(c for c in repo.listar_contas(conn) if c["titular"] == "André")

    repo.importar(
        engine,
        lancamentos=[
            Lancamento(data=date(2026, 3, 2), descricao="MERCADO", valor_centavos=-30_000),
            Lancamento(data=date(2026, 3, 3), descricao="ALMOÇO ANDRÉ", valor_centavos=-9_000),
            Lancamento(data=date(2026, 3, 4), descricao="PENSAO ALIMENTICIA",
                       valor_centavos=-1_560_000),
        ],
        conta_id=conta["id"], arquivo="fatura.csv", origem="extrato",
        usuario="André", pessoa_padrao="André", usar_ia=False,
    )

    with engine.connect() as conn:
        donos = {
            linha.descricao: linha.pessoa
            for linha in conn.execute(
                sa.select(db.transacoes.c.descricao, db.transacoes.c.pessoa)
            )
        }
    assert donos == {
        "MERCADO": "Casal",              # ninguém disse: é da casa
        "ALMOÇO ANDRÉ": "André",         # a descrição diz
        "PENSAO ALIMENTICIA": "André",   # a categoria é dele
    }
