"""Análise escrita do mês, gerada pela Claude API sobre os números já apurados."""

from __future__ import annotations

import streamlit as st

from core import ai, analytics, repo


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        competencias = repo.competencias_disponiveis(conn)
    if not competencias:
        st.info("Sem lançamentos ainda — importe um extrato para a IA ter o que analisar.",
                icon="📥")
        return

    c1, c2 = st.columns([1.2, 3])
    competencia = c1.selectbox("Competência", competencias)

    if not ai.disponivel():
        st.warning(
            "**Análise por IA ainda não configurada.** Adicione `ANTHROPIC_API_KEY` em "
            "`.streamlit/secrets.toml` (ou nas configurações do Streamlit Cloud) para ligar. "
            "Todo o resto do sistema funciona sem ela.",
            icon="🔌",
        )
        with st.expander("Ver os números que seriam enviados para a análise"):
            with engine.connect() as conn:
                st.code(analytics.contexto_para_ia(conn, competencia), language="text")
        return

    cache = st.session_state.setdefault("analises", {})
    forcar = c2.button("Gerar análise deste mês", type="primary")

    if competencia in cache and not forcar:
        st.markdown(cache[competencia])
        st.caption("Análise já gerada nesta sessão. Clique no botão para refazer.")
        return

    if not forcar:
        st.caption(
            "A análise usa apenas os números já classificados: gasto por categoria, evolução "
            "dos meses, metas e as maiores saídas. Nada é inventado."
        )
        return

    with engine.connect() as conn:
        contexto = analytics.contexto_para_ia(conn, competencia)
    with st.spinner("Analisando o mês…"):
        texto = ai.analisar_mes(contexto)
    cache[competencia] = texto
    st.markdown(texto)

    with st.expander("Números usados na análise"):
        st.code(contexto, language="text")
