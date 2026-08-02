"""Leitores por instituicao.

Cada leitor e uma camada fina sobre `tabular` (CSV/XLSX) ou `pdf`, com os
ajustes conhecidos de cada banco. Os detalhes marcados como CALIBRAR serao
confirmados quando chegarem os arquivos reais do Andre e da Ro - ate la o
caminho generico ja da conta de ler os arquivos.
"""

from __future__ import annotations

from .base import Lancamento, ajustar_ano_fatura
from . import pdf as leitor_pdf
from . import tabular

# Nubank exporta CSV com colunas fixas: date, title, amount (fatura) ou
# Data, Valor, Identificador, Descricao (conta). Valor da fatura vem positivo.
MAPA_NUBANK_FATURA = {"data": "date", "descricao": "title", "valor": "amount"}
MAPA_NUBANK_CONTA = {"data": "Data", "descricao": "Descrição", "valor": "Valor"}


def _e_planilha(nome: str) -> bool:
    return nome.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm", ".xls"))


def _ler_tabular_ou_pdf(conteudo: bytes, nome: str, *, tudo_despesa: bool, **kw) -> list[Lancamento]:
    if _e_planilha(nome):
        return tabular.ler(conteudo, nome, **kw)
    kw.pop("inverter_sinal", None)
    return leitor_pdf.ler(conteudo, nome, tudo_despesa=tudo_despesa, **kw)


def nubank(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Cartao e conta do Nubank. No CSV da fatura o gasto vem positivo."""
    if _e_planilha(nome):
        df = tabular.carregar_tabela(conteudo, nome)
        colunas = set(df.columns)
        if {"date", "title", "amount"} <= colunas:
            # fatura: amount positivo = gasto
            return tabular.extrair(df, MAPA_NUBANK_FATURA, inverter_sinal=True, **kw)[0]
        mapa = tabular.sugerir_mapeamento(df.columns)
        return tabular.extrair(df, mapa, **kw)[0]
    return leitor_pdf.ler(conteudo, nome, tudo_despesa=True, **kw)


def xp(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Fatura do cartao Visa XP. CALIBRAR com amostra real."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=True, **kw)


def btg(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Fatura do BTG Mastercard. CALIBRAR com amostra real."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=True, **kw)


def bradesco(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Extrato de conta corrente Bradesco: tem credito e debito. CALIBRAR."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=False, **kw)


def itau(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Extrato de conta corrente Itau (inclui a Conjunta). CALIBRAR."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=False, **kw)


def generico(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Qualquer instituicao nova, ate ganhar leitor proprio."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=False, **kw)


LEITORES = {
    "nubank": nubank,
    "xp": xp,
    "btg": btg,
    "bradesco": bradesco,
    "itau": itau,
    "generico": generico,
}

ROTULOS = {
    "nubank": "Nubank (cartão e conta)",
    "xp": "XP / Visa XP",
    "btg": "BTG",
    "bradesco": "Bradesco",
    "itau": "Itaú (inclui Conjunta)",
    "generico": "Genérico — CSV/XLSX com mapeamento de colunas",
}


def ler_arquivo(
    parser: str,
    conteudo: bytes,
    nome_arquivo: str,
    *,
    competencia: str | None = None,
    tipo_conta: str = "corrente",
    **kw,
) -> list[Lancamento]:
    """Ponto de entrada unico do upload."""
    leitor = LEITORES.get(parser or "generico", generico)
    lancamentos = leitor(conteudo, nome_arquivo, competencia=competencia, **kw)
    if tipo_conta == "cartao" and competencia:
        lancamentos = ajustar_ano_fatura(lancamentos, competencia)
    elif competencia:
        for lan in lancamentos:
            lan.competencia = lan.data.strftime("%Y-%m")
    return lancamentos
