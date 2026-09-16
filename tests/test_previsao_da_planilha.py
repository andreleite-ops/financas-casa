"""A planilha da carga inicial também é previsão.

Ela traz o ano inteiro — de janeiro a dezembro — e, dos meses que ainda não
aconteceram, ela é a previsão. Toda a proteção contra renda dobrada olhava só
o que foi lançado à mão: com o primeiro extrato real depois da carga, salário
da planilha + salário do extrato somariam calados, e nem o aviso vermelho da
tela de Receitas dispararia.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from parsers.base import Lancamento


def _conta(engine, nome, tipo="corrente", titular="André"):
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=nome, tipo=tipo, titular=titular, instituicao="Banco",
                parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def _planilha(engine, conta_id, dia, valor, descricao="SALARIO"):
    return repo.importar(
        engine, conta_id=conta_id, arquivo="planilha.xlsx", origem="planilha",
        usuario="André", pessoa_padrao="André", usar_ia=False,
        lancamentos=[Lancamento(data=dia, descricao=descricao, valor_centavos=valor,
                                origem="planilha")],
    )


def _extrato(engine, conta_id, lancamentos, pessoa="André"):
    return repo.importar(
        engine, conta_id=conta_id, arquivo="extrato.csv", origem="extrato",
        usuario="André", pessoa_padrao=pessoa, usar_ia=False, lancamentos=lancamentos,
    )


def _receitas(engine, competencia):
    with engine.connect() as conn:
        return analytics.resumo(conn, competencia=competencia)["receitas"]


def test_salario_da_planilha_com_reajuste_no_extrato_nao_dobra(engine):
    """Agosto: a planilha previu redondo, o extrato trouxe o valor real."""
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    corrente = _conta(engine, "Conta Salário")
    _planilha(engine, planilha, date(2026, 8, 5), 2_000_000)

    resumo = _extrato(engine, corrente, [
        Lancamento(data=date(2026, 8, 6), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_059_621),
    ])
    assert resumo["previsoes_realizadas"] == 1
    assert _receitas(engine, "2026-08") == 2_059_621


def test_valor_exato_continua_sendo_conferencia(engine):
    """O que bate no centavo é conferência, não previsão realizada."""
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    corrente = _conta(engine, "Conta Salário")
    _planilha(engine, planilha, date(2026, 8, 5), 2_059_621)

    resumo = _extrato(engine, corrente, [
        Lancamento(data=date(2026, 8, 6), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_059_621),
    ])
    assert resumo["conferidos_planilha"] == 1
    assert resumo["previsoes_realizadas"] == 0
    assert _receitas(engine, "2026-08") == 2_059_621


def test_recebimento_picado_contra_previsao_da_planilha_vira_pergunta(engine):
    """Os pacientes da Rô contra o total mensal que está na planilha."""
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    conta_ro = _conta(engine, "Conta Rô", titular="Rô")
    _planilha(engine, planilha, date(2026, 8, 28), 480_000, "ATENDIMENTOS")

    resumo = _extrato(engine, conta_ro, [
        Lancamento(data=date(2026, 8, 1 + i), descricao=f"PIX PACIENTE {i}", valor_centavos=30_000)
        for i in range(16)
    ], pessoa="Rô")
    assert resumo["previsoes_realizadas"] == 0
    assert len(resumo["previsoes_a_conferir"]) == 1, "a soma bate: tem de virar pergunta"


def test_tela_de_receitas_conta_a_planilha_como_previsao():
    composicao = [
        {"origem": "planilha", "total": 2_000_000, "no_total": True},
        {"origem": "extrato", "total": 2_059_621, "no_total": True},
        {"origem": "extrato", "total": 500_000, "no_total": False},   # transferência
    ]
    assert analytics.previsto_e_realizado(composicao) == (2_000_000, 2_059_621)

    so_previsao = [{"origem": "planilha", "total": 2_000_000, "no_total": True},
                   {"origem": "manual", "total": 480_000, "no_total": True}]
    assert analytics.previsto_e_realizado(so_previsao) == (2_480_000, 0)


def test_planilha_de_meses_passados_nao_e_tocada_sem_extrato(engine):
    """Janeiro a julho são história, e história sem extrato fica como está."""
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    _planilha(engine, planilha, date(2026, 3, 5), 2_000_000)
    assert _receitas(engine, "2026-03") == 2_000_000
