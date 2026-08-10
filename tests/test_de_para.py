"""De-para: o vocabulário da Rô traduzido para o plano de contas.

Treze rótulos — CUIDADOS PESSOAIS, INFRA, TAXAS, VIAGEM… — cobrem os 441
lançamentos pendentes da carga inicial. Decidir treze vezes é trabalho de
minutos; decidir 441 vezes é trabalho que não acontece.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo


def _lancar(engine, descricao, valor, rotulo, competencia="2026-03"):
    conta = repo.conta_da_planilha(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(db.transacoes).values(
                data=date(2026, 3, 10), competencia=competencia, descricao=descricao,
                descricao_norm=descricao, valor_centavos=valor, conta_id=conta,
                pessoa="Casal", status="pendente", origem="planilha", ativo=True,
                classificacao_origem=rotulo, hash_dedup=descricao + rotulo,
            )
        )


def _categoria(engine, nome, sub=None):
    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)
    cat = next(c for c in plano if c["nome"] == nome)
    sub_id = next((s["id"] for s in cat["subcategorias"] if s["nome"] == sub), None)
    return cat["id"], sub_id


def test_traduzir_um_rotulo_classifica_todos_os_lancamentos_dele(engine):
    for i in range(5):
        _lancar(engine, f"SALAO {i}", -10_000, "CUIDADOS PESSOAIS")
    _lancar(engine, "MERCADO", -20_000, "CASA")

    with engine.connect() as conn:
        rotulos = {r["rotulo"]: r["quantidade"] for r in repo.rotulos_pendentes(conn)}
    assert rotulos == {"CUIDADOS PESSOAIS": 5, "CASA": 1}

    cat, sub = _categoria(engine, "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética")
    assert repo.salvar_de_para(
        engine, rotulo="CUIDADOS PESSOAIS", categoria_id=cat,
        subcategoria_id=sub, usuario="André",
    ) == 5

    with engine.connect() as conn:
        assert {r["rotulo"] for r in repo.rotulos_pendentes(conn)} == {"CASA"}
        assert len(repo.fila_pendentes(conn)) == 1


def test_a_traducao_vale_para_a_proxima_importacao(engine):
    """Guardar sem aplicar deixa o trabalho pela metade; aplicar sem guardar
    faz a próxima importação perguntar tudo de novo."""
    from parsers.base import Lancamento

    cat, sub = _categoria(engine, "Moradia", "Manutenção & Reforma")
    repo.salvar_de_para(engine, rotulo="INFRA", categoria_id=cat,
                        subcategoria_id=sub, usuario="André")

    repo.importar(
        engine,
        lancamentos=[Lancamento(data=date(2026, 4, 2), descricao="PEDREIRO",
                                valor_centavos=-80_000, origem="planilha",
                                categoria_hint="INFRA")],
        conta_id=repo.conta_da_planilha(engine), arquivo="p.xlsx",
        origem="planilha", usuario="André", usar_ia=False,
    )

    with engine.connect() as conn:
        assert repo.fila_pendentes(conn) == []
        linha = conn.execute(
            sa.select(db.transacoes.c.categoria_id, db.transacoes.c.subcategoria_id)
            .where(db.transacoes.c.descricao == "PEDREIRO")
        ).first()
    assert (linha.categoria_id, linha.subcategoria_id) == (cat, sub)


def test_rotulo_de_despesa_nao_leva_junto_o_dinheiro_que_entrou(engine):
    """A guarda de natureza vale aqui como em todo o resto."""
    _lancar(engine, "SALAO", -10_000, "CUIDADOS PESSOAIS")
    _lancar(engine, "ESTORNO SALAO", 4_000, "CUIDADOS PESSOAIS")

    cat, sub = _categoria(engine, "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética")
    assert repo.salvar_de_para(
        engine, rotulo="CUIDADOS PESSOAIS", categoria_id=cat,
        subcategoria_id=sub, usuario="André",
    ) == 1   # só a saída

    with engine.connect() as conn:
        pendentes = [i["descricao"] for i in repo.fila_pendentes(conn)]
    assert pendentes == ["ESTORNO SALAO"]


def test_traducao_para_categoria_do_andre_leva_o_dono_junto(engine):
    """Filhos & Pensão é do André: traduzir para lá não pode deixar o gasto
    no dono errado."""
    _lancar(engine, "ESCOLA", -150_000, "EXTRA")
    cat, sub = _categoria(engine, "Filhos & Pensão", "Escola & Faculdade")
    assert repo.salvar_de_para(
        engine, rotulo="EXTRA", categoria_id=cat, subcategoria_id=sub, usuario="André",
    ) == 1

    with engine.connect() as conn:
        pessoa = conn.execute(
            sa.select(db.transacoes.c.pessoa).where(db.transacoes.c.descricao == "ESCOLA")
        ).scalar()
    assert pessoa == "André"


def test_desfazer_devolve_a_fila_o_que_a_traducao_classificou(engine):
    """Uma decisão em massa precisa ser reversível para valer a pena tomar."""
    _lancar(engine, "SALAO", -10_000, "CUIDADOS PESSOAIS")
    _lancar(engine, "MANICURE", -6_000, "CUIDADOS PESSOAIS")
    cat, sub = _categoria(engine, "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética")
    repo.salvar_de_para(engine, rotulo="CUIDADOS PESSOAIS", categoria_id=cat,
                        subcategoria_id=sub, usuario="André")
    with engine.connect() as conn:
        assert repo.fila_pendentes(conn) == []

    assert repo.apagar_de_para(engine, "CUIDADOS PESSOAIS") == 2
    with engine.connect() as conn:
        assert repo.listar_de_para(conn) == {}
        assert {i["descricao"] for i in repo.fila_pendentes(conn)} == {"SALAO", "MANICURE"}


def test_desfazer_nao_desmancha_a_correcao_feita_a_mao_depois(engine):
    """Desfazer a decisão em massa não pode desfazer o trabalho fino em cima dela."""
    _lancar(engine, "SALAO", -10_000, "CUIDADOS PESSOAIS")
    _lancar(engine, "REMEDIO", -6_000, "CUIDADOS PESSOAIS")
    cat, sub = _categoria(engine, "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética")
    repo.salvar_de_para(engine, rotulo="CUIDADOS PESSOAIS", categoria_id=cat,
                        subcategoria_id=sub, usuario="André")

    # o remédio não era cuidado pessoal: corrigido à mão para Saúde
    saude, farmacia = _categoria(engine, "Saúde", "Farmácia")
    with engine.connect() as conn:
        remedio = conn.execute(
            sa.select(db.transacoes.c.id).where(db.transacoes.c.descricao == "REMEDIO")
        ).scalar()
    repo.reclassificar(engine, remedio, categoria_id=saude, subcategoria_id=farmacia,
                       pessoa="Rô", usuario="André", criar_regra=False)

    assert repo.apagar_de_para(engine, "CUIDADOS PESSOAIS") == 1   # só o salão
    with engine.connect() as conn:
        assert {i["descricao"] for i in repo.fila_pendentes(conn)} == {"SALAO"}
        categoria = conn.execute(
            sa.select(db.transacoes.c.categoria_id)
            .where(db.transacoes.c.descricao == "REMEDIO")
        ).scalar()
    assert categoria == saude


def test_o_destino_e_uma_escolha_so_categoria_ou_subcategoria(engine):
    """Dois campos encadeados obrigavam a escolher, esperar a tela recarregar e
    só então ver as subcategorias — e às vezes elas nem apareciam."""
    from views.classificacao import _destinos

    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)
    destinos = _destinos(plano, "despesa")

    # a categoria sozinha é um destino válido
    assert destinos["Vestuário & Cuidados Pessoais"][1] is None
    # e cada subcategoria dela também, na mesma lista
    subs = [k for k in destinos if k.startswith("    ↳ Vestuário & Cuidados Pessoais")]
    assert len(subs) == 3
    categoria_id, sub_id = destinos["    ↳ Vestuário & Cuidados Pessoais › Cabeleireiro & Estética"]
    assert sub_id is not None
    assert destinos["Vestuário & Cuidados Pessoais"][0] == categoria_id
    # receita não aparece na lista de despesa
    assert "Trabalho" not in destinos


def test_so_categoria_resolve_o_relatorio_e_deixa_o_detalhe_na_fila(engine):
    """Pedido do André: categoria em massa, subcategoria caso a caso.

    A categoria é o que soma no relatório, então o número já fica certo. Mas a
    subcategoria ainda é escolha de quem olha o lançamento — por isso as linhas
    continuam na fila, agora com a categoria preenchida.
    """
    for i in range(3):
        _lancar(engine, f"SALAO {i}", -10_000, "CUIDADOS PESSOAIS")

    cat, _sub = _categoria(engine, "Vestuário & Cuidados Pessoais")
    assert repo.salvar_de_para(
        engine, rotulo="CUIDADOS PESSOAIS", categoria_id=cat,
        subcategoria_id=None, usuario="André",
    ) == 3

    with engine.connect() as conn:
        # continuam na fila, para escolher a subcategoria uma a uma
        assert len(repo.fila_pendentes(conn)) == 3
        # mas o rótulo sai da lista de traduzir: a categoria já foi decidida
        assert repo.rotulos_pendentes(conn) == []
        # e o relatório já soma na categoria certa
        por_categoria = {
            l["categoria"]: l["total"] for l in analytics.por_categoria(conn, competencia="2026-03")
        }
        assert abs(por_categoria["Vestuário & Cuidados Pessoais"]) == 30_000
        linha = conn.execute(
            sa.select(db.transacoes.c.categoria_id, db.transacoes.c.subcategoria_id)
            .where(db.transacoes.c.descricao == "SALAO 0")
        ).first()
    assert (linha.categoria_id, linha.subcategoria_id) == (cat, None)


def test_com_subcategoria_escolhida_o_lancamento_sai_da_fila(engine):
    _lancar(engine, "SALAO", -10_000, "CUIDADOS PESSOAIS")
    cat, sub = _categoria(engine, "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética")
    repo.salvar_de_para(engine, rotulo="CUIDADOS PESSOAIS", categoria_id=cat,
                        subcategoria_id=sub, usuario="André")
    with engine.connect() as conn:
        assert repo.fila_pendentes(conn) == []
