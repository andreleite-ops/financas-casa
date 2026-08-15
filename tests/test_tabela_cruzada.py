"""Planilha com os meses nas colunas — o formato das receitas do André.

Cada linha é um tipo de recebimento (salário, bônus) e cada coluna um mês.
Sem desempilhar, o leitor veria uma linha por tipo e perderia a distribuição
ao longo do ano.
"""

from __future__ import annotations

import pandas as pd
import pytest

from parsers import tabular
from parsers.base import ErroDeLeitura


def _planilha_de_receitas():
    return pd.DataFrame({
        "André Leite": ["Bônus Comercial 2026", "Bônus Anual 2025", "Salário", "Total"],
        "dez/25 - (Antecip.)": ["", "700.000,00", "", "700.000,00"],
        "jan/26": ["-", "-", "19.754,67", "19.754,67"],
        "fev/26": ["11.457,82", "-", "19.754,67", "31.212,49"],
        "mar/26": ["-", "30.244,33", "19.754,67", "49.999,00"],
        "Total": ["11.457,82", "730.244,33", "59.264,01", "800.966,16"],
    })


def test_reconhece_o_mes_mesmo_com_anotacao_no_cabecalho():
    """"dez/25 - (Antecip.)" é um mês tanto quanto "dez/25"."""
    assert tabular.mes_da_coluna("dez/25 - (Antecip.)") == "2025-12"
    assert tabular.mes_da_coluna("jan/26") == "2026-01"
    assert tabular.mes_da_coluna("01/2026") == "2026-01"
    assert tabular.mes_da_coluna("Total") is None
    assert tabular.mes_da_coluna("André Leite") is None


def test_desempilha_uma_linha_por_mes():
    longo = tabular.desempilhar(_planilha_de_receitas(), "André Leite", dia=5)

    # 1 bônus comercial + 2 do bônus anual + 3 salários
    assert len(longo) == 6
    assert set(longo.columns) == {"DATA", "DESCRICAO", "VALOR"}
    assert "05/12/2025" in list(longo["DATA"])


def test_descarta_a_linha_e_a_coluna_de_total():
    """Somar de novo o que já está nos meses dobraria tudo."""
    longo = tabular.desempilhar(_planilha_de_receitas(), "André Leite")

    assert "Total" not in list(longo["DESCRICAO"])
    mapa = tabular.sugerir_mapeamento(longo.columns, longo)
    lancamentos, _ = tabular.extrair(longo, mapa, origem="planilha")
    soma = sum(lan.valor_centavos for lan in lancamentos)
    # 700.000 + 11.457,82 + 30.244,33 + 19.754,67 × 2 + 19.754,67
    assert soma == 70_000_000 + 1_145_782 + 3_024_433 + 1_975_467 * 3


def test_celulas_vazias_e_travessoes_nao_viram_lancamento():
    longo = tabular.desempilhar(_planilha_de_receitas(), "André Leite")
    assert all(str(v).strip() not in ("", "-") for v in longo["VALOR"])


def test_arquivo_sem_coluna_de_mes_avisa():
    df = pd.DataFrame({"Item": ["Salário"], "Valor": ["100,00"]})
    with pytest.raises(ErroDeLeitura, match="mês"):
        tabular.desempilhar(df, "Item")


def test_dia_do_mes_respeita_meses_curtos():
    df = pd.DataFrame({"Item": ["Salário"], "fev/26": ["1.000,00"]})
    longo = tabular.desempilhar(df, "Item", dia=28)
    assert list(longo["DATA"]) == ["28/02/2026"]


# ---------------------------------------------------------------------------
# a tabela de baixo tem de fechar com o card de cima
# ---------------------------------------------------------------------------
def test_matriz_de_despesas_fecha_com_o_card_do_topo(engine, conn):
    """Duas divergências, em direções opostas, que se somavam na tela.

    A matriz incluía transferência entre contas — que o card exclui, porque
    pagar a fatura não é gasto — e deixava de fora o que ainda não tem
    categoria, que o card conta. Na prática o pagamento da fatura aparecia como
    a maior "despesa" do ano e o pendente sumia da tabela.
    """
    from datetime import date

    import sqlalchemy as sa

    from core import analytics, db
    from core.dedup import hash_lancamento
    from core.texto import normalizar

    def inserir(dia, descricao, valor, categoria=None):
        conta_id = conn.execute(sa.select(db.contas.c.id).limit(1)).scalar_one()
        categoria_id = None
        if categoria:
            categoria_id = conn.execute(
                sa.select(db.categorias.c.id).where(db.categorias.c.nome == categoria)
            ).scalar_one()
        norm = normalizar(descricao)
        conn.execute(
            sa.insert(db.transacoes).values(
                data=dia, competencia=dia.strftime("%Y-%m"), descricao=descricao,
                descricao_norm=norm, valor_centavos=valor, conta_id=conta_id,
                categoria_id=categoria_id, pessoa="Casal",
                status="manual" if categoria_id else "pendente", origem="extrato",
                hash_dedup=hash_lancamento(conta_id, dia, valor, norm), ativo=True,
            )
        )

    inserir(date(2026, 8, 3), "SUPERMERCADO", -60_000, "Alimentação")
    inserir(date(2026, 8, 4), "PIX SEM NOME", -40_000)                      # pendente
    inserir(date(2026, 8, 10), "PAGAMENTO DE FATURA", -500_000,
            analytics.CATEGORIA_TRANSFERENCIA)                              # não é gasto
    inserir(date(2026, 8, 12), "SALARIO", 900_000, "Trabalho")              # receita

    card = analytics.resumo(conn, competencia="2026-08")["despesas"]
    tabela = analytics.tabela_mes_a_mes(conn, 2026)
    soma = sum(linha["acumulado"] for linha in tabela["linhas"])

    assert soma == card == 100_000
    categorias = {linha["categoria"] for linha in tabela["linhas"]}
    assert analytics.SEM_CATEGORIA in categorias                  # o pendente aparece
    assert analytics.CATEGORIA_TRANSFERENCIA not in categorias    # a transferência não
    assert "Trabalho" not in categorias                           # receita nunca entrou
