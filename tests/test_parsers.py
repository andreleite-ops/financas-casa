"""Testes dos leitores de extrato (parsers.base, parsers.tabular, parsers.instituicoes)."""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest

from parsers import instituicoes, tabular
from parsers.base import ErroDeLeitura, Lancamento, ajustar_ano_fatura, ler_data


def _csv_bytes(texto: str) -> bytes:
    return texto.encode("utf-8")


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


# --------------------------------------------------------------------------
# sugerir_mapeamento
# --------------------------------------------------------------------------
def test_sugerir_mapeamento_cabecalhos_portugues():
    mapa = tabular.sugerir_mapeamento(["Data", "Histórico", "Valor"])
    assert mapa["data"] == "Data"
    assert mapa["descricao"] == "Histórico"
    assert mapa["valor"] == "Valor"


def test_sugerir_mapeamento_via_csv_em_memoria():
    conteudo = _csv_bytes(
        "Data;Histórico;Valor\n"
        "01/05/2026;PIX RECEBIDO;150,00\n"
        "02/05/2026;PADARIA;-20,00\n"
    )
    df = tabular.carregar_tabela(conteudo, "extrato.csv")
    mapa = tabular.sugerir_mapeamento(df.columns)
    assert mapa["data"] == "Data"
    assert mapa["descricao"] == "Histórico"
    assert mapa["valor"] == "Valor"

    lancamentos, avisos = tabular.extrair(df, mapa, competencia="2026-05")
    assert len(lancamentos) == 2
    assert lancamentos[0].valor_centavos == 15000
    assert lancamentos[1].valor_centavos == -2000


def test_sugerir_mapeamento_via_xlsx_em_memoria():
    df_original = pd.DataFrame(
        {
            "Data": ["01/05/2026", "02/05/2026"],
            "Histórico": ["SALARIO", "MERCADO"],
            "Valor": ["5.000,00", "-320,50"],
        }
    )
    conteudo = _xlsx_bytes(df_original)
    df = tabular.carregar_tabela(conteudo, "extrato.xlsx")
    mapa = tabular.sugerir_mapeamento(df.columns)
    assert mapa["data"] == "Data"
    assert mapa["valor"] == "Valor"

    lancamentos, _ = tabular.extrair(df, mapa, competencia="2026-05")
    assert lancamentos[0].valor_centavos == 500000
    assert lancamentos[1].valor_centavos == -32050


def test_colunas_entrada_e_saida_debito_vira_negativo():
    conteudo = _csv_bytes(
        "Data;Historico;Entrada;Saida\n"
        "01/05/2026;SALARIO;5000,00;\n"
        "02/05/2026;SUPERMERCADO;;320,50\n"
    )
    df = tabular.carregar_tabela(conteudo, "extrato.csv")
    mapa = tabular.sugerir_mapeamento(df.columns)
    assert mapa["entrada"] == "Entrada"
    assert mapa["saida"] == "Saida"

    lancamentos, _ = tabular.extrair(df, mapa, competencia="2026-05")
    assert len(lancamentos) == 2
    assert lancamentos[0].valor_centavos == 500000
    assert lancamentos[1].valor_centavos == -32050  # debito vira negativo


def test_achar_cabecalho_com_lixo_antes():
    conteudo = _csv_bytes(
        "Extrato de conta;;;\n"
        "Cliente: Fulano de Tal;;;\n"
        ";;;\n"
        "Data;Histórico;Valor;Saldo\n"
        "01/05/2026;PIX RECEBIDO;150,00;500,00\n"
        "02/05/2026;PADARIA;-20,00;480,00\n"
    )
    df = tabular.carregar_tabela(conteudo, "extrato_sujo.csv")
    assert list(df.columns)[:3] == ["Data", "Histórico", "Valor"]
    assert len(df) == 2

    mapa = tabular.sugerir_mapeamento(df.columns)
    lancamentos, _ = tabular.extrair(df, mapa, competencia="2026-05")
    assert len(lancamentos) == 2
    assert lancamentos[0].descricao == "PIX RECEBIDO"
    assert lancamentos[0].valor_centavos == 15000
    assert lancamentos[1].valor_centavos == -2000


# --------------------------------------------------------------------------
# fatura Nubank: amount positivo = gasto -> deve virar negativo
# --------------------------------------------------------------------------
def test_fatura_nubank_valor_positivo_vira_gasto_negativo():
    conteudo = _csv_bytes(
        "date,title,amount\n"
        "2026-07-05,IFOOD SAO PAULO,45.90\n"
        "2026-07-06,ESTORNO PARCIAL,-10.00\n"
    )
    lancamentos = instituicoes.nubank(conteudo, "fatura-nubank-julho.csv", competencia="2026-07")
    assert len(lancamentos) == 2
    assert lancamentos[0].descricao == "IFOOD SAO PAULO"
    assert lancamentos[0].valor_centavos == -4590   # gasto normal: negativo
    assert lancamentos[1].valor_centavos == 1000    # estorno na fatura: vira positivo


# --------------------------------------------------------------------------
# ler_data
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texto, ano_referencia, esperado",
    [
        ("12/07", 2026, date(2026, 7, 12)),
        ("12/07/2026", None, date(2026, 7, 12)),
        ("2026-07-12", None, date(2026, 7, 12)),
        ("12 JUL", 2026, date(2026, 7, 12)),
    ],
)
def test_ler_data_formatos(texto, ano_referencia, esperado):
    assert ler_data(texto, ano_referencia=ano_referencia) == esperado


def test_ler_data_invalida():
    with pytest.raises(ErroDeLeitura):
        ler_data("")


# --------------------------------------------------------------------------
# ajustar_ano_fatura
# --------------------------------------------------------------------------
def test_ajustar_ano_fatura_compra_dezembro_fatura_janeiro():
    compra_28_dez = ler_data("28/12", ano_referencia=2026)  # vira 2026-12-28
    lan = Lancamento(data=compra_28_dez, descricao="COMPRA FIM DE ANO", valor_centavos=-10000)
    ajustar_ano_fatura([lan], "2026-01")
    assert lan.data == date(2025, 12, 28)
    assert lan.competencia == "2026-01"


def test_ajustar_ano_fatura_nao_mexe_em_compra_normal():
    compra = ler_data("15/06", ano_referencia=2026)
    lan = Lancamento(data=compra, descricao="COMPRA NORMAL", valor_centavos=-5000)
    ajustar_ano_fatura([lan], "2026-06")
    assert lan.data == date(2026, 6, 15)
