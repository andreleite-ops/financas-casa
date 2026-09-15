"""O CSV da fatura de cartão chama gasto de positivo.

O arquivo que o Nubank exporta é `date,title,amount`, com `amount` positivo na
compra e negativo no pagamento da fatura. Lido ao pé da letra, a fatura inteira
entra como receita: as despesas do cartão somem do quadro do mês e a renda
aparece dobrada. Foi o que aconteceu com a fatura de setembro.

A defesa que existia não pegava o caso. A caixa "o valor vem positivo mesmo
quando é gasto" nascia desmarcada, e o aviso só disparava quando *todas* as
linhas da amostra fossem entrada — a linha de "Pagamento recebido", que vem
negativa em toda fatura, bastava para calar o aviso.
"""

from __future__ import annotations

import pandas as pd

from parsers import tabular

# uma fatura como o Nubank exporta: compras positivas, pagamento negativo
FATURA = pd.DataFrame(
    [
        {"date": "2026-08-16", "title": "Padaria Central", "amount": "32.50"},
        {"date": "2026-08-18", "title": "Posto", "amount": "210.00"},
        {"date": "2026-08-20", "title": "Farmacia", "amount": "88.90"},
        {"date": "2026-09-01", "title": "Supermercado", "amount": "540.10"},
        {"date": "2026-09-05", "title": "Streaming", "amount": "39.90"},
        {"date": "2026-09-07", "title": "Livraria", "amount": "74.00"},
        {"date": "2026-09-10", "title": "Pagamento recebido", "amount": "-985.40"},
    ]
)

# um extrato de conta corrente: saídas negativas, entradas positivas
EXTRATO = pd.DataFrame(
    [
        {"Data": "01/09/2026", "Descrição": "SALARIO", "Valor": "8000,00"},
        {"Data": "03/09/2026", "Descrição": "ALUGUEL", "Valor": "-2500,00"},
        {"Data": "05/09/2026", "Descrição": "MERCADO", "Valor": "-430,00"},
        {"Data": "09/09/2026", "Descrição": "LUZ", "Valor": "-180,00"},
        {"Data": "12/09/2026", "Descrição": "FATURA CARTAO", "Valor": "-985,40"},
    ]
)


def test_fatura_de_cartao_e_reconhecida_como_positivo_igual_gasto():
    mapa = tabular.sugerir_mapeamento(FATURA.columns, FATURA)
    assert tabular.positivo_e_gasto(FATURA, mapa)


def test_o_pagamento_da_fatura_nao_cala_a_deteccao():
    """Era esta linha que desarmava o aviso antigo.

    Com ela na amostra, "todas as linhas são ENTRADA" deixava de ser verdade e
    nada era dito. A proporção continua alta o bastante para acusar.
    """
    mapa = tabular.sugerir_mapeamento(FATURA.columns, FATURA)
    proporcao = tabular.proporcao_positiva(FATURA, mapa)
    assert proporcao is not None
    assert proporcao < 1.0                       # existe uma linha negativa
    assert proporcao >= tabular.PROPORCAO_DE_GASTO


def test_extrato_de_conta_corrente_nao_e_invertido():
    """Onde o negativo já é gasto, mexer no sinal estragaria tudo."""
    mapa = tabular.sugerir_mapeamento(EXTRATO.columns, EXTRATO)
    assert not tabular.positivo_e_gasto(EXTRATO, mapa)


def test_arquivo_que_declara_o_sinal_nao_e_adivinhado():
    """Com coluna de tipo, quem manda é o arquivo — não a estatística."""
    planilha = pd.DataFrame(
        [
            {"Data": "01/09/2026", "Descrição": "SALARIO", "Valor": "8000,00", "Tipo": "REC"},
            {"Data": "03/09/2026", "Descrição": "ALUGUEL", "Valor": "2500,00", "Tipo": "DESP"},
            {"Data": "05/09/2026", "Descrição": "MERCADO", "Valor": "430,00", "Tipo": "DESP"},
        ]
    )
    mapa = tabular.sugerir_mapeamento(planilha.columns, planilha)
    assert mapa["tipo"] == "Tipo"
    assert tabular.proporcao_positiva(planilha, mapa) is None
    assert not tabular.positivo_e_gasto(planilha, mapa)


def test_planilha_so_de_recebimentos_nao_vira_gasto_sozinha():
    """A detecção vale para cartão; a planilha de receitas não passa por ela.

    Aqui todas as linhas são positivas e todas são receita de verdade. Quem
    chama `positivo_e_gasto` só o faz quando a conta escolhida é um cartão —
    esta planilha entra numa conta corrente e nunca chega a ser perguntada.
    """
    recebimentos = pd.DataFrame(
        [
            {"Data": "05/09/2026", "Descrição": "Atendimento", "Valor": "300,00"},
            {"Data": "08/09/2026", "Descrição": "Atendimento", "Valor": "300,00"},
            {"Data": "11/09/2026", "Descrição": "Atendimento", "Valor": "450,00"},
        ]
    )
    mapa = tabular.sugerir_mapeamento(recebimentos.columns, recebimentos)
    # o arquivo é mesmo todo positivo — é por isso que a decisão não pode ser
    # só dele, e sim dele mais o tipo da conta escolhida na tela
    assert tabular.proporcao_positiva(recebimentos, mapa) == 1.0


def test_sem_inverter_a_fatura_inteira_entraria_como_receita():
    """O estrago, medido: é o que se viu no quadro de setembro."""
    mapa = tabular.sugerir_mapeamento(FATURA.columns, FATURA)
    errado, _ = tabular.extrair(FATURA, mapa, competencia="2026-09", inverter_sinal=False)
    entradas = sum(l.valor_centavos for l in errado if l.valor_centavos > 0)
    assert entradas == 98_540                       # a fatura toda virou renda

    certo, _ = tabular.extrair(FATURA, mapa, competencia="2026-09", inverter_sinal=True)
    saidas = [l for l in certo if l.valor_centavos < 0]
    creditos = [l for l in certo if l.valor_centavos > 0]
    assert -sum(l.valor_centavos for l in saidas) == 98_540   # as seis compras
    assert len(saidas) == 6
    # o pagamento da fatura é o único crédito, e é transferência, não renda
    assert [l.descricao for l in creditos] == ["Pagamento recebido"]


def test_competencia_da_fatura_manda_no_mes_mesmo_com_compra_de_agosto():
    """A compra de 16/08 é da fatura de setembro e conta em setembro."""
    mapa = tabular.sugerir_mapeamento(FATURA.columns, FATURA)
    lidos, _ = tabular.extrair(FATURA, mapa, competencia="2026-09", inverter_sinal=True)
    assert {l.competencia for l in lidos} == {"2026-09"}
    assert min(l.data for l in lidos).month == 8
