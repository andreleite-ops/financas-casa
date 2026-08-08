"""Identidade visual do app.

Barra lateral em vinho sólido, miolo branco com os blocos em areia, barras em
vinho, letras pretas e o verde/vermelho reservados para o significado do
número (bom / atenção).

Contrastes conferidos: branco sobre o vinho da lateral dá 14,2:1 e a areia
12,2:1; no miolo, preto sobre branco 18,8:1 e sobre areia 16,1:1. O bloco de
areia contra o branco é sutil de propósito (1,17:1), por isso sempre leva uma
borda fina — a separação vem dela, não da cor.
"""

from __future__ import annotations

import streamlit as st

# --- rampa de vinho: é ela que pinta todas as barras ----------------------
VINHO_ESCURO = "#4F1626"
VINHO = "#8E2F44"
VINHO_CLARO = "#C67C8D"
VINHO_LATERAL = "#4A1423"   # fundo da barra lateral

# séries dos gráficos (mesma rampa, separadas por luminosidade — legíveis
# inclusive para daltonismo e em preto e branco)
SERIE_RECEITA = VINHO_ESCURO
SERIE_DESPESA = VINHO
SERIE_POUPANCA = VINHO_CLARO

# --- fundos ---------------------------------------------------------------
BRANCO = "#FFFFFF"
AREIA = "#F3EDE1"
AREIA_CLARA = "#FAF6EE"
LINHA = "#E0D6C4"

# --- letras ---------------------------------------------------------------
PRETO = "#14110F"
CINZA = "#5A544B"
CINZA_CLARO = "#8A8378"
BOM = "#14532D"       # verde
CRITICO = "#9B1C1C"   # vermelho
ACENTO = VINHO

# letras sobre o vinho da lateral
LATERAL_TEXTO = "#F6EFE6"
LATERAL_SUAVE = "#D9BEC5"

CORES_PESSOA = {"André": VINHO_ESCURO, "Rô": VINHO, "Casal": "#6B6255"}

