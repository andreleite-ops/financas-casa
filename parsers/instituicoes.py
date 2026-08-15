"""Leitores por instituicao.

Cada leitor e uma camada fina sobre `tabular` (CSV/XLSX) ou `pdf`, com os
ajustes conhecidos de cada banco. Os detalhes marcados como CALIBRAR serao
confirmados quando chegarem os arquivos reais do Andre e da Ro - ate la o
caminho generico ja da conta de ler os arquivos.
"""

from __future__ import annotations

from .base import Lancamento, ajustar_ano_fatura
from . import pdf as leitor_pdf
from . import extrato_itau as leitor_itau, fatura_nubank as leitor_nubank, tabular

# Nubank exporta CSV com colunas fixas: date, title, amount (fatura) ou
# Data, Valor, Identificador, Descricao (conta). Valor da fatura vem positivo.
MAPA_NUBANK_FATURA = {"data": "date", "descricao": "title", "valor": "amount"}
MAPA_NUBANK_CONTA = {"data": "Data", "descricao": "Descrição", "valor": "Valor"}


def _e_planilha(nome: str) -> bool:
    return nome.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm", ".xls"))


def _ler_tabular_ou_pdf(conteudo: bytes, nome: str, *, tudo_despesa: bool, **kw) -> list[Lancamento]:
    if _e_planilha(nome):
        # senha só existe para PDF; passar adiante quebraria o leitor tabular
        kw.pop("senha", None)
        df = tabular.carregar_tabela(conteudo, nome)
        mapa = kw.pop("mapa", None) or tabular.sugerir_mapeamento(df.columns, df)
        # "Tipo" no cabeçalho não quer dizer D/C: na fatura do cartão essa
        # coluna costuma trazer "à vista"/"parcelado"/"Forma de pagamento".
        # Confirmando pelo conteúdo — e apagando o papel quando o conteúdo não
        # confirma — a coluna deixa de mandar no sinal e deixa de desligar a
        # inversão da fatura, que era o caminho para um mês inteiro de compras
        # entrar como receita.
        col_tipo = mapa.get("tipo")
        if col_tipo is not None and (
            col_tipo not in df.columns
            or not tabular.coluna_diz_o_sinal(df[col_tipo])
        ):
            mapa = {**mapa, "tipo": None}
        # fatura de cartão exporta tudo positivo: o valor é o que se gastou, e
        # quem vem negativo é estorno ou o pagamento da própria fatura. Sem
        # inverter, um mês inteiro de compras entrava como receita. Só que a
        # inversão vale apenas quando o arquivo não diz o sinal por conta
        # própria — havendo coluna de tipo (D/C) ou colunas separadas de
        # entrada e saída, quem manda é o arquivo.
        arquivo_diz_o_sinal = bool(
            mapa.get("tipo") or mapa.get("entrada") or mapa.get("saida")
        )
        kw.setdefault("inverter_sinal", tudo_despesa and not arquivo_diz_o_sinal)
        return tabular.extrair(df, mapa, **kw)[0]
    kw.pop("inverter_sinal", None)
    return leitor_pdf.ler(conteudo, nome, tudo_despesa=tudo_despesa, **kw)


def nubank(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Cartao e conta do Nubank. No CSV da fatura o gasto vem positivo."""
    if _e_planilha(nome):
        kw.pop("senha", None)
        df = tabular.carregar_tabela(conteudo, nome)
        colunas = set(df.columns)
        if {"date", "title", "amount"} <= colunas:
            # fatura: amount positivo = gasto
            return tabular.extrair(df, MAPA_NUBANK_FATURA, inverter_sinal=True, **kw)[0]
        mapa = tabular.sugerir_mapeamento(df.columns)
        return tabular.extrair(df, mapa, **kw)[0]
    # a fatura em PDF tem leitor próprio: o Nubank imprime o sinal de menos com
    # o caractere matemático, divide as compras por portador e põe o final do
    # cartão em cada linha — nada disso o leitor genérico enxerga
    kw.pop("inverter_sinal", None)
    return leitor_nubank.ler(conteudo, nome, **kw)


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
    """Extrato de conta corrente Itaú, incluindo a Conjunta.

    O PDF do Itaú tem leitor próprio: a data só aparece na primeira linha de
    cada dia, o sinal é um traço no fim do número e há saldo corrido na mesma
    linha do valor. O leitor genérico não daria conta de nenhuma das três.
    """
    if _e_planilha(nome):
        return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=False, **kw)
    kw.pop("inverter_sinal", None)
    return leitor_itau.ler(conteudo, nome, **kw)


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
