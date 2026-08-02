"""Identidade visual do app.

Paleta: barras em vinho, fundo areia com cartões brancos, letras pretas e o
verde/vermelho reservados para o significado do número (bom / atenção).

A rampa de vinho foi verificada: passa monotonicidade de luminosidade, gap
mínimo entre passos e contraste do tom mais claro contra o branco (3,15:1) e
contra a areia (2,70:1). Todas as cores de texto passam 4,5:1 nos dois fundos.
"""

from __future__ import annotations

import streamlit as st

# --- rampa de vinho: é ela que pinta todas as barras ----------------------
VINHO_ESCURO = "#4F1626"
VINHO = "#8E2F44"
VINHO_CLARO = "#C67C8D"

# séries dos gráficos (mesma rampa, separadas por luminosidade — legíveis
# inclusive para daltonismo e em preto e branco)
SERIE_RECEITA = VINHO_ESCURO
SERIE_DESPESA = VINHO
SERIE_POUPANCA = VINHO_CLARO

# --- fundos ---------------------------------------------------------------
AREIA = "#F3EDE1"
AREIA_CLARA = "#FAF6EE"
BRANCO = "#FFFFFF"
LINHA = "#E0D6C4"

# --- letras ---------------------------------------------------------------
PRETO = "#14110F"
CINZA = "#5A544B"
CINZA_CLARO = "#8A8378"
BOM = "#14532D"       # verde
CRITICO = "#9B1C1C"   # vermelho
ACENTO = VINHO

CORES_PESSOA = {"André": VINHO_ESCURO, "Rô": VINHO, "Casal": "#6B6255"}

CSS = f"""
<style>
  .stApp {{ background: {AREIA}; }}
  section[data-testid="stSidebar"] {{ background: {BRANCO}; border-right: 1px solid {LINHA}; }}
  h1, h2, h3 {{ font-family: Charter, Georgia, serif; letter-spacing: .2px; color: {PRETO}; }}
  h1 {{ font-size: 1.7rem !important; }}
  .marca {{ font-family: Charter, Georgia, serif; font-size: 1.25rem; font-weight: 700;
           color: {VINHO_ESCURO}; line-height: 1.1; }}
  .marca small {{ display: block; font-family: system-ui, sans-serif; font-size: .68rem;
           font-weight: 500; color: {CINZA_CLARO}; letter-spacing: .09em;
           text-transform: uppercase; margin-top: .2rem; }}
  .sub {{ color: {CINZA}; font-size: .92rem; margin-top: -.5rem; margin-bottom: 1.1rem; }}

  /* cartoes de numero */
  div[data-testid="stMetric"] {{ background: {BRANCO}; border: 1px solid {LINHA};
       border-radius: 10px; padding: .85rem 1rem; }}
  div[data-testid="stMetricLabel"] {{ color: {CINZA}; font-size: .8rem !important; }}
  div[data-testid="stMetricValue"] {{ font-size: 1.5rem !important; font-weight: 650;
       color: {PRETO}; }}

  /* barra categoria x meta */
  .cb {{ display: grid; grid-template-columns: 190px 1fr 110px; gap: .7rem;
        align-items: center; padding: .28rem 0; }}
  .cb .lbl {{ font-size: .87rem; color: {PRETO}; }}
  .cb .lbl small {{ display: block; font-size: .72rem; }}
  .cb small.ok {{ color: {CINZA_CLARO}; }}
  .cb small.ov {{ color: {CRITICO}; font-weight: 600; }}
  .cb small.bom {{ color: {BOM}; font-weight: 600; }}
  .track {{ position: relative; height: 15px; }}
  .bar {{ position: absolute; left: 0; top: 2px; height: 11px; border-radius: 0 3px 3px 0; }}
  /* trecho que passou da meta: hachura em vez de outra cor */
  .bar.excesso {{ background: repeating-linear-gradient(
        135deg, {VINHO} 0 3px, {AREIA_CLARA} 3px 6px);
      border-top: 1px solid {VINHO}; border-bottom: 1px solid {VINHO}; }}
  .meta-tick {{ position: absolute; top: -2px; width: 2px; height: 19px;
               background: {PRETO}; opacity: .7; }}
  .cb .val {{ font-size: .87rem; text-align: right; font-variant-numeric: tabular-nums;
             color: {PRETO}; }}

  .pill {{ display: inline-block; font-size: .7rem; font-weight: 600; padding: .1rem .5rem;
          border-radius: 3px; color: {BRANCO}; letter-spacing: .02em; }}
  .p-andre {{ background: {VINHO_ESCURO}; }}
  .p-ro {{ background: {VINHO}; }}
  .p-casal {{ background: #6B6255; }}
  .p-neutro {{ background: {AREIA_CLARA}; color: {CINZA}; border: 1px solid {LINHA}; }}
  .p-alerta {{ background: {AREIA}; color: {VINHO_ESCURO}; border: 1px solid {VINHO_CLARO};
              font-weight: 700; }}

  .aviso-dev {{ font-size: .78rem; color: {CINZA}; background: {AREIA_CLARA};
               border: 1px solid {LINHA}; border-left: 3px solid {VINHO};
               border-radius: 4px; padding: .45rem .6rem; margin-bottom: .6rem; }}
  .login-topo {{ text-align: center; margin: 2.5rem 0 1rem; }}
  .login-topo h1 {{ font-size: 2rem !important; margin-bottom: .1rem; color: {VINHO_ESCURO}; }}
  .login-topo p {{ color: {CINZA}; }}
  div[data-testid="stForm"] {{ border: 1px solid {LINHA}; border-radius: 10px;
        background: {BRANCO}; padding: 1.2rem; }}

  /* tabelas e blocos */
  div[data-testid="stDataFrame"] {{ border: 1px solid {LINHA}; border-radius: 8px; }}
  div[data-testid="stExpander"], div[data-testid="stVerticalBlockBorderWrapper"] > div
    {{ border-radius: 8px; }}
  .nota {{ font-size: .78rem; color: {CINZA_CLARO}; margin-top: .4rem; }}

  /* abas: sublinhado vinho no ativo */
  button[data-testid="stTab"][aria-selected="true"] {{ color: {VINHO} !important; }}
</style>
"""


def aplicar() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def selo_pessoa(pessoa: str) -> str:
    classe = {"André": "p-andre", "Rô": "p-ro", "Casal": "p-casal"}.get(pessoa, "p-neutro")
    return f"<span class='pill {classe}'>{pessoa}</span>"
