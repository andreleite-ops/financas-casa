"""Login com dois usuarios: Andre e Ro.

Nao ha cadastro aberto. As senhas ficam como hash bcrypt em
`.streamlit/secrets.toml`; o arquivo de exemplo mostra o formato e
`scripts/gerar_senha.py` gera os hashes.

Sem secrets configurado (primeira execucao local), o app entra em modo de
desenvolvimento com uma senha padrao e um aviso bem visivel na tela.
"""

from __future__ import annotations

import os

import bcrypt
import streamlit as st

SENHA_DEV = "financas"
USUARIOS_PADRAO = {
    "andre": {"nome": "André"},
    "ro": {"nome": "Rô"},
}


def _usuarios() -> tuple[dict, bool]:
    """Devolve (usuarios, modo_dev)."""
    try:
        crus = st.secrets.get("usuarios")
    except Exception:
        crus = None
    if not crus:
        return dict(USUARIOS_PADRAO), True

    usuarios: dict[str, dict] = {}
    for login, dados in dict(crus).items():
        dados = dict(dados)
        usuarios[str(login).strip().lower()] = {
            "nome": dados.get("nome", login),
            "senha_hash": dados.get("senha_hash", ""),
        }
    return usuarios, False


def gerar_hash(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _confere(senha: str, usuario: dict, modo_dev: bool) -> bool:
    if modo_dev:
        return senha == os.environ.get("SENHA_DEV", SENHA_DEV)
    hash_guardado = usuario.get("senha_hash", "")
    if not hash_guardado:
        return False
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_guardado.encode("utf-8"))
    except ValueError:
        return False


def exigir_login() -> dict:
    """Bloqueia o app ate alguem entrar. Devolve o usuario logado."""
    if st.session_state.get("usuario"):
        return st.session_state["usuario"]

    usuarios, modo_dev = _usuarios()

    st.markdown(
        "<div class='login-topo'><h1>Finanças da Casa</h1>"
        "<p>Acesso restrito a André e Rô</p></div>",
        unsafe_allow_html=True,
    )
    with st.form("login"):
        quem = st.selectbox(
            "Quem está entrando",
            options=list(usuarios.keys()),
            format_func=lambda login: usuarios[login]["nome"],
        )
        senha = st.text_input("Senha", type="password")
        entrou = st.form_submit_button("Entrar", width="stretch")

    if modo_dev:
        st.info(
            "**Modo de desenvolvimento** — nenhuma senha configurada ainda. "
            f"Use `{SENHA_DEV}` para entrar. Antes de publicar, gere os hashes com "
            "`python scripts/gerar_senha.py` e cole em `.streamlit/secrets.toml`.",
            icon="🔧",
        )

    if entrou:
        if _confere(senha, usuarios[quem], modo_dev):
            st.session_state["usuario"] = {"login": quem, "nome": usuarios[quem]["nome"]}
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()


def sair() -> None:
    st.session_state.pop("usuario", None)
    st.rerun()
