"""Quem sabe se o arquivo é de gastos é a CONTA, não a instituição.

O leitor de arquivo é escolhido pelo banco, porque o formato é dele. Mas ser
cartão é propriedade da conta cadastrada — o Bradesco tem conta corrente e
cartão, e o mesmo leitor servia os dois com `tudo_despesa=False` fixo no
código. Resultado: a fatura do cartão era lida como extrato, as compras ficavam
positivas, e o mês inteiro caía do lado errado por mais que a conta estivesse
cadastrada como cartão.

Foi assim que a fatura de setembro passou pela terceira vez. Nas duas
anteriores o buraco também era este, de outra forma: a proteção olhava para
algum lugar que não era o arquivo que estava entrando.
"""

from __future__ import annotations

import pandas as pd

from parsers import instituicoes, tabular

# uma fatura como um banco genérico exporta: gasto positivo, sem coluna de sinal
FATURA = (
    "Data;Descricao;Valor\n"
    "01/09/2026;SUPERMERCADO;540,10\n"
    "05/09/2026;POSTO;210,00\n"
    "07/09/2026;DROGARIA SAO PAULO;42,40\n"
    "09/09/2026;DL*UBERRIDES;15,00\n"
    "10/09/2026;PAGAMENTO FATURA;-807,50\n"
).encode()


def _por_descricao(lancamentos) -> dict[str, int]:
    return {lan.descricao: lan.valor_centavos for lan in lancamentos}


def test_conta_de_cartao_le_a_fatura_como_gasto():
    lidos = instituicoes.ler_arquivo(
        "generico", FATURA, "fatura.csv", competencia="2026-09", tipo_conta="cartao",
    )
    valores = _por_descricao(lidos)
    assert valores["SUPERMERCADO"] == -54_010, "compra tem de sair negativa"
    assert valores["DL*UBERRIDES"] == -1_500
    assert valores["PAGAMENTO FATURA"] == 80_750, "o pagamento da fatura é o crédito"


def test_a_mesma_fatura_numa_conta_corrente_nao_e_invertida():
    """O tipo da conta manda, e manda nos dois sentidos."""
    lidos = instituicoes.ler_arquivo(
        "generico", FATURA, "extrato.csv", competencia="2026-09", tipo_conta="corrente",
    )
    assert _por_descricao(lidos)["SUPERMERCADO"] == 54_010


def test_bradesco_cartao_nao_herda_o_padrao_da_conta_corrente():
    """O caso concreto: o banco tem os dois produtos, o leitor é um só."""
    como_cartao = instituicoes.ler_arquivo(
        "bradesco", FATURA, "fatura.csv", competencia="2026-09", tipo_conta="cartao",
    )
    como_corrente = instituicoes.ler_arquivo(
        "bradesco", FATURA, "extrato.csv", competencia="2026-09", tipo_conta="corrente",
    )
    assert _por_descricao(como_cartao)["POSTO"] == -21_000
    assert _por_descricao(como_corrente)["POSTO"] == 21_000


def test_coluna_tipo_sem_conteudo_de_sinal_nao_decide_nada():
    """"à vista"/"parcelado" tem o nome certo e não diz sinal nenhum.

    Mapeada pelo nome, essa coluna fazia o sistema concluir que o arquivo já
    declarava o lado de cada linha — e isso desligava a inversão da fatura.
    """
    fatura = pd.DataFrame([
        {"Data": "01/09/2026", "Descricao": "SUPERMERCADO", "Valor": "540,10",
         "Tipo": "à vista"},
        {"Data": "05/09/2026", "Descricao": "DECO SKIN", "Valor": "84,95",
         "Tipo": "parcelado 1/2"},
        {"Data": "07/09/2026", "Descricao": "DROGARIA", "Valor": "42,40",
         "Tipo": "à vista"},
    ])
    mapa = tabular.sugerir_mapeamento(fatura.columns, fatura)
    assert mapa["tipo"] is None, "a coluna não confirma o sinal pelo conteúdo"
    # e sem ela no caminho, a fatura volta a ser reconhecida pelo que é
    assert tabular.positivo_e_gasto(fatura, mapa)


def test_coluna_tipo_que_diz_mesmo_o_sinal_continua_valendo():
    """A guarda é sobre conteúdo, não sobre o nome: D/C de verdade manda."""
    planilha = pd.DataFrame([
        {"Data": "01/09/2026", "Descricao": "SALARIO", "Valor": "8000,00", "Tipo": "C"},
        {"Data": "03/09/2026", "Descricao": "ALUGUEL", "Valor": "2500,00", "Tipo": "D"},
        {"Data": "05/09/2026", "Descricao": "MERCADO", "Valor": "430,00", "Tipo": "D"},
    ])
    mapa = tabular.sugerir_mapeamento(planilha.columns, planilha)
    assert mapa["tipo"] == "Tipo"
    lidos, _ = tabular.extrair(planilha, mapa, competencia="2026-09")
    valores = _por_descricao(lidos)
    assert valores["SALARIO"] == 800_000
    assert valores["ALUGUEL"] == -250_000
