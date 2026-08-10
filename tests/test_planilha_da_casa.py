"""A planilha da casa, do jeito que ela é, contra a tabela dinâmica dela mesma.

O acordo com o André é curto: depois do upload, o total de despesas e o total
de receitas têm de bater com o que a tabela dinâmica da planilha mostra. Este
arquivo reproduz, em miniatura, tudo que fez o número não bater — a aba de
resumo vindo primeiro, o mês de competência diferente do mês da data, o estorno
com sinal negativo dentro de DESP e o valor digitado com uma letra no meio — e
trava o resultado.
"""

from __future__ import annotations

import io

import pandas as pd

from core import analytics, repo
from parsers import tabular

# ---------------------------------------------------------------------------
# a miniatura da planilha
# ---------------------------------------------------------------------------
# DESP:  1.000,00 + 250,00 - 100,00 (estorno) + 40,00  = 1.190,00
# REC:  20.000,00 + 5.000,00 - 500,00 (ajuste)         = 24.500,00
# A linha "Z195,82" não entra em lugar nenhum: é erro de digitação.
LANCAMENTOS = [
    # DATA          MÊS/ANO     CATEGORIA  BENEFICIÁRIO   VALOR        CLASSIFICAÇÃO
    ("05/01/2026", "jan/26", "DESP", "PADARIA",      "1.000,00", "ALIMENTAÇÃO"),
    ("28/01/2026", "jan/26", "DESP", "FARMACIA",       "250,00", "SAÚDE"),
    ("30/01/2026", "jan/26", "DESP", "ESTORNO FARM",  "-100,00", "SAÚDE"),
    # lançada em fevereiro, mas a Rô contou em janeiro: a competência manda
    ("02/02/2026", "jan/26", "DESP", "MERCADO",         "40,00", "ALIMENTAÇÃO"),
    ("31/01/2026", "jan/26", "DESP", "SEM PARAR",     "Z195,82", "TAG"),
    ("05/01/2026", "jan/26", "REC",  "SALARIO",     "20.000,00", "TAG"),
    ("05/01/2026", "jan/26", "REC",  "ATENDIMENTOS", "5.000,00", "BIOS"),
    ("10/01/2026", "jan/26", "REC",  "NUN",           "-500,00", "ALUGUEL"),
]

DESP_ESPERADA = 119_000    # centavos
REC_ESPERADA = 2_450_000   # centavos


