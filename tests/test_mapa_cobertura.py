"""O mapa de carregamento não pode confundir planilha com extrato.

A planilha é importada dentro de alguma conta. Sem separar por origem, os
lançamentos dela apareceriam como se o extrato daquela conta já tivesse sido
carregado — exatamente o oposto do que o painel serve para dizer.
"""

from __future__ import annotations

from datetime import date

from core import repo
from parsers.base import Lancamento


def _lote(dia_inicial: int, quantos: int, origem: str = "extrato"):
    return [
        Lancamento(data=date(2026, 5, dia_inicial + i), descricao=f"GASTO {origem} {i}",
                   valor_centavos=-(1000 + i * 137), origem=origem)
        for i in range(quantos)
    ]


def test_planilha_nao_conta_como_extrato_da_conta(engine):
    repo.importar(engine, conta_id=4, lancamentos=_lote(1, 5, "planilha"),
                  arquivo="planilha.xlsx", usuario="Rô", origem="planilha",
                  competencia="2026-05", usar_ia=False)

    with engine.connect() as conn:
        contas = repo.cobertura(conn, ["2026-05"])
        planilha = repo.cobertura_planilha(conn, ["2026-05"])

    # a conta que recebeu a planilha continua sem extrato carregado
    assert (4, "2026-05") not in contas
    # e a linha da planilha registra os 5
    assert planilha["2026-05"]["total"] == 5


def test_extrato_da_mesma_conta_aparece_normalmente(engine):
    repo.importar(engine, conta_id=4, lancamentos=_lote(1, 5, "planilha"),
                  arquivo="planilha.xlsx", usuario="Rô", origem="planilha",
                  competencia="2026-05", usar_ia=False)
    repo.importar(engine, conta_id=4, lancamentos=_lote(20, 3, "extrato"),
                  arquivo="extrato.pdf", usuario="André", origem="extrato",
                  competencia="2026-05", usar_ia=False)

    with engine.connect() as conn:
        contas = repo.cobertura(conn, ["2026-05"])

    # só os 3 do extrato, sem os 5 da planilha somados junto
    assert contas[(4, "2026-05")]["ativos"] == 3