CSS = f"""
<style>
  /* ==================== miolo: branco com areia ==================== */
  .stApp {{ background: {BRANCO}; }}
  h1, h2, h3 {{ font-family: Charter, Georgia, serif; letter-spacing: .2px; color: {PRETO}; }}
  h1 {{ font-size: 1.7rem !important; }}
  .sub {{ color: {CINZA}; font-size: .92rem; margin-top: -.5rem; margin-bottom: 1.1rem; }}

  /* cartoes de numero: areia com borda fina */
  div[data-testid="stMetric"] {{ background: {AREIA}; border: 1px solid {LINHA};
       border-radius: 10px; padding: .85rem 1rem; }}
  div[data-testid="stMetricLabel"] {{ color: {CINZA}; font-size: .8rem !important; }}
  div[data-testid="stMetricValue"] {{ font-size: 1.5rem !important; font-weight: 650;
       color: {PRETO}; }}

  /* blocos com borda: fila de classificacao, duplicidades, contas */
  div[data-testid="stVerticalBlockBorderWrapper"] {{ background: {AREIA_CLARA};
       border-radius: 10px; }}
  div[data-testid="stForm"] {{ border: 1px solid {LINHA}; border-radius: 10px;
        background: {AREIA_CLARA}; padding: 1.2rem; }}
  div[data-testid="stExpander"] details {{ background: {AREIA_CLARA};
        border: 1px solid {LINHA}; border-radius: 10px; }}
  div[data-testid="stDataFrame"] {{ border: 1px solid {LINHA}; border-radius: 8px; }}

  /* ==================== barra lateral: vinho ==================== */
  section[data-testid="stSidebar"] {{ background: {VINHO_LATERAL}; border-right: none; }}
  section[data-testid="stSidebar"] * {{ color: {LATERAL_TEXTO}; }}
  section[data-testid="stSidebar"] > div {{ padding-top: 1.3rem; }}

  /* respiro pedido: 1 linha entre o titulo e "ANDRE & RO", 3 linhas ate o menu */
  .marca {{ font-family: Charter, Georgia, serif; font-size: 1.3rem; font-weight: 700;
           color: {BRANCO} !important; line-height: 1.15; margin-bottom: 3.9rem; }}
  .marca small {{ display: block; font-family: system-ui, sans-serif; font-size: .68rem;
           font-weight: 500; color: {LATERAL_SUAVE} !important; letter-spacing: .11em;
           text-transform: uppercase; margin-top: 1.3rem; }}

  /* navegacao: uma linha de respiro entre os itens */
  section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 0; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
       margin-bottom: .6rem; padding: .32rem .5rem; border-radius: 6px; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{
       background: rgba(255,255,255,.10); }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label p {{ font-size: .93rem; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {{
       border-color: {LATERAL_SUAVE} !important; }}

  section[data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,.18);
       margin: 1.4rem 0; }}
  section[data-testid="stSidebar"] div[data-testid="stCaptionContainer"] p {{
       color: {LATERAL_SUAVE} !important; }}
  section[data-testid="stSidebar"] button {{ background: transparent;
       border: 1px solid rgba(255,255,255,.42); color: {LATERAL_TEXTO}; }}
  section[data-testid="stSidebar"] button:hover {{ background: rgba(255,255,255,.13);
       border-color: {BRANCO}; }}
  section[data-testid="stSidebar"] code {{ background: rgba(255,255,255,.15);
       color: {BRANCO} !important; }}

  .aviso-dev {{ font-size: .76rem; color: {LATERAL_SUAVE} !important;
       background: rgba(0,0,0,.24); border: 1px solid rgba(255,255,255,.16);
       border-left: 3px solid {VINHO_CLARO}; border-radius: 4px;
       padding: .5rem .6rem; margin-bottom: .9rem; line-height: 1.45; }}
  .aviso-dev b {{ color: {BRANCO} !important; }}

  /* ==================== barras e etiquetas ==================== */
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
        135deg, {VINHO} 0 3px, {AREIA} 3px 6px);
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
  .p-neutro {{ background: {AREIA}; color: {CINZA}; border: 1px solid {LINHA}; }}
  .p-alerta {{ background: {AREIA}; color: {VINHO_ESCURO}; border: 1px solid {VINHO_CLARO};
              font-weight: 700; }}

  /* ==================== mapa de carregamento ==================== */
  .mapa {{ overflow-x: auto; border: 1px solid {LINHA}; border-radius: 10px;
          background: {BRANCO}; }}
  .mapa table {{ border-collapse: collapse; font-size: .82rem; width: 100%; }}
  .mapa th {{ font-size: .68rem; text-transform: uppercase; letter-spacing: .05em;
             color: {CINZA_CLARO}; font-weight: 600; padding: .5rem .45rem;
             border-bottom: 1px solid {LINHA}; white-space: nowrap; text-align: center; }}
  .mapa th.conta, .mapa td.conta {{ text-align: left; position: sticky; left: 0;
             background: {BRANCO}; min-width: 190px; padding-left: .8rem;
             border-right: 1px solid {LINHA}; }}
  .mapa td {{ padding: .45rem; text-align: center; border-bottom: 1px solid {AREIA};
             white-space: nowrap; }}
  .mapa tr:last-child td {{ border-bottom: 0; }}
  .mapa .ok {{ color: {BOM}; font-weight: 700; }}
  .mapa .ok small {{ display: block; color: {CINZA_CLARO}; font-weight: 400;
             font-size: .66rem; }}
  .mapa .falta {{ color: {CINZA_CLARO}; }}
  .mapa .dup {{ color: {CRITICO}; font-weight: 700; }}
  .mapa td.futuro {{ background: repeating-linear-gradient(
             135deg, {AREIA_CLARA} 0 4px, {BRANCO} 4px 8px); color: {CINZA_CLARO}; }}
  .mapa .nome {{ color: {PRETO}; }}
  .mapa .tipo {{ display: block; font-size: .66rem; color: {CINZA_CLARO}; }}

  /* ==================== login ==================== */
  .login-topo {{ text-align: center; margin: 2.5rem 0 1rem; }}
  .login-topo h1 {{ font-size: 2rem !important; margin-bottom: .1rem; color: {VINHO_ESCURO}; }}
  .login-topo p {{ color: {CINZA}; }}

  .nota {{ font-size: .78rem; color: {CINZA_CLARO}; margin-top: .4rem; }}
  button[data-testid="stTab"][aria-selected="true"] {{ color: {VINHO} !important; }}
</style>
"""


def aplicar() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def selo_pessoa(pessoa: str) -> str:
    classe = {"André": "p-andre", "Rô": "p-ro", "Casal": "p-casal"}.get(pessoa, "p-neutro")
    return f"<span class='pill {classe}'>{pessoa}</span>"
