"""Diagnóstico de PDF: o que o leitor entendeu, antes de gravar qualquer coisa.

Um leitor que devolve zero lançamentos e mais nada não deixa ninguém avançar —
não dá para saber se o PDF é imagem, se a senha está errada, se o layout é novo
ou se o banco escreve a data de outro jeito. Estes testes garantem que a tela
consegue dizer qual dos quatro é.
"""

from __future__ import annotations

import io

import pytest

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from parsers import pdf
from parsers.base import ErroDeLeitura

FATURA = [
    "FATURA CARTAO — VENCIMENTO 10/09/2026",
    "LIMITE DISPONIVEL 12.000,00",
    "05/08 SUPERMERCADO PAO DE ACUCAR 432,10",
    "07/08 UBER *TRIP SAO PAULO 28,90",
    "12/08 NETFLIX.COM 55,90",
    "18/08 ESTORNO COMPRA LOJA X 120,00",
    "TOTAL DA FATURA 3.210,45",
]

# layout que o leitor ainda não conhece: a data vem com ponto e o valor
# antes da descrição
LAYOUT_DESCONHECIDO = [
    "EXTRATO CONTA CORRENTE",
    "05.08.2026 432,10 D SUPERMERCADO",
    "07.08.2026 1.200,00 C TRANSFERENCIA RECEBIDA",
]


def _pdf(linhas: list[str], senha: str | None = None) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    if senha:
        c.setEncrypt(__import__("reportlab.lib.pdfencrypt", fromlist=["StandardEncryption"])
                     .StandardEncryption(senha, canPrint=1))
    y = 800
    for linha in linhas:
        c.drawString(40, y, linha)
        y -= 18
    c.save()
    return buffer.getvalue()


def test_conta_o_que_reconheceu_e_o_que_descartou_de_proposito():
    diag = pdf.diagnosticar(_pdf(FATURA), competencia="2026-08", tudo_despesa=True)

    descricoes = [lan.descricao for lan in diag["lancamentos"]]
    assert "SUPERMERCADO PAO DE ACUCAR" in descricoes
    assert "NETFLIX.COM" in descricoes
    # estorno abate a fatura: entra positivo
    estorno = next(l for l in diag["lancamentos"] if "ESTORNO" in l.descricao)
    assert estorno.valor_centavos > 0
    # total e limite não viram lançamento: não têm data, nem chegam a ser
    # candidatos — mas continuam visíveis no texto cru, para conferência
    assert not any("TOTAL" in d or "LIMITE" in d for d in descricoes)
    assert any("TOTAL DA FATURA" in linha for linha in diag["amostra"])
    assert diag["linhas_no_pdf"] == len(FATURA)
    assert len(diag["lancamentos"]) == 4


def test_aponta_as_linhas_que_pareciam_lancamento_e_ficaram_de_fora():
    """São elas que dizem o que falta ensinar ao leitor sobre um banco novo."""
    diag = pdf.diagnosticar(_pdf(LAYOUT_DESCONHECIDO), competencia="2026-08")

    assert diag["lancamentos"] == []
    # o diagnóstico não fica mudo: mostra o texto cru para a linha ser lida
    assert any("SUPERMERCADO" in linha for linha in diag["amostra"])
    assert any("TRANSFERENCIA RECEBIDA" in linha for linha in diag["amostra"])


def test_pdf_com_senha_abre_com_a_senha_certa_e_avisa_sem_ela():
    protegido = _pdf(FATURA, senha="1234")

    with pytest.raises(ErroDeLeitura):
        pdf.diagnosticar(protegido)

    diag = pdf.diagnosticar(protegido, senha="1234", competencia="2026-08", tudo_despesa=True)
    assert len(diag["lancamentos"]) >= 3


def test_pdf_sem_texto_diz_que_e_digitalizado():
    vazio = _pdf([])
    with pytest.raises(ErroDeLeitura, match="digitalizado"):
        pdf.diagnosticar(vazio)
