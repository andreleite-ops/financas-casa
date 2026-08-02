"""Regressão: extrato de conta corrente com coluna de saldo.

Fatura de cartão traz um número por linha (o valor). Extrato de conta corrente
traz dois: o valor e o saldo corrido depois dele. Antes da correção o leitor
pegava o último número da linha — ou seja, o saldo — como se fosse o valor, e
empurrava o valor real para dentro da descrição. Todo lançamento saía errado,
e como a contagem de linhas continuava certa o erro passava despercebido.
"""

from __future__ import annotations

from parsers.pdf import extrair_linhas

EXTRATO_COM_SALDO = """
BRADESCO - EXTRATO CONTA CORRENTE  AG 1234 CC 56789-0
Data     Historico                        Valor      Saldo
05/07    PRO LABORE EMPRESA A         27.000,00 C  31.200,00
07/07    CONDOMINIO EDIFICIO           4.200,00 D  27.000,00
10/07    ENEL DISTRIBUICAO               512,33 D  26.487,67
20/07    TARIFA PACOTE DE SERVICOS        49,00 D  17.438,67
SALDO EM 31/07                                    17.438,67
"""

FATURA_SEM_SALDO = """
03/07  IFOOD *RESTAURANTE SP          86,40
18/07  POSTO IPIRANGA 0442           280,00
28/07  ESTORNO COMPRA CANCELADA      118,90
"""


def test_saldo_nao_e_confundido_com_o_valor():
    lancamentos, _ = extrair_linhas(EXTRATO_COM_SALDO, competencia="2026-07")

    assert len(lancamentos) == 4
    valores = {lan.descricao: lan.valor_centavos for lan in lancamentos}
    assert valores["PRO LABORE EMPRESA A"] == 2_700_000      # marca C = entrada
    assert valores["CONDOMINIO EDIFICIO"] == -420_000        # marca D = saída
    assert valores["ENEL DISTRIBUICAO"] == -51_233
    assert valores["TARIFA PACOTE DE SERVICOS"] == -4_900

    # nenhum valor nem marca D/C pode ter vazado para a descrição
    for lan in lancamentos:
        assert "," not in lan.descricao
        assert not lan.descricao.rstrip().endswith((" C", " D"))


def test_marca_do_banco_manda_mais_que_a_palavra_estorno():
    """Linha marcada D é saída mesmo dizendo ESTORNO; C é entrada mesmo sem."""
    texto = "05/07  ESTORNO INDEVIDO COBRADO  100,00 D  900,00\n"
    lancamentos, _ = extrair_linhas(texto, competencia="2026-07")
    assert lancamentos[0].valor_centavos == -10_000


def test_fatura_de_cartao_continua_lendo_um_numero_por_linha():
    lancamentos, _ = extrair_linhas(FATURA_SEM_SALDO, competencia="2026-07")

    assert [lan.valor_centavos for lan in lancamentos] == [-8_640, -28_000, 11_890]
    assert lancamentos[0].descricao == "IFOOD *RESTAURANTE SP"


def test_linha_de_saldo_nao_vira_lancamento():
    """Duas formas de linha de saldo, cada uma barrada num ponto diferente.

    "SALDO EM 31/07 ..." nem chega a ser avaliada, porque não começa com data.
    "31/07 SALDO ANTERIOR ..." casa o formato de lançamento e é barrada pela
    lista de ruído — essa é a que precisa de guarda explícita.
    """
    lancamentos, _ = extrair_linhas(EXTRATO_COM_SALDO, competencia="2026-07")
    assert all("SALDO" not in lan.descricao for lan in lancamentos)

    lancamentos, ignoradas = extrair_linhas(
        "31/07  SALDO ANTERIOR  1.234,56\n"
        "31/07  TOTAL DA FATURA  3.400,00\n",
        competencia="2026-07",
    )
    assert lancamentos == []
    assert len(ignoradas) == 2
