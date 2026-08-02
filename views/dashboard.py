"""Visão Geral: cards do mês, categoria x meta, evolução e tabelas."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import analytics, db, repo
from core.money import fmt_brl, fmt_mil
from ui import graficos
from ui.tema import BOM, CRITICO, SERIE_DESPESA, SERIE_POUPANCA


def _barra_categoria(item: dict, teto: int) -> str:
    nome, realizado, meta = item["categoria"], item["realizado"], item["meta"]
    largura = (realizado / teto * 100) if teto else 0
    posicao_meta = (meta / teto * 100) if teto and meta else None
    estourou = item["estourou"]

    if not meta:
        legenda = "<small class='ok'>sem meta definida</small>"
    elif item["meta_e_piso"]:
        # poupanca: a meta e piso, superar e bom
        falta = (1 - realizado / meta) * 100
        legenda = (
            f"<small class='ov'>▼ {falta:.0f}% abaixo da meta</small>"
            if item["abaixo_do_piso"]
            else f"<small class='bom'>✔ {realizado / meta * 100:.0f}% da meta</small>"
        )
    elif estourou:
        legenda = f"<small class='ov'>▲ {(realizado / meta - 1) * 100:.0f}% acima da meta</small>"
    else:
        legenda = f"<small class='ok'>{realizado / meta * 100:.0f}% da meta</small>"
    tick = (
        f"<div class='meta-tick' style='left:{min(posicao_meta, 100):.1f}%'></div>"
        if posicao_meta is not None
        else ""
    )
    cor = SERIE_POUPANCA if item["meta_e_piso"] else SERIE_DESPESA

    # a barra e sempre vinho; o trecho que passou da meta ganha hachura.
    # Marcar o excesso por forma, e nao por cor, mantem a leitura clara --
    # vermelho ao lado de vinho vira quase a mesma coisa numa barra fina.
    if estourou and posicao_meta is not None:
        base = f"<div class='bar' style='width:{posicao_meta:.1f}%;background:{cor}'></div>"
        excesso = (
            f"<div class='bar excesso' style='left:{posicao_meta:.1f}%;"
            f"width:{min(largura - posicao_meta, 100 - posicao_meta):.1f}%'></div>"
        )
        barras = base + excesso
    else:
        barras = f"<div class='bar' style='width:{min(largura, 100):.1f}%;background:{cor}'></div>"

    return (
        f"<div class='cb'><div class='lbl'>{nome}{legenda}</div>"
        f"<div class='track'>{barras}{tick}</div>"
        f"<div class='val'>{fmt_brl(realizado)}</div></div>"
    )


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        competencias = repo.competencias_disponiveis(conn)

    if not competencias:
        st.info(
            "Ainda não há lançamentos. Comece pela tela **Upload de Extratos** — "
            "importe a planilha da Rô para carregar o histórico e depois as faturas e "
            "extratos do mês.",
            icon="📥",
        )
        return

    coluna_mes, coluna_pessoa, _ = st.columns([1.2, 1.2, 2.4])
    competencia = coluna_mes.selectbox("Competência", competencias, index=0)
    pessoa = coluna_pessoa.selectbox("Pessoa", ["Todos", *db.PESSOAS])
    ano = int(competencia[:4])

    with engine.connect() as conn:
        atual = analytics.resumo(conn, competencia=competencia, pessoa=pessoa)
        serie = analytics.serie_mensal(conn, ano, pessoa=pessoa)
        categorias = analytics.por_categoria(conn, competencia=competencia, pessoa=pessoa)
        metas = repo.listar_metas(conn, ano)
        orcamento = analytics.orcamento(conn, competencia, metas)
        matriz = analytics.tabela_mes_a_mes(conn, ano, pessoa=pessoa)
        anual = analytics.comparativo_anual(conn, pessoa=pessoa)
        acumulado = analytics.resumo(conn, ano=ano, pessoa=pessoa)

    posicao = next((i for i, m in enumerate(serie) if m["competencia"] == competencia), None)
    anterior = serie[posicao - 1] if posicao else None

    def variacao(chave: str) -> str | None:
        if not anterior or not anterior[chave]:
            return None
        delta = (atual[chave] / anterior[chave] - 1) * 100
        return f"{delta:+.1f}% vs mês anterior"

    c1, c2, c3, c4 = st.columns(4)
    # só usamos delta onde ele é de fato uma variação; o resto vai em legenda,
    # senão o Streamlit desenha uma seta ↑↓ em texto que não é comparação
    c1.metric("Receitas do mês", fmt_brl(atual["receitas"]), variacao("receitas"))
    c2.metric("Despesas do mês", fmt_brl(atual["despesas"]), variacao("despesas"),
              delta_color="inverse")

    meta_poupanca = next(
        (o["meta"] for o in orcamento if o["categoria"] == analytics.CATEGORIA_POUPANCA), 0
    )
    c3.metric("Poupança do mês", fmt_brl(atual["poupanca"]))
    if meta_poupanca:
        proporcao = atual["poupanca"] / meta_poupanca * 100
        if proporcao >= 100:
            c3.markdown(
                f"<span class='nota' style='color:{BOM}'>✔ {proporcao:.0f}% da meta "
                f"({fmt_brl(meta_poupanca)})</span>",
                unsafe_allow_html=True,
            )
        else:
            c3.markdown(
                f"<span class='nota' style='color:{CRITICO}'>{proporcao:.0f}% da meta — "
                f"faltam {fmt_brl(meta_poupanca - atual['poupanca'])}</span>",
                unsafe_allow_html=True,
            )
    else:
        c3.markdown("<span class='nota'>sem meta definida</span>", unsafe_allow_html=True)

    estouradas = [o for o in orcamento if o["estourou"]]
    c4.metric("Sobra livre", fmt_brl(atual["sobra"]))
    c4.markdown(
        f"<span class='nota' style='color:{CRITICO if estouradas else BOM}'>"
        + (f"{len(estouradas)} categoria(s) acima da meta" if estouradas
           else "todas as categorias dentro da meta")
        + "</span>",
        unsafe_allow_html=True,
    )

    if atual["nao_classificado"]:
        st.warning(
            f"{fmt_brl(abs(atual['nao_classificado']))} ainda sem categoria neste mês. "
            "Resolva na tela **Classificação** para os números fecharem.",
            icon="🏷️",
        )

    st.markdown("### Gasto por categoria")
    st.markdown(
        "<p class='sub'>Barra = realizado · traço preto = meta do mês</p>", unsafe_allow_html=True
    )
    com_gasto = [o for o in orcamento if o["realizado"] or o["meta"]]
    com_gasto.sort(key=lambda linha: -linha["realizado"])
    if com_gasto:
        teto = max(max(o["realizado"] for o in com_gasto),
                   max(o["meta"] for o in com_gasto), 1)
        st.markdown(
            "".join(_barra_categoria(item, teto) for item in com_gasto),
            unsafe_allow_html=True,
        )
    else:
        st.caption("Nenhum gasto classificado neste mês.")

    esquerda, direita = st.columns([1.35, 1])
    with esquerda:
        st.markdown(f"### Evolução mensal {ano}")
        grafico = graficos.receitas_despesas(serie)
        if grafico is not None:
            st.altair_chart(grafico, width="stretch")
    with direita:
        st.markdown("### Participação no mês")
        rosca = graficos.rosca_categorias(categorias)
        if rosca is not None:
            st.altair_chart(rosca, width="stretch")
        else:
            st.caption("Sem despesas classificadas no mês.")

    st.markdown(f"### Mês a mês {ano} · acumulado e comparativo")
    if matriz["linhas"]:
        colunas = {"Categoria": [linha["categoria"] for linha in matriz["linhas"]]}
        for mes in matriz["meses"]:
            colunas[graficos.MESES_PT.get(mes, mes)] = [
                fmt_mil(linha["meses"][mes]) for linha in matriz["linhas"]
            ]
        colunas[f"Acum. {ano}"] = [fmt_mil(linha["acumulado"]) for linha in matriz["linhas"]]
        colunas["Média/mês"] = [fmt_mil(linha["media"]) for linha in matriz["linhas"]]
        colunas[f"Total {ano - 1}"] = [
            fmt_mil(linha["ano_anterior"]) if linha["ano_anterior"] else "—"
            for linha in matriz["linhas"]
        ]
        tabela = pd.DataFrame(colunas)
        total = {"Categoria": "TOTAL"}
        for mes in matriz["meses"]:
            total[graficos.MESES_PT.get(mes, mes)] = fmt_mil(
                sum(linha["meses"][mes] for linha in matriz["linhas"])
            )
        total[f"Acum. {ano}"] = fmt_mil(sum(l["acumulado"] for l in matriz["linhas"]))
        total["Média/mês"] = fmt_mil(
            sum(l["acumulado"] for l in matriz["linhas"]) // max(len(matriz["meses"]), 1)
        )
        total[f"Total {ano - 1}"] = fmt_mil(sum(l["ano_anterior"] for l in matriz["linhas"]))
        tabela = pd.concat([tabela, pd.DataFrame([total])], ignore_index=True)
        st.dataframe(tabela, width="stretch", hide_index=True)
        st.markdown(
            "<p class='nota'>Valores em R$ mil. A poupança aparece como categoria própria e "
            "não entra no total de despesas.</p>",
            unsafe_allow_html=True,
        )

    st.markdown("### Ano a ano")
    if len(anual) > 1 or (anual and anual[0]["ano"] != date.today().year):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Ano": linha["ano"],
                        "Receitas": fmt_brl(linha["receitas"]),
                        "Despesas": fmt_brl(linha["despesas"]),
                        "Poupança": fmt_brl(linha["poupanca"]),
                        "Sobra livre": fmt_brl(linha["sobra"]),
                        "Taxa de poupança": (
                            f"{linha['poupanca'] / linha['receitas'] * 100:.1f}%"
                            if linha["receitas"]
                            else "—"
                        ),
                    }
                    for linha in anual
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption(
            f"Acumulado {ano}: receitas {fmt_brl(acumulado['receitas'])} · "
            f"despesas {fmt_brl(acumulado['despesas'])} · "
            f"poupança {fmt_brl(acumulado['poupanca'])}. "
            "O comparativo ano a ano aparece quando houver mais de um ano importado."
        )
