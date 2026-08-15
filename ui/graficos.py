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
    """Receitas e despesas mes a mes, em dois paineis com escalas proprias.

    Um painel so nao servia: o bonus de janeiro e a venda do apartamento em
    abril passam de meio milhao cada, e numa escala que precisa chegar la os
    meses normais da casa viravam tocos de dois pixels — um ano inteiro de
    gasto ilegivel para caber duas barras. Dois paineis, cada um com sua
    escala, dizem a verdade sem esmagar nada; o eixo do tempo continua o mesmo
    embaixo, e e ele que permite ler os dois juntos."""
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
    cor = alt.Color(
        "serie:N",
        scale=alt.Scale(
            domain=["Receitas", "Despesas", "Poupança"],
            range=[SERIE_RECEITA, SERIE_DESPESA, SERIE_POUPANCA],
        ),
        legend=alt.Legend(title=None, orient="top", labelColor=CINZA),
    )
    tooltip = [
        alt.Tooltip("mes:N", title="Mês"),
        alt.Tooltip("serie:N", title="Série"),
        alt.Tooltip("rotulo:N", title="Valor"),
    ]

    def painel(series: list[str], titulo: str, altura: int, com_rotulo_x: bool):
        eixo_x = (
            alt.Axis(labelAngle=0, **_EIXO) if com_rotulo_x
            else alt.Axis(labels=False, ticks=False, title=None, domainColor=LINHA)
        )
        dados = df[df["serie"].isin(series)]
        # painel inteiro zerado (o mês do cartão antes de entrar o extrato, por
        # exemplo) degenera a escala: o Vega calcula os cortes num intervalo de
        # largura zero e imprime "NaN" no eixo. Um teto de R$ 1 mantém o painel
        # vazio, mas legível.
        teto = float(dados["valor"].max() or 0)
        escala = alt.Scale(domain=[0, 1]) if teto <= 0 else alt.Undefined
        return (
            alt.Chart(dados)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=18)
            .encode(
                x=alt.X("mes:N", sort=alt.SortField("ordem"), title=None, axis=eixo_x),
                xOffset=alt.XOffset("serie:N", sort=series),
                y=alt.Y("valor:Q", title=titulo, scale=escala,
                        axis=alt.Axis(format="~s", **_EIXO)),
                color=cor,
                tooltip=tooltip,
            )
            .properties(height=altura)
        )

    return (
        alt.vconcat(
            painel(["Receitas"], "Receitas (R$)", 120, False),
            painel(["Despesas", "Poupança"], "Despesas e poupança (R$)", 150, True),
            spacing=6,
        )
        .resolve_scale(y="independent", color="shared")
        .configure_view(strokeWidth=0)
    )


def rosca_categorias(dados: list[dict], limite: int = 5):
    """Participacao de cada categoria no mes.

    Cinco fatias mais "Demais". Rosca serve para dar a proporcao de relance, e
    passar de seis fatias acaba com isso: as menores viram fios, os tons
    vizinhos se confundem e sobra um grafico bonito que ninguem le. Quem quer o
    detalhe tem a lista de barras logo acima, com o valor de cada categoria.
    """
    if not dados:
        return None
    principais = dados[:limite]
    resto = sum(linha["total"] for linha in dados[limite:])
    total = sum(linha["total"] for linha in dados) or 1
    linhas = [{"categoria": d["categoria"], "valor": d["total"] / 100,
               "parte": f"{d['total'] / total * 100:.0f}%",
               "rotulo": fmt_brl(d["total"])} for d in principais]
    if resto:
        linhas.append({"categoria": "Demais", "valor": resto / 100,
                       "parte": f"{resto / total * 100:.0f}%", "rotulo": fmt_brl(resto)})
    df = pd.DataFrame(linhas)
    ordem = [linha["categoria"] for linha in linhas]
    base = alt.Chart(df).encode(
        theta=alt.Theta("valor:Q", stack=True),
        color=alt.Color(
            "categoria:N",
            sort=ordem,
            scale=alt.Scale(domain=ordem, range=TONS_ROSCA[: len(ordem)]),
            legend=alt.Legend(title=None, orient="right", labelColor=CINZA,
                              labelFontSize=11),
        ),
        tooltip=[alt.Tooltip("categoria:N", title="Categoria"),
                 alt.Tooltip("rotulo:N", title="Total")],
    )
    arco = base.mark_arc(innerRadius=58, outerRadius=92, stroke="#FFFFFF", strokeWidth=2)
    # o percentual vai FORA do arco, no branco: dentro dele o cinza sumia no
    # vinho escuro das fatias maiores. E a resposta que a rosca existe para
    # dar, e ela nao pode depender de passar o mouse
    percentual = base.mark_text(radius=108, fontSize=11, fontWeight=600, color=CINZA).encode(
        text=alt.Text("parte:N"),
    )
    return (arco + percentual).properties(height=250).configure_view(strokeWidth=0)


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