def _pasta_de_trabalho() -> bytes:
    """Duas abas, o resumo primeiro — a ordem real do arquivo da casa."""
    resumo = pd.DataFrame({
        "Rótulos de Linha": ["DESP", "REC", "Total Geral"],
        "Soma de VALOR": ["1.190,00", "24.500,00", "25.690,00"],
    })
    lancamentos = pd.DataFrame(
        LANCAMENTOS,
        columns=["DATA", "MÊS/ANO", "CATEGORIA", "BENEFICIÁRIO", "VALOR", "CLASSIFICAÇÃO"],
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        resumo.to_excel(escritor, sheet_name="Tabela Dinamica", index=False)
        lancamentos.to_excel(escritor, sheet_name="Lançamento Despesas", index=False)
    return buffer.getvalue()


def _ler():
    conteudo = _pasta_de_trabalho()
    df = tabular.carregar_tabela(conteudo, "planilha.xlsx")
    return df, tabular.sugerir_mapeamento(df.columns, df)


# ---------------------------------------------------------------------------
# leitura
# ---------------------------------------------------------------------------
def test_ignora_a_aba_de_resumo_mesmo_vindo_primeiro():
    assert tabular.aba_de_resumo("Tabela Dinamica")
    assert tabular.aba_de_resumo("Resumo 2026")
    assert not tabular.aba_de_resumo("Lançamento Despesas")

    conteudo = _pasta_de_trabalho()
    abas = tabular.listar_abas(conteudo)
    assert abas[0] == "Tabela Dinamica"          # o resumo é mesmo o primeiro
    assert tabular._aba_com_lancamentos(conteudo, abas) == "Lançamento Despesas"


def test_reconhece_as_colunas_da_planilha_da_casa():
    _, mapa = _ler()
    assert mapa["data"] == "DATA"
    assert mapa["competencia"] == "MÊS/ANO"
    assert mapa["tipo"] == "CATEGORIA"
    assert mapa["descricao"] == "BENEFICIÁRIO"
    assert mapa["valor"] == "VALOR"
    assert mapa["categoria"] == "CLASSIFICAÇÃO"


def test_aponta_o_valor_com_letra_em_vez_de_adivinhar():
    df, mapa = _ler()
    lancamentos, avisos = tabular.extrair(df, mapa, origem="planilha")

    assert not any("SEM PARAR" == lan.descricao for lan in lancamentos)
    assert any("Z195,82" in aviso for aviso in avisos)


def test_estorno_dentro_de_desp_continua_sendo_despesa():
    df, mapa = _ler()
    lancamentos, _ = tabular.extrair(df, mapa, origem="planilha")
    estorno = next(lan for lan in lancamentos if lan.descricao == "ESTORNO FARM")

    # o crédito abate o gasto: entra com sinal de entrada, natureza de despesa
    assert estorno.valor_centavos == 10_000
    assert estorno.natureza_hint == "despesa"


def test_competencia_manda_no_mes_do_relatorio():
    df, mapa = _ler()
    lancamentos, _ = tabular.extrair(df, mapa, origem="planilha")
    mercado = next(lan for lan in lancamentos if lan.descricao == "MERCADO")

    assert mercado.data.month == 2       # o dinheiro andou em fevereiro
    assert mercado.competencia == "2026-01"   # a Rô contou em janeiro


# ---------------------------------------------------------------------------
# o acordo: depois do upload, os totais batem com a dinâmica
# ---------------------------------------------------------------------------
def test_totais_batem_com_a_tabela_dinamica(engine):
    df, mapa = _ler()
    lancamentos, _ = tabular.extrair(df, mapa, origem="planilha")
    conta_id = repo.conta_da_planilha(engine)
    repo.importar(
        engine, lancamentos=lancamentos, conta_id=conta_id, arquivo="planilha.xlsx",
        origem="planilha", usuario="andre", pessoa_padrao="Rô", usar_ia=False,
    )

    with engine.connect() as conn:
        resumo = analytics.resumo(conn, ano=2026)
        serie = analytics.serie_mensal(conn, 2026)

    assert resumo["receitas"] == REC_ESPERADA
    assert resumo["despesas"] + resumo["poupanca"] == DESP_ESPERADA

    # tudo caiu em janeiro, inclusive o lançamento datado de fevereiro
    assert [linha["competencia"] for linha in serie] == ["2026-01"]
    assert serie[0]["receitas"] == REC_ESPERADA
    assert serie[0]["despesas"] + serie[0]["poupanca"] == DESP_ESPERADA


def test_a_fonte_de_renda_diz_de_quem_e_o_dinheiro(engine):
    df, mapa = _ler()
    lancamentos, _ = tabular.extrair(df, mapa, origem="planilha")
    conta_id = repo.conta_da_planilha(engine)
    repo.importar(
        engine, lancamentos=lancamentos, conta_id=conta_id, arquivo="planilha.xlsx",
        # tudo entraria como da Rô se ninguém olhasse o rótulo
        origem="planilha", usuario="andre", pessoa_padrao="Rô", usar_ia=False,
    )

    with engine.connect() as conn:
        por_pessoa = {
            linha["pessoa"]: linha["total"]
            for linha in analytics.receitas_por_pessoa(conn, ano=2026)
        }

    assert por_pessoa["André"] == 2_000_000    # TAG
    assert por_pessoa["Rô"] == 500_000         # BIOS
    assert por_pessoa["Casal"] == -50_000      # NUN, o aluguel é dos dois
    assert sum(por_pessoa.values()) == REC_ESPERADA
