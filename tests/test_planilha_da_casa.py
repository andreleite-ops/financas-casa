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
from datetime import date

import pandas as pd
import sqlalchemy as sa

from core import analytics, db, repo, seed
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


def test_a_matriz_de_receitas_mostra_a_fonte_de_cada_um(engine):
    """Sem a fonte, o pró-labore do André e o da Rô virariam a mesma linha."""
    df, mapa = _ler()
    lancamentos, _ = tabular.extrair(df, mapa, origem="planilha")
    conta_id = repo.conta_da_planilha(engine)
    repo.importar(
        engine, lancamentos=lancamentos, conta_id=conta_id, arquivo="planilha.xlsx",
        origem="planilha", usuario="andre", pessoa_padrao="Rô", usar_ia=False,
    )

    with engine.connect() as conn:
        matriz = analytics.receitas_por_pessoa_e_tipo(conn, 2026)

    por_fonte = {(l["pessoa"], l["fonte"]): l["total"] for l in matriz["linhas"]}
    assert por_fonte[("André", "TAG")] == 2_000_000
    assert por_fonte[("Rô", "BIOS")] == 500_000
    assert por_fonte[("Casal", "ALUGUEL")] == -50_000
    # nenhuma receita ficou com o dono herdado do titular da conta
    assert not [linha for linha in matriz["linhas"] if linha["fonte"] == "—"]


# ---------------------------------------------------------------------------
# venda de bem: entra no total, fica fora da renda que baliza o orçamento
# ---------------------------------------------------------------------------
VENDA = [
    ("15/03/2026", "mar/26", "REC",  "VENDA APTO MAE", "520.000,00", "VENDA"),
    ("30/04/2026", "abr/26", "DESP", "IR GANHO DE CAPITAL", "31.667,03", "IMPOSTOS"),
]


