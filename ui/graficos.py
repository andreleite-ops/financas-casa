"""Graficos do dashboard, em Altair.

Todas as barras saem da mesma rampa de vinho, separadas por luminosidade em vez
de matiz: receita no tom mais escuro, despesa no tom principal, poupanca no tom
claro. Isso mantem a leitura mesmo para daltonismo e em impressao preto e
branco. Grade discreta, eixo em cinza recuado.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from core.money import fmt_brl

from .tema import (
    CINZA,
    CINZA_CLARO,
    LINHA,
    SERIE_DESPESA,
    SERIE_POUPANCA,
    SERIE_RECEITA,
    VINHO,
    VINHO_CLARO,
    VINHO_ESCURO,
)

MESES_PT = {
    "01": "Jan", "02": "Fev", "03": "Mar", "04": "Abr", "05": "Mai", "06": "Jun",
    "07": "Jul", "08": "Ago", "09": "Set", "10": "Out", "11": "Nov", "12": "Dez",
}

_EIXO = {"labelColor": CINZA_CLARO, "titleColor": CINZA, "domainColor": LINHA,
         "tickColor": LINHA, "gridColor": "#EAE3D6", "labelFont": "system-ui",
         "labelFontSize": 11}

# tons de vinho para as fatias da rosca, do escuro ao claro
TONS_ROSCA = ["#3F1120", "#5E1D2F", "#7B2A3E", "#98404F", "#B25E6C",
              "#C67C8D", "#D69CA8", "#E3BCC4", "#8A8378"]


def rotulo_mes(competencia: str) -> str:
    return MESES_PT.get(competencia[5:7], competencia[5:7])


def receitas_despesas(serie: list[dict]):
    """Colunas agrupadas por mes: receitas x despesas x poupanca."""
    if not serie:
        return None
    linhas = []
    for mes in serie:
        for chave, rotulo in (
            ("receitas", "Receitas"), ("despesas", "Despesas"), ("poupanca", "Poupança")
        ):
            linhas.append(
                {
                    "mes": rotulo_mes(mes["competencia"]),
                    "ordem": mes["competencia"],
                    "serie": rotulo,
                    "valor": mes[chave] / 100,
                    "rotulo": fmt_brl(mes[chave]),
                }
            )
    df = pd.DataFrame(linhas)
    return (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=20)
        .encode(
            x=alt.X("mes:N", sort=alt.SortField("ordem"), title=None,
                    axis=alt.Axis(labelAngle=0, **_EIXO)),
            xOffset=alt.XOffset("serie:N", sort=["Receitas", "Despesas", "Poupança"]),
            y=alt.Y("valor:Q", title="R$", axis=alt.Axis(format=",.0f", **_EIXO)),
            color=alt.Color(
                "serie:N",
                scale=alt.Scale(
                    domain=["Receitas", "Despesas", "Poupança"],
                    range=[SERIE_RECEITA, SERIE_DESPESA, SERIE_POUPANCA],
                ),
                legend=alt.Legend(title=None, orient="top", labelColor=CINZA),
            ),
            tooltip=[
                alt.Tooltip("mes:N", title="Mês"),
                alt.Tooltip("serie:N", title="Série"),
                alt.Tooltip("rotulo:N", title="Valor"),
            ],
        )
        .properties(height=260)
        .configure_view(strokeWidth=0)
    )


def rosca_categorias(dados: list[dict], limite: int = 8):
    """Participacao de cada categoria no mes."""
    if not dados:
        return None
    principais = dados[:limite]
    resto = sum(linha["total"] for linha in dados[limite:])
    linhas = [{"categoria": d["categoria"], "valor": d["total"] / 100,
               "rotulo": fmt_brl(d["total"])} for d in principais]
    if resto:
        linhas.append({"categoria": "Demais", "valor": resto / 100, "rotulo": fmt_brl(resto)})
    df = pd.DataFrame(linhas)
    return (
        alt.Chart(df)
        .mark_arc(innerRadius=62, stroke="#FFFFFF", strokeWidth=2)
        .encode(
            theta=alt.Theta("valor:Q", stack=True),
            color=alt.Color(
                "categoria:N",
                sort=[linha["categoria"] for linha in linhas],
                scale=alt.Scale(range=TONS_ROSCA),
                legend=alt.Legend(title=None, orient="right", labelColor=CINZA,
                                  labelFontSize=11),
            ),
            tooltip=[alt.Tooltip("categoria:N", title="Categoria"),
                     alt.Tooltip("rotulo:N", title="Total")],
        )
        .properties(height=250)
        .configure_view(strokeWidth=0)
    )


def evolucao_categoria(historico: list[dict], categoria: str):
    """Linha de uma categoria ao longo dos meses."""
    if not historico:
        return None
    df = pd.DataFrame(
        [{"mes": rotulo_mes(h["competencia"]), "ordem": h["competencia"],
          "valor": h["total"] / 100, "rotulo": fmt_brl(h["total"])} for h in historico]
    )
    base = alt.Chart(df).encode(
        x=alt.X("mes:N", sort=alt.SortField("ordem"), title=None,
                axis=alt.Axis(labelAngle=0, **_EIXO)),
        y=alt.Y("valor:Q", title="R$", axis=alt.Axis(format=",.0f", **_EIXO)),
    )
    linha = base.mark_line(color=SERIE_DESPESA, strokeWidth=2)
    ponto = base.mark_point(color=SERIE_DESPESA, filled=True, size=55).encode(
        tooltip=[alt.Tooltip("mes:N", title="Mês"), alt.Tooltip("rotulo:N", title=categoria)]
    )
    return (linha + ponto).properties(height=220).configure_view(strokeWidth=0)


def barras_pessoa(dados: list[dict]):
    """Receitas por pessoa."""
    if not dados:
        return None
    from .tema import CORES_PESSOA

    df = pd.DataFrame(
        [{"pessoa": d["pessoa"], "valor": d["total"] / 100, "rotulo": fmt_brl(d["total"])}
         for d in dados]
    )
    return (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, size=26)
        .encode(
            y=alt.Y("pessoa:N", title=None, axis=alt.Axis(**_EIXO)),
            x=alt.X("valor:Q", title="R$", axis=alt.Axis(format=",.0f", **_EIXO)),
            color=alt.Color(
                "pessoa:N",
                scale=alt.Scale(domain=list(CORES_PESSOA), range=list(CORES_PESSOA.values())),
                legend=None,
            ),
            tooltip=[alt.Tooltip("pessoa:N", title="Pessoa"),
                     alt.Tooltip("rotulo:N", title="Receitas")],
        )
        .properties(height=max(90, 42 * len(df)))
        .configure_view(strokeWidth=0)
    )
