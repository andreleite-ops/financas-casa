"""O que a tela de upload diz sobre a conferência do extrato.

Três respostas, e nenhuma delas é o silêncio: fechou, não fechou (e em que
dia), ou não houve conferência. A terceira é a que faltava — calado parecia
"conferido e certo", e foi num extrato sem total impresso que dez linhas do
quadro de limites entraram sem ninguém ver.
"""

from __future__ import annotations

import dataclasses

from parsers import extrato_itau as it
from views import upload

COM_SALDO_DIARIO = """FULANA DE TAL agência: 0660 conta: 11111-1
extrato conta corrente
período de visualização: mês completo - 01/10/2026 a 31/10/2026
data lançamentos valor (R$) saldo (R$)
30/09/2026 SALDO ANTERIOR 1.000,00
01/10/2026 PIX TRANSF PACIENTE01/10 500,00
01/10/2026 SALDO TOTAL DISPONIVEL DIA 1.500,00
05/10/2026 ALUGUEL -500,00
05/10/2026 SALDO TOTAL DISPONIVEL DIA 1.000,00
"""


def test_quando_fecha_a_tela_nao_tem_nada_a_avisar():
    lancamentos, _ = it.extrair_linhas(COM_SALDO_DIARIO)

    assert upload._conferencia_do_extrato("itau", COM_SALDO_DIARIO, lancamentos) == []


def test_o_aviso_diz_o_dia_que_nao_fecha_e_de_quanto():
    """"Não fecha" sozinho obriga a conferir o mês inteiro linha por linha.
    Com o dia e a diferença, é uma olhada no extrato."""
    lancamentos, _ = it.extrair_linhas(COM_SALDO_DIARIO)
    trocados = [
        dataclasses.replace(lan, valor_centavos=-lan.valor_centavos)
        for lan in lancamentos
    ]

    avisos = upload._conferencia_do_extrato("itau", COM_SALDO_DIARIO, trocados)

    assert len(avisos) == 1
    assert "01/10" in avisos[0]
    assert "1.000,00" in avisos[0]
    # cifrão escapado: um par deles vira fórmula e a tela come o valor
    assert r"R\$" in avisos[0]


def test_sem_regua_a_tela_diz_que_nao_conferiu():
    """O extrato que não imprime total nem saldo que se saiba ler não dá para
    conferir — e é exatamente aí que o aviso importa."""
    texto = "30/09/2026 SALDO ANTERIOR 1.000,00\n01/10/2026 PADARIA -20,00\n"
    lancamentos, _ = it.extrair_linhas(texto)

    avisos = upload._conferencia_do_extrato("itau", texto, lancamentos)

    assert len(avisos) == 1
    assert "não houve conferência" in avisos[0]


def test_arquivo_sem_leitor_de_extrato_nao_gera_aviso_nenhum():
    """Fatura de cartão e planilha não têm saldo para conferir: avisar ali
    seria barulho em toda importação."""
    assert upload._conferencia_do_extrato("xp", "qualquer texto", []) == []
    assert upload._conferencia_do_extrato("itau", None, []) == []
