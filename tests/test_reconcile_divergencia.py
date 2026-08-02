"""Testes de core.reconcile: caminho de divergencia (planilha x extrato com

valor/data diferentes para o mesmo estabelecimento) e as acoes de resolucao.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import db, reconcile, repo
from parsers.base import Lancamento


def _conta_id(engine, nome: str = "Conjunta C/C") -> int:
    with engine.begin() as conn:
        return conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar_one()


def _semear_divergencia(engine, conta_id: int, competencia: str = "2026-07") -> dict:
    """Planilha com um lancamento, extrato com o mesmo estabelecimento mas
    valor e data diferentes (dentro da janela de dias considerada divergencia).
    """
    repo.importar(
        engine, conta_id=conta_id, origem="planilha", competencia=competencia, usuario="ro",
        arquivo="planilha_julho.xlsx", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 7, 15), descricao="SUPERMERCADO XYZ", valor_centavos=-20000,
                       competencia=competencia, origem="planilha"),
        ],
    )
    resumo = repo.importar(
        engine, conta_id=conta_id, origem="extrato", competencia=competencia, usuario="andre",
        arquivo="extrato_julho.csv", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 7, 17), descricao="SUPERMERCADO XYZ", valor_centavos=-21500,
                       competencia=competencia, origem="extrato"),
        ],
    )
    return resumo


def test_criticar_reconhece_divergencia_de_valor_e_data(engine):
    conta_id = _conta_id(engine)
    resumo = _semear_divergencia(engine, conta_id)

    # valores diferentes: nao e duplicata nem conferencia automatica
    assert resumo["conferidos_planilha"] == 0
    assert resumo["duplicados_exatos"] == 0
    assert resumo["duplicados_provaveis"] == 0

    with engine.begin() as conn:
        critica = reconcile.criticar(conn)

    assert critica["sem_conferencia"] is False
    assert len(critica["divergencias"]) == 1

    item = critica["divergencias"][0]
    assert item["planilha"]["descricao"] == "SUPERMERCADO XYZ"
    assert item["planilha"]["valor_centavos"] == -20000
    assert item["extrato"]["descricao"] == "SUPERMERCADO XYZ"
    assert item["extrato"]["valor_centavos"] == -21500
    assert item["diferenca"] == -21500 - (-20000)  # -1500
    assert item["dias"] == 2

    # o par de divergencia nao pode aparecer nas outras listas
    assert critica["faltantes"] == []
    assert critica["so_planilha"] == []


def test_resolver_divergencia_manter_extrato_desativa_linha_da_planilha(engine):
    conta_id = _conta_id(engine)
    _semear_divergencia(engine, conta_id)

    with engine.begin() as conn:
        critica = reconcile.criticar(conn)
    planilha_id = critica["divergencias"][0]["planilha"]["id"]
    extrato_id = critica["divergencias"][0]["extrato"]["id"]

    reconcile.resolver_divergencia(engine, planilha_id=planilha_id, manter="extrato", usuario="andre")

    with engine.begin() as conn:
        linha_planilha = conn.execute(
            sa.select(db.transacoes.c.ativo, db.transacoes.c.observacao)
            .where(db.transacoes.c.id == planilha_id)
        ).fetchone()
        linha_extrato = conn.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == extrato_id)
        ).fetchone()

    assert linha_planilha.ativo is False
    assert "descartado" in linha_planilha.observacao
    assert linha_extrato.ativo is True


def test_resolver_divergencia_manter_planilha_deixa_as_duas_ativas(engine):
    conta_id = _conta_id(engine)
    _semear_divergencia(engine, conta_id)

    with engine.begin() as conn:
        critica = reconcile.criticar(conn)
    planilha_id = critica["divergencias"][0]["planilha"]["id"]
    extrato_id = critica["divergencias"][0]["extrato"]["id"]

    reconcile.resolver_divergencia(engine, planilha_id=planilha_id, manter="planilha", usuario="andre")

    with engine.begin() as conn:
        linha_planilha = conn.execute(
            sa.select(db.transacoes.c.ativo, db.transacoes.c.observacao)
            .where(db.transacoes.c.id == planilha_id)
        ).fetchone()
        linha_extrato = conn.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == extrato_id)
        ).fetchone()

    assert linha_planilha.ativo is True
    assert "mantido" in linha_planilha.observacao
    assert linha_extrato.ativo is True


def test_descartar_da_planilha(engine):
    conta_id = _conta_id(engine)
    competencia = "2026-07"
    repo.importar(
        engine, conta_id=conta_id, origem="planilha", competencia=competencia, usuario="ro",
        arquivo="planilha_julho.xlsx", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 7, 4), descricao="ITEM SO PLANILHA", valor_centavos=-1234,
                       competencia=competencia, origem="planilha"),
        ],
    )
    with engine.begin() as conn:
        planilha_id = conn.execute(
            sa.select(db.transacoes.c.id).where(db.transacoes.c.descricao == "ITEM SO PLANILHA")
        ).scalar_one()

    quantidade = reconcile.descartar_da_planilha(engine, [planilha_id], "andre")
    assert quantidade == 1

    with engine.begin() as conn:
        linha = conn.execute(
            sa.select(db.transacoes.c.ativo, db.transacoes.c.observacao)
            .where(db.transacoes.c.id == planilha_id)
        ).fetchone()
    assert linha.ativo is False
    assert "descartado" in linha.observacao


def test_descartar_da_planilha_lista_vazia_nao_faz_nada(engine):
    assert reconcile.descartar_da_planilha(engine, [], "andre") == 0
