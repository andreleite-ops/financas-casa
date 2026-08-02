"""Testes de core.analytics: resumo mensal, poupanca separada e estornos."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db
from core.dedup import hash_lancamento
from core.texto import normalizar


def _categoria_id(conn, nome: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _conta_id(conn, nome: str = "Nubank Mastercard") -> int:
    return conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar_one()


def _inserir(conn, conta_id, dia, descricao, valor_centavos, categoria_id):
    descricao_norm = normalizar(descricao)
    conn.execute(
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
            ativo=True,
        )
    )


def _montar_mes(conn, conta_id, ano, mes, *, salario=500000, mercado=150000,
                 estorno_mercado=20000, aporte=100000):
    trabalho_id = _categoria_id(conn, "Trabalho")
    alimentacao_id = _categoria_id(conn, "Alimentação")
    poupanca_id = _categoria_id(conn, analytics.CATEGORIA_POUPANCA)

    _inserir(conn, conta_id, date(ano, mes, 5), "PRO LABORE", salario, trabalho_id)
    _inserir(conn, conta_id, date(ano, mes, 10), "SUPERMERCADO PAO ACUCAR", -mercado, alimentacao_id)
    _inserir(conn, conta_id, date(ano, mes, 12), "ESTORNO SUPERMERCADO", estorno_mercado, alimentacao_id)
    _inserir(conn, conta_id, date(ano, mes, 15), "APLICACAO CDB", -aporte, poupanca_id)
    return {
        "trabalho_id": trabalho_id,
        "alimentacao_id": alimentacao_id,
        "poupanca_id": poupanca_id,
    }


# --------------------------------------------------------------------------
# resumo: poupanca separada + sobra + estorno abate a categoria
# --------------------------------------------------------------------------
def test_resumo_separa_poupanca_e_calcula_sobra(engine, conn):
    conta_id = _conta_id(conn)
    _montar_mes(conn, conta_id, 2026, 5)

    dados = analytics.resumo(conn, competencia="2026-05")
    assert dados["receitas"] == 500_000
    assert dados["despesas"] == 130_000       # 150_000 - 20_000 de estorno
    assert dados["poupanca"] == 100_000
    assert dados["sobra"] == 500_000 - 130_000 - 100_000
    assert dados["sobra"] == 270_000


def test_estorno_abate_categoria_em_vez_de_virar_receita(engine, conn):
    conta_id = _conta_id(conn)
    ids = _montar_mes(conn, conta_id, 2026, 5)

    por_cat = {c["categoria_id"]: c for c in analytics.por_categoria(conn, competencia="2026-05")}
    alimentacao = por_cat[ids["alimentacao_id"]]
    assert alimentacao["total"] == 130_000   # nao 150_000 nem negativo
    assert alimentacao["qtd"] == 2

    # a categoria de receita nao ganhou nada com o estorno
    receitas = analytics.por_categoria(conn, competencia="2026-05", natureza="receita")
    total_receitas = sum(r["total"] for r in receitas)
    assert total_receitas == 500_000


# --------------------------------------------------------------------------
# serie_mensal e tabela_mes_a_mes com 2 meses
# --------------------------------------------------------------------------
def test_serie_mensal_dois_meses(engine, conn):
    conta_id = _conta_id(conn)
    _montar_mes(conn, conta_id, 2026, 5)
    _montar_mes(conn, conta_id, 2026, 6, salario=520000, mercado=140000, estorno_mercado=0, aporte=110000)

    serie = analytics.serie_mensal(conn, 2026)
    competencias = [m["competencia"] for m in serie]
    assert competencias == ["2026-05", "2026-06"]

    maio, junho = serie
    assert maio["receitas"] == 500_000
    assert maio["poupanca"] == 100_000
    assert junho["receitas"] == 520_000
    assert junho["despesas"] == 140_000
    assert junho["poupanca"] == 110_000
    assert junho["sobra"] == 520_000 - 140_000 - 110_000


def test_tabela_mes_a_mes_dois_meses(engine, conn):
    conta_id = _conta_id(conn)
    _montar_mes(conn, conta_id, 2026, 5)
    _montar_mes(conn, conta_id, 2026, 6, salario=520000, mercado=140000, estorno_mercado=0, aporte=110000)

    tabela = analytics.tabela_mes_a_mes(conn, 2026)
    assert tabela["meses"] == ["05", "06"]
    linha_alimentacao = next(l for l in tabela["linhas"] if l["categoria"] == "Alimentação")
    assert linha_alimentacao["meses"]["05"] == 130_000
    assert linha_alimentacao["meses"]["06"] == 140_000
    assert linha_alimentacao["acumulado"] == 270_000


# --------------------------------------------------------------------------
# comparativo_anual com 2 anos
# --------------------------------------------------------------------------
def test_comparativo_anual_dois_anos(engine, conn):
    conta_id = _conta_id(conn)
    _montar_mes(conn, conta_id, 2025, 11)
    _montar_mes(conn, conta_id, 2026, 5)

    comparativo = analytics.comparativo_anual(conn)
    anos = [c["ano"] for c in comparativo]
    assert anos == [2025, 2026]
    for linha in comparativo:
        assert linha["receitas"] == 500_000
        assert linha["poupanca"] == 100_000