def _com_a_venda() -> bytes:
    lancamentos = pd.DataFrame(
        LANCAMENTOS + VENDA,
        columns=["DATA", "MÊS/ANO", "CATEGORIA", "BENEFICIÁRIO", "VALOR", "CLASSIFICAÇÃO"],
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        lancamentos.to_excel(escritor, sheet_name="Lançamento Despesas", index=False)
    return buffer.getvalue()


def _importar_com_a_venda(engine):
    conteudo = _com_a_venda()
    df = tabular.carregar_tabela(conteudo, "planilha.xlsx")
    lancamentos, _ = tabular.extrair(
        df, tabular.sugerir_mapeamento(df.columns, df), origem="planilha"
    )
    repo.importar(
        engine, lancamentos=lancamentos, conta_id=repo.conta_da_planilha(engine),
        arquivo="planilha.xlsx", origem="planilha", usuario="andre",
        pessoa_padrao="Rô", usar_ia=False,
    )


def test_venda_de_bem_conta_no_total_mas_nao_vira_renda(engine):
    _importar_com_a_venda(engine)
    with engine.connect() as conn:
        resumo = analytics.resumo(conn, ano=2026)

    # o dinheiro entrou: aparece nas receitas
    assert resumo["receitas"] == REC_ESPERADA + 52_000_000
    # mas não é renda: a base do orçamento ignora a venda
    assert resumo["receitas_nao_recorrentes"] == 52_000_000
    assert resumo["renda_recorrente"] == REC_ESPERADA
    # e o IR da venda é despesa, no mês em que foi recolhido
    assert resumo["despesas"] == DESP_ESPERADA + 3_166_703


def test_a_venda_do_apartamento_e_do_andre(engine):
    _importar_com_a_venda(engine)
    with engine.connect() as conn:
        por_pessoa = {
            linha["pessoa"]: linha["total"]
            for linha in analytics.receitas_por_pessoa(conn, ano=2026)
        }

    assert por_pessoa["André"] == 2_000_000 + 52_000_000


# ---------------------------------------------------------------------------
# data do Excel lida como texto: "2026-01-05 00:00:00"
# ---------------------------------------------------------------------------
def test_data_do_excel_com_hora_nao_troca_dia_por_mes():
    """Sem a coluna MÊS/ANO, errar a data é errar o mês do relatório inteiro."""
    from parsers.base import ler_data

    # 05/01: dia e mês ambos <= 12, o caso em que a inversão passa despercebida
    assert ler_data("2026-01-05 00:00:00") == date(2026, 1, 5)
    assert ler_data("2026-12-05 00:00:00") == date(2026, 12, 5)
    assert ler_data("2026-11-05T00:00:00") == date(2026, 11, 5)
    assert ler_data("2026-01-20 00:00:00") == date(2026, 1, 20)
    # e o formato brasileiro continua sendo dia primeiro
    assert ler_data("05/01/2026") == date(2026, 1, 5)


def test_planilha_sem_coluna_de_competencia_usa_a_data(engine):
    """O arquivo novo tem só DATA: o mês do relatório sai dela."""
    lancamentos = pd.DataFrame(
        [
            ("2026-01-05 00:00:00", "DESP", "PADARIA", "1000", "ALIMENTAÇÃO"),
            ("2026-12-05 00:00:00", "REC", "SALARIO", "20000", "TAG"),
        ],
        columns=["DATA", "CATEGORIA", "BENEFICIÁRIO", "VALOR", "CLASSIFICAÇÃO"],
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        lancamentos.to_excel(escritor, sheet_name="Lançamento Despesas e Receitas", index=False)

    df = tabular.carregar_tabela(buffer.getvalue(), "planilha.xlsx")
    lidos, _ = tabular.extrair(df, tabular.sugerir_mapeamento(df.columns, df), origem="planilha")

    assert {lan.competencia for lan in lidos} == {"2026-01", "2026-12"}


def test_descricao_manda_no_que_foi_o_rotulo_manda_em_de_quem_e(engine):
    """A venda do apto vinha rotulada TAG: o rótulo não pode virá-la em salário."""
    lancamentos = pd.DataFrame(
        [
            ("2026-04-05 00:00:00", "REC", "VENDA APTO RIO ANDRÉ", "520000", "TAG"),
            ("2026-04-28 00:00:00", "REC", "SALARIO", "20596.21", "TAG"),
        ],
        columns=["DATA", "CATEGORIA", "BENEFICIÁRIO", "VALOR", "CLASSIFICAÇÃO"],
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        lancamentos.to_excel(escritor, sheet_name="Lançamento Despesas e Receitas", index=False)
    df = tabular.carregar_tabela(buffer.getvalue(), "planilha.xlsx")
    lidos, _ = tabular.extrair(df, tabular.sugerir_mapeamento(df.columns, df), origem="planilha")
    repo.importar(
        engine, lancamentos=lidos, conta_id=repo.conta_da_planilha(engine),
        arquivo="planilha.xlsx", origem="planilha", usuario="andre",
        pessoa_padrao="Rô", usar_ia=False,
    )

    with engine.connect() as conn:
        resumo = analytics.resumo(conn, ano=2026)
        matriz = analytics.receitas_por_pessoa_e_tipo(conn, 2026)

    por_tipo = {linha["tipo"]: linha for linha in matriz["linhas"]}
    # a descrição diz o que foi: venda de bem, não pró-labore
    assert por_tipo["Venda de Bens"]["total"] == 52_000_000
    assert por_tipo["Pró-labore / Salário"]["total"] == 2_059_621
    # o rótulo diz de quem é: as duas são do André
    assert {linha["pessoa"] for linha in matriz["linhas"]} == {"André"}
    # e só o salário vira base do orçamento
    assert resumo["renda_recorrente"] == 2_059_621
    assert resumo["receitas_nao_recorrentes"] == 52_000_000


# ---------------------------------------------------------------------------
# regra nova não reclassifica sozinha o que já está gravado
# ---------------------------------------------------------------------------
def test_reaplicar_regras_resolve_o_que_ficou_pendente(engine):
    """Uma categoria criada depois só alcança o passado por este caminho.

    A pensão do André entrou na base antes de existir Filhos & Pensão, e ficou
    na fila. Criar a categoria não move lançamento nenhum — quem move é o botão
    "Reaplicar regras na fila", e é ele que este teste protege.
    """
    from core import classify

    lancamentos = pd.DataFrame(
        [("2026-03-10 00:00:00", "DESP", "PENSÃO ALIMENTICIA", "15600",
          "CONTRIBUIÇÃO MENSAL"),
         ("2026-03-11 00:00:00", "DESP", "PADARIA", "50", "ALIMENTAÇÃO")],
        columns=["DATA", "CATEGORIA", "BENEFICIÁRIO", "VALOR", "CLASSIFICAÇÃO"],
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        lancamentos.to_excel(escritor, sheet_name="Lançamento Despesas e Receitas", index=False)
    df = tabular.carregar_tabela(buffer.getvalue(), "planilha.xlsx")
    lidos, _ = tabular.extrair(df, tabular.sugerir_mapeamento(df.columns, df), origem="planilha")

    # simula a base de antes: sem as regras de pensão, a linha cai na fila
    with engine.begin() as conn:
        conn.execute(
            sa.delete(db.regras).where(db.regras.c.padrao.like("PENSAO%"))
        )
    repo.importar(
        engine, lancamentos=lidos, conta_id=repo.conta_da_planilha(engine),
        arquivo="planilha.xlsx", origem="planilha", usuario="andre",
        pessoa_padrao="Rô", usar_ia=False,
    )
    with engine.connect() as conn:
        assert len(repo.fila_pendentes(conn)) == 1

    # o reboot devolve as regras; o botão é que alcança o que já estava gravado
    seed.semear(engine)
    with engine.begin() as conn:
        assert classify.reclassificar_pendentes(conn) == 1

    with engine.connect() as conn:
        assert repo.fila_pendentes(conn) == []
        por_categoria = {
            linha["categoria"]: linha["total"]
            for linha in analytics.por_categoria(conn, competencia="2026-03")
        }
    assert abs(por_categoria["Filhos & Pensão"]) == 1_560_000
