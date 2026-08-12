"""Receitas — quanto cada um trouxe, de onde, em cada mês."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import analytics, db, repo
from core.money import fmt_brl, fmt_mil
from ui import graficos
from ui.tema import CORES_PESSOA, selo_pessoa
from views import manual


def _cartoes_por_pessoa(por_pessoa, total):
    colunas = st.columns(1 + max(len(por_pessoa), 1))
    colunas[0].metric("Total do casal", fmt_brl(total))
    for coluna, linha in zip(colunas[1:], por_pessoa):
        parte = (linha["total"] / total * 100) if total else 0
        coluna.metric(linha["pessoa"], fmt_brl(linha["total"]))
        coluna.markdown(
            f"<span class='nota'>{parte:.0f}% do total</span>", unsafe_allow_html=True
        )


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        competencias = repo.competencias_disponiveis(conn)
    if not competencias:
        st.info(
            "Sem lançamentos ainda. Importe um extrato — ou lance uma receita à mão "
            "aqui embaixo.",
            icon="📥",
        )
        manual.formulario(engine, usuario, date.today().year, "receita")
        return

    anos = sorted({int(c[:4]) for c in competencias}, reverse=True)
    c1, c2, _ = st.columns([1, 1.4, 2.2])
    ano = c1.selectbox("Ano", anos)
    do_ano = [c for c in competencias if c.startswith(str(ano))]
    escopo = c2.radio("Ver", [f"Ano {ano} inteiro", "Um mês"], horizontal=True)

    if escopo == "Um mês":
        competencia = c2.selectbox("Competência", do_ano)
        filtro = {"competencia": competencia}
        rotulo = competencia
    else:
        filtro = {"ano": ano}
        rotulo = f"ano {ano}"

    with engine.connect() as conn:
        total = analytics.resumo(conn, **filtro)["receitas"]
        por_pessoa = analytics.receitas_por_pessoa(conn, **filtro)
        matriz = analytics.receitas_por_pessoa_e_tipo(conn, ano)
        itens = analytics.lancamentos(conn, **filtro, natureza="receita", limite=400)

    _cartoes_por_pessoa(por_pessoa, total)
    manual.formulario(engine, usuario, ano, "receita")

    if not matriz["linhas"]:
        st.caption(
            "Nenhuma receita classificada. Se o salário caiu na conta e não aparece aqui, "
            "ele deve estar na fila da tela **Classificação**."
        )
        return

    # ---- a matriz: uma linha por pessoa, fonte e tipo, uma coluna por mês ---
    st.markdown(f"### Quem trouxe o quê, mês a mês · {ano}")
    st.caption(
        "**TAG** é do André · **BIOS** é da Rô · **NUN** (aluguel do apartamento) é dos dois. "
        "O dono sai da fonte, não da conta em que o dinheiro caiu — é o que impede a mesma "
        "receita de contar duas vezes quando ela aparece na planilha e no extrato."
    )
    colunas = {
        "Pessoa": [linha["pessoa"] for linha in matriz["linhas"]],
        "Fonte": [linha["fonte"] for linha in matriz["linhas"]],
        "Tipo": [linha["tipo"] for linha in matriz["linhas"]],
    }
    for mes in matriz["meses"]:
        colunas[graficos.MESES_PT.get(mes, mes)] = [
            fmt_mil(linha["meses"][mes]) if linha["meses"][mes] else "—"
            for linha in matriz["linhas"]
        ]
    colunas["Total"] = [fmt_mil(linha["total"]) for linha in matriz["linhas"]]
    tabela = pd.DataFrame(colunas)

    # uma linha de total por pessoa, para o olho fechar a conta sem somar
    for pessoa in sorted({linha["pessoa"] for linha in matriz["linhas"]}):
        da_pessoa = [linha for linha in matriz["linhas"] if linha["pessoa"] == pessoa]
        soma = {"Pessoa": pessoa, "Fonte": "", "Tipo": "— total —"}
        for mes in matriz["meses"]:
            valor = sum(linha["meses"][mes] for linha in da_pessoa)
            soma[graficos.MESES_PT.get(mes, mes)] = fmt_mil(valor) if valor else "—"
        soma["Total"] = fmt_mil(sum(linha["total"] for linha in da_pessoa))
        tabela = pd.concat([tabela, pd.DataFrame([soma])], ignore_index=True)

    tabela = tabela.sort_values(
        ["Pessoa", "Tipo"], key=lambda col: col.map(lambda v: (v == "— total —", str(v)))
    ).reset_index(drop=True)
    st.dataframe(tabela, width="stretch", hide_index=True)
    st.markdown(
        "<p class='nota'>Valores em R$ mil. Cada pessoa aparece com suas fontes de "
        "recebimento e uma linha de total.</p>",
        unsafe_allow_html=True,
    )

    # receita sem fonte reconhecida caiu no titular da conta, e isso é um chute:
    # melhor dizer quanto é do que deixar o dono errado passar por certo
    sem_fonte = [linha for linha in matriz["linhas"] if linha["fonte"] == "—"]
    if sem_fonte:
        st.warning(
            f"{fmt_brl(sum(linha['total'] for linha in sem_fonte))} de receita sem fonte "
            "identificada — o dono aí é o titular da conta, não uma decisão. Classifique "
            "esses lançamentos na tela **Classificação** para o rateio ficar de pé.",
            icon="👤",
        )

    # ---- participação de cada um -------------------------------------------
    esquerda, direita = st.columns([1, 1.3])
    with esquerda:
        st.markdown("### Participação")
        grafico = graficos.barras_pessoa(por_pessoa)
        if grafico is not None:
            st.altair_chart(grafico, width="stretch")
    with direita:
        st.markdown("### Total por pessoa")
        st.dataframe(
            pd.DataFrame([
                {
                    "Pessoa": linha["pessoa"],
                    "Receitas": fmt_brl(linha["total"]),
                    "Participação": f"{linha['total'] / total * 100:.1f}%" if total else "—",
                }
                for linha in por_pessoa
            ]),
            width="stretch", hide_index=True,
        )

    # ---- lançamentos, separados por pessoa ---------------------------------
    st.markdown(f"### Lançamentos de receita · {rotulo}")
    if not itens:
        st.caption("Nenhum lançamento de receita no período.")
        return

    pessoas = sorted({item["pessoa"] for item in itens})
    abas = st.tabs([f"{p} ({sum(1 for i in itens if i['pessoa'] == p)})" for p in pessoas])
    for aba, pessoa in zip(abas, pessoas):
        with aba:
            do_pessoa = [item for item in itens if item["pessoa"] == pessoa]
            st.markdown(
                f"{selo_pessoa(pessoa)} &nbsp; **{fmt_brl(sum(i['valor_centavos'] for i in do_pessoa))}** "
                f"em {len(do_pessoa)} lançamento(s)",
                unsafe_allow_html=True,
            )
            st.dataframe(
                pd.DataFrame([
                    {
                        "Data": f"{i['data']:%d/%m/%Y}",
                        "Descrição": i["descricao"],
                        "Tipo": i["subcategoria"] or i["categoria"] or "—",
                        "Conta": i["conta"],
                        "Valor": fmt_brl(i["valor_centavos"]),
                    }
                    for i in do_pessoa
                ]),
                width="stretch", hide_index=True,
            )
