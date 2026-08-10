"""Receita digitada à mão — o caso dos atendimentos da Rô.

Ela recebe dos pacientes em dezenas de valores pequenos. Importar linha a linha
seria trabalho sem retorno, e o extrato do Itaú traria os depósitos sem dizer
que são atendimentos. Um total por mês responde a mesma pergunta.
"""

from __future__ import annotations

import pytest

from core import analytics, repo


def _categoria_trabalho(engine):
    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn, natureza="receita")
    cat = next(c for c in plano if c["nome"] == "Trabalho")
    sub = next(s for s in cat["subcategorias"] if s["nome"] == "Prestação de Serviços")
    return cat["id"], sub["id"]


def test_lancamento_manual_entra_nas_receitas_da_pessoa(engine):
    cat, sub = _categoria_trabalho(engine)
    repo.lancar_receita_manual(
        engine, competencia="2026-03", valor_centavos=1_511_000, pessoa="Rô",
        categoria_id=cat, subcategoria_id=sub, descricao="ATENDIMENTOS", usuario="André",
    )

    with engine.connect() as conn:
        resumo = analytics.resumo(conn, competencia="2026-03")
        por_pessoa = {l["pessoa"]: l["total"] for l in analytics.receitas_por_pessoa(conn, ano=2026)}

    assert resumo["receitas"] == 1_511_000
    assert por_pessoa == {"Rô": 1_511_000}
    # não é venda de bem: entra na renda que baliza o orçamento
    assert resumo["renda_recorrente"] == 1_511_000


def test_a_competencia_manda_no_mes_mesmo_com_o_dia_no_fim(engine):
    cat, sub = _categoria_trabalho(engine)
    repo.lancar_receita_manual(
        engine, competencia="2026-02", valor_centavos=100_000, pessoa="Rô",
        categoria_id=cat, subcategoria_id=sub, descricao="ATENDIMENTOS",
        usuario="André", dia=31,   # fevereiro não tem 31
    )
    with engine.connect() as conn:
        lancado = repo.receitas_manuais(conn, 2026)

    assert len(lancado) == 1
    assert lancado[0]["competencia"] == "2026-02"
    with engine.connect() as conn:
        assert analytics.resumo(conn, competencia="2026-02")["receitas"] == 100_000


def test_vale_para_qualquer_pessoa_nao_so_para_a_ro(engine):
    cat, sub = _categoria_trabalho(engine)
    for pessoa, valor in (("André", 500_000), ("Rô", 300_000), ("Casal", 200_000)):
        repo.lancar_receita_manual(
            engine, competencia="2026-04", valor_centavos=valor, pessoa=pessoa,
            categoria_id=cat, subcategoria_id=sub, descricao="RECEBIDO", usuario="André",
        )
    with engine.connect() as conn:
        por_pessoa = {l["pessoa"]: l["total"] for l in analytics.receitas_por_pessoa(conn, ano=2026)}

    assert por_pessoa == {"André": 500_000, "Rô": 300_000, "Casal": 200_000}


def test_recusa_valor_zero_ou_negativo(engine):
    cat, sub = _categoria_trabalho(engine)
    for valor in (0, -100):
        with pytest.raises(ValueError):
            repo.lancar_receita_manual(
                engine, competencia="2026-05", valor_centavos=valor, pessoa="Rô",
                categoria_id=cat, subcategoria_id=sub, descricao="X", usuario="André",
            )


def test_apagar_desfaz_o_lancamento(engine):
    cat, sub = _categoria_trabalho(engine)
    identificador = repo.lancar_receita_manual(
        engine, competencia="2026-06", valor_centavos=800_000, pessoa="Rô",
        categoria_id=cat, subcategoria_id=sub, descricao="ATENDIMENTOS", usuario="André",
    )
    assert repo.excluir_transacao(engine, identificador)
    with engine.connect() as conn:
        assert analytics.resumo(conn, competencia="2026-06")["receitas"] == 0
        assert repo.receitas_manuais(conn, 2026) == []
