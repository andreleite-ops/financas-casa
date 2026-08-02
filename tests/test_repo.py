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
