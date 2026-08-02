"""Receitas — sempre separadas por pessoa."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import analytics, repo
from core.money import fmt_brl
from ui import graficos
from ui.tema import VINHO, selo_pessoa


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        competencias = repo.competencias_disponiveis(conn)
    if not competencias:
        st.info("Sem lançamentos ainda. Importe um extrato para ver as receitas.", icon="📥")
        return

    c1, c2, _ = st.columns([1.2, 1.2, 2.4])
    competencia = c1.selectbox("Competência", competencias)
    ano = int(competencia[:4])
    visao = c2.radio("Visão", ["Mês", f"Ano {ano}"], horizontal=True)
    filtro = {"competencia": competencia} if visao == "Mês" else {"ano": ano}

    with engine.connect() as conn:
        total = analytics.resumo(conn, **filtro)
        por_pessoa = analytics.receitas_por_pessoa(conn, **filtro)
        itens = analytics.lancamentos(conn, **filtro, natureza="receita", limite=300)
        serie = analytics.serie_mensal(conn, ano)

    colunas = st.columns(1 + len(por_pessoa))
    colunas[0].metric("Total do casal", fmt_brl(total["receitas"]))
    for coluna, linha in zip(colunas[1:], por_pessoa):
        parte = (linha["total"] / total["receitas"] * 100) if total["receitas"] else 0
        coluna.metric(linha["pessoa"], fmt_brl(linha["total"]), f"{parte:.0f}% do total",
                      delta_color="off")

    esquerda, direita = st.columns([1, 1.3])
    with esquerda:
        st.markdown("### Por pessoa")
        grafico = graficos.barras_pessoa(por_pessoa)
        if grafico is not None:
            st.altair_chart(grafico, width="stretch")
    with direita:
        st.markdown(f"### Receitas mês a mês {ano}")
        if serie:
            df = pd.DataFrame(
                [
                    {"Mês": graficos.rotulo_mes(m["competencia"]),
                     "Receitas": fmt_brl(m["receitas"]),
                     "Despesas": fmt_brl(m["despesas"]),
                     "Poupança": fmt_brl(m["poupanca"])}
                    for m in serie
                ]
            )
            st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("### Lançamentos de receita")
    if not itens:
        st.caption(
            "Nenhuma receita classificada no período. Se o salário caiu na conta e não "
            "apareceu aqui, ele deve estar na fila da tela **Classificação**."
        )
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Data": f"{i['data']:%d/%m/%Y}",
                    "Descrição": i["descricao"],
                    "Categoria": i["categoria"] or "—",
                    "Subcategoria": i["subcategoria"] or "—",
                    "Pessoa": i["pessoa"],
                    "Conta": i["conta"],
                    "Valor": fmt_brl(i["valor_centavos"]),
                }
                for i in itens
            ]
        ),
        width="stretch", hide_index=True,
    )

    quebra: dict[tuple[str, str], int] = {}
    for item in itens:
        chave = (item["pessoa"], item["subcategoria"] or item["categoria"] or "—")
        quebra[chave] = quebra.get(chave, 0) + item["valor_centavos"]
    if quebra:
        st.markdown("#### Composição por pessoa e tipo")
        linhas = "".join(
            f"<div class='cb'><div class='lbl'>{selo_pessoa(pessoa)} {tipo}</div>"
            f"<div class='track'><div class='bar' style='width:"
            f"{valor / max(quebra.values()) * 100:.1f}%;background:{VINHO}'></div></div>"
            f"<div class='val'>{fmt_brl(valor)}</div></div>"
            for (pessoa, tipo), valor in sorted(quebra.items(), key=lambda kv: -kv[1])
        )
        st.markdown(linhas, unsafe_allow_html=True)
