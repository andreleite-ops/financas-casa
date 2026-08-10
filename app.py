"""Controle de Finanças Domésticas — André & Rô.

Streamlit sobre base única na nuvem. Ponto de entrada: navegação, login e
inicialização do banco.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import auth, db, dedup, repo, seed  # noqa: E402
from ui import tema  # noqa: E402
from views import (  # noqa: E402
    analise_ia,
    classificacao,
    dashboard,
    orcamento,
    plano_contas,
    receitas,
    upload,
)

st.set_page_config(
    page_title="Finanças da Casa · André & Rô",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

TELAS = [
    ("📊 Visão Geral", dashboard, "Visão Geral", "Onde o dinheiro entrou e saiu"),
    ("📥 Upload de Extratos", upload, "Upload de Extratos",
     "Faturas, extratos, planilha da Rô e cadastro de contas"),
    ("🏷️ Classificação", classificacao, "Classificação",
     "Fila de pendências e reclassificação de qualquer lançamento"),
    ("💼 Receitas", receitas, "Receitas", "Pró-labore, bônus e serviços, separados por pessoa"),
    ("🎯 Orçamento & Metas", orcamento, "Orçamento & Metas",
     "Quanto por cento da renda vai para cada categoria"),
    ("✨ Análise IA", analise_ia, "Análise IA", "Leitura do mês escrita pela IA"),
    ("📚 Plano de Contas", plano_contas, "Plano de Contas", "Categorias e subcategorias"),
]


@st.cache_resource(show_spinner="Preparando a base…")
def preparar():
    """Cria o schema e a carga inicial uma única vez por processo."""
    engine = db.get_engine()
    seed.semear(engine)
    return engine


def main() -> None:
    tema.aplicar()
    usuario = auth.exigir_login()
    engine = preparar()

    with engine.connect() as conn:
        pendentes = len(repo.fila_pendentes(conn, limite=500))
        duplicidades = len(dedup.pendentes(conn))

    with st.sidebar:
        st.markdown(
            "<div class='marca'>Finanças da Casa<small>André &amp; Rô</small></div>",
            unsafe_allow_html=True,
        )
        rotulos = []
        for rotulo, _modulo, _titulo, _sub in TELAS:
            if rotulo.startswith("🏷️") and pendentes:
                rotulo = f"{rotulo} · {pendentes}"
            elif rotulo.startswith("📥") and duplicidades:
                rotulo = f"{rotulo} · {duplicidades}"
            rotulos.append(rotulo)

        # botões de areia em vez de rádio: numa barra vinho a bolinha do rádio
        # some no escuro e o alvo do clique é minúsculo. Aqui a área inteira é
        # clicável, e a tela aberta fica marcada
        if "tela" not in st.session_state:
            st.session_state["tela"] = TELAS[0][2]
        for (_ícone, _modulo, titulo, _sub), rotulo in zip(TELAS, rotulos):
            aberta = st.session_state["tela"] == titulo
            if st.button(
                rotulo, key=f"nav_{titulo}", width="stretch",
                type="primary" if aberta else "secondary",
            ):
                st.session_state["tela"] = titulo
                st.rerun()
        indice = next(
            (i for i, tela in enumerate(TELAS) if tela[2] == st.session_state["tela"]), 0
        )

        st.divider()
        estado = db.diagnostico()
        if estado["motivo"] == "sem_url":
            st.markdown(
                "<div class='aviso-dev'><b>Base local (SQLite)</b><br>"
                "O app não recebeu nenhum <code>DATABASE_URL</code>. Confira se o segredo "
                "foi salvo <b>neste</b> app e se a linha está no topo, antes de qualquer "
                "seção entre colchetes.</div>",
                unsafe_allow_html=True,
            )
        elif estado["motivo"] == "sqlite_local":
            st.markdown(
                "<div class='aviso-dev'><b>Base local (SQLite)</b><br>"
                "Rodando num arquivo local. Serve para testar, mas cada computador "
                "teria a sua própria base.</div>",
                unsafe_allow_html=True,
            )
        elif estado["motivo"] == "falha_conexao":
            st.markdown(
                "<div class='aviso-dev'><b>Não consegui conectar ao Supabase</b><br>"
                "O <code>DATABASE_URL</code> chegou, mas a conexão falhou — normalmente é "
                "a senha do banco. Detalhe abaixo.</div>",
                unsafe_allow_html=True,
            )
            with st.expander("Ver o erro"):
                st.code(estado["detalhe"], language="text")
        st.caption(f"Logado como **{usuario['nome']}**")
        if st.button("Sair", width="stretch"):
            auth.sair()

    _rotulo, modulo, titulo, subtitulo = TELAS[indice]
    st.markdown(f"# {titulo}")
    st.markdown(f"<p class='sub'>{subtitulo}</p>", unsafe_allow_html=True)
    modulo.render(engine, usuario)


if __name__ == "__main__":
    main()
