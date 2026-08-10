"""Orçamento & Metas — % da renda por categoria, com a Poupança como meta."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import analytics, repo
from core.money import fmt_brl
from ui.tema import ACENTO, CRITICO


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        competencias = repo.competencias_disponiveis(conn)
        anos = sorted({int(c[:4]) for c in competencias}, reverse=True) or [date.today().year]
        plano = repo.plano_de_contas(conn, natureza="despesa")

    c1, c2, c3 = st.columns([1, 1.2, 1.6])
    ano = c1.selectbox("Ano", anos)
    do_ano = [c for c in competencias if c.startswith(str(ano))] or [f"{ano}-01"]
    competencia = c2.selectbox("Comparar com o mês", do_ano)

    with engine.connect() as conn:
        metas = repo.listar_metas(conn, ano)
        media = analytics.resumo(conn, ano=ano)
        meses_com_dado = len({c for c in competencias if c.startswith(str(ano))}) or 1
        # venda de bem fica de fora: um apartamento vendido uma vez não é renda
        # mensal, e se entrasse aqui deixaria as metas em % frouxas o ano inteiro
        renda_media = media["renda_recorrente"] // meses_com_dado

    base = c3.number_input(
        "Renda mensal considerada (R$)",
        min_value=0.0, step=500.0, value=float(renda_media / 100),
        help="Sugerido: média da renda do ano, sem venda de bens. Ajuste se quiser "
             "planejar por outro valor.",
    )
    renda_base = int(round(base * 100))

    if media["receitas_nao_recorrentes"]:
        st.caption(
            f"Fora da base: {fmt_brl(media['receitas_nao_recorrentes'])} de **venda de bens** "
            f"em {ano}. O dinheiro entrou e aparece nas Receitas, mas não é renda mensal — "
            "usá-lo aqui afrouxaria as metas do ano inteiro."
        )

    st.caption(
        "Defina quanto por cento da renda vai para cada categoria. A Poupança entra como "
        "meta, não como sobra — é o que garante que ela aconteça."
    )

    with st.form("metas"):
        novos: dict[int, float] = {}
        cabecalho = st.columns([2.1, 0.9, 1.1, 1.9, 1.1])
        for coluna, titulo in zip(
            cabecalho, ["Categoria", "% meta", "Meta R$", f"Realizado em {competencia}", "Uso"]
        ):
            coluna.markdown(f"<span class='nota'><b>{titulo}</b></span>", unsafe_allow_html=True)

        with engine.connect() as conn:
            realizado = analytics.orcamento(conn, competencia, metas, renda_base=renda_base)
        por_id = {linha["categoria_id"]: linha for linha in realizado}

        ordenadas = sorted(
            [cat for cat in plano if cat["ativa"]],
            key=lambda cat: -metas.get(cat["id"], 0),
        )
        for categoria in ordenadas:
            linha = por_id.get(
                categoria["id"],
                {"meta": 0, "realizado": 0, "uso": None, "meta_e_piso": False},
            )
            colunas = st.columns([2.1, 0.9, 1.1, 1.9, 1.1])
            colunas[0].markdown(categoria["nome"])
            novos[categoria["id"]] = colunas[1].number_input(
                categoria["nome"], min_value=0.0, max_value=100.0, step=0.5,
                value=float(metas.get(categoria["id"], 0.0)),
                key=f"meta{categoria['id']}", label_visibility="collapsed",
            )
            meta_valor = int(round(renda_base * novos[categoria["id"]] / 100))
            colunas[2].markdown(
                f"<span class='nota'>{fmt_brl(meta_valor)}</span>", unsafe_allow_html=True
            )
            uso = (linha["realizado"] / meta_valor * 100) if meta_valor else None
            # poupança: meta é piso — ficar abaixo é que preocupa
            if linha.get("meta_e_piso"):
                fora_da_meta = bool(uso is not None and uso < 100)
            else:
                fora_da_meta = bool(uso is not None and uso > 100)
            cor = CRITICO if fora_da_meta else ACENTO
            largura = min(uso or 0, 100)
            colunas[3].markdown(
                f"<div style='height:8px;background:#e3e4da;border-radius:99px;overflow:hidden'>"
                f"<div style='height:100%;width:{largura:.0f}%;background:{cor};"
                f"border-radius:99px'></div></div>",
                unsafe_allow_html=True,
            )
            colunas[4].markdown(
                f"<span class='nota' style='color:{cor if fora_da_meta else '#575a4f'}'>"
                f"{f'{uso:.0f}%' if uso is not None else '—'} · "
                f"{fmt_brl(linha['realizado'])}</span>",
                unsafe_allow_html=True,
            )

        total = sum(novos.values())
        salvou = st.form_submit_button("Salvar metas", type="primary")

    if abs(total - 100) < 0.01:
        st.success(f"Total alocado: {total:.1f}% da renda.", icon="✔️")
    elif total > 100:
        st.error(
            f"Total alocado: {total:.1f}% — passou de 100%. "
            f"Sobra negativa de {fmt_brl(int(renda_base * (total - 100) / 100))} por mês."
        )
    else:
        st.info(
            f"Total alocado: {total:.1f}% — sobram {100 - total:.1f}% "
            f"({fmt_brl(int(renda_base * (100 - total) / 100))}) sem destino definido."
        )

    if salvou:
        repo.salvar_metas(engine, ano, novos)
        st.success("Metas salvas.")
        st.rerun()
