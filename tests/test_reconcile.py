"""Testes de core.reconcile: conferencia planilha x extrato."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import db, reconcile, repo
from parsers.base import Lancamento


def _conta_id(engine, nome: str = "Conjunta C/C") -> int:
    with engine.begin() as conn:
        return conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar_one()


def test_criticar_confere_falta_e_so_planilha(engine):
    conta_id = _conta_id(engine)
    competencia = "2026-05"

    # planilha da Ro: carga inicial do periodo
    repo.importar(
        engine, conta_id=conta_id, origem="planilha", competencia=competencia, usuario="ro",
        arquivo="planilha_maio.xlsx", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 5, 3), descricao="ALUGUEL APTO", valor_centavos=-250000,
                       competencia=competencia, origem="planilha"),
            Lancamento(data=date(2026, 5, 5), descricao="ASSINATURA REVISTA XPTO", valor_centavos=-3000,
                       competencia=competencia, origem="planilha"),
        ],
    )

    # extrato do banco do mesmo periodo: um lancamento igual, um novo
    resumo = repo.importar(
        engine, conta_id=conta_id, origem="extrato", competencia=competencia, usuario="andre",
        arquivo="extrato_maio.csv", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 5, 3), descricao="ALUGUEL APTO", valor_centavos=-250000,
                       competencia=competencia, origem="extrato"),
            Lancamento(data=date(2026, 5, 10), descricao="LIVRARIA CULTURA", valor_centavos=-8000,
                       competencia=competencia, origem="extrato"),
        ],
    )
    # o lancamento igual vira conferencia, nao duplicata
    assert resumo["conferidos_planilha"] == 1
    assert resumo["duplicados_exatos"] == 0
    assert resumo["duplicados_provaveis"] == 0

    with engine.begin() as conn:
        critica = reconcile.criticar(conn)

    assert critica["sem_conferencia"] is False
    assert critica["periodos"] == 1
    assert critica["conferidos"] == 1

    faltantes_desc = {item["descricao"] for item in critica["faltantes"]}
    assert faltantes_desc == {"LIVRARIA CULTURA"}

    so_planilha_desc = {item["descricao"] for item in critica["so_planilha"]}
    assert so_planilha_desc == {"ASSINATURA REVISTA XPTO"}

    assert critica["divergencias"] == []

    # a linha da planilha que bateu com o extrato fica inativa
    with engine.begin() as conn:
        linha_planilha = conn.execute(
            sa.select(db.transacoes.c.ativo, db.transacoes.c.observacao)
            .where(
                db.transacoes.c.conta_id == conta_id,
                db.transacoes.c.origem == "planilha",
                db.transacoes.c.descricao == "ALUGUEL APTO",
            )
        ).fetchone()
        linha_extrato = conn.execute(
            sa.select(db.transacoes.c.ativo, db.transacoes.c.observacao)
            .where(
                db.transacoes.c.conta_id == conta_id,
                db.transacoes.c.origem == "extrato",
                db.transacoes.c.descricao == "ALUGUEL APTO",
            )
        ).fetchone()
    assert linha_planilha.ativo is False
    assert linha_extrato.ativo is True
    assert linha_extrato.observacao == "conferido com a planilha"


def test_criticar_sem_conferencia_quando_falta_uma_origem(engine):
    conta_id = _conta_id(engine)
    # so extrato, nenhuma planilha no periodo: nada para conferir ainda
    repo.importar(
        engine, conta_id=conta_id, origem="extrato", competencia="2026-06", usuario="andre",
        arquivo="extrato_junho.csv", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 6, 2), descricao="FARMACIA SAO JOAO", valor_centavos=-4500,
                       competencia="2026-06", origem="extrato"),
        ],
    )
    with engine.begin() as conn:
        critica = reconcile.criticar(conn)
    assert critica["sem_conferencia"] is True
    assert critica["periodos"] == 0
    assert critica["faltantes"] == []
    assert critica["so_planilha"] == []
