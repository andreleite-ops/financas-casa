"""Orçamento & Metas — % da renda por categoria, com a Poupança como meta."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import repo
from core.money import fmt_brl
from ui import dados
from ui.tema import ACENTO, CRITICO


def render(engine, usuario: dict) -> None:
    competencias = dados.competencias(engine, dados.versao())
    anos = sorted({int(c[:4]) for c in competencias}, reverse=True) or [date.today().year]
    plano = dados.plano_de_contas(engine, dados.versao(), natureza="despesa")

    c1, c2, c3 = st.columns([1, 1.2, 1.6])
    ano = c1.selectbox("Ano", anos)
    do_ano = [c for c in competencias if c.startswith(str(ano))] or [f"{ano}-01"]
    competencia = c2.selectbox("Comparar com o mês", do_ano)

    # metas e média do ano, guardadas até alguém gravar: eram sete idas ao
    # banco por toque de campo nesta tela
    painel = dados.metas_do_ano(engine, dados.versao(), ano)
    metas = painel["metas"]
    media = painel["media"]
    meses_com_dado = len({c for c in competencias if c.startswith(str(ano))}) or 1
    # venda de bem fica de fora por não se repetir, não por valer menos:
    # meia dúzia de meses de meta em % não pode se apoiar num ganho único
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
            f"em {ano} — ganho de verdade, e está lá nas Receitas. Fica fora daqui só por "
            "não se repetir: uma entrada única não pode definir a meta de todo mês."
        )

    st.caption(
        "Defina quanto por cento da renda vai para cada categoria. A Poupança entra como "
        "meta, não como sobra — é o que garante que ela aconteça."
    )

    # o histórico como ponto de partida: a casa já mostrou, mês a mês, quanto
    # cada conta come. Não é a meta — é o retrato de onde se está, e é dele que
    # se decide o que mudar. Um orçamento que começa em zero não é preenchido.
    completo = dados.realizado_do_orcamento(
        engine, dados.versao(), ano, competencia, renda_base
    )
    sugestao = completo["sugestao"]
    if sugestao:
        c1, c2 = st.columns([3, 1.2])
        total_sugerido = sum(sugestao.values())
        c1.info(
            f"**Sugestão a partir do que vocês gastam:** as médias de {ano} somam "
            f"{total_sugerido:.0f}% da renda considerada. Use como ponto de partida e "
            "ajuste o que quiser mudar — quem decide a meta são vocês, o histórico só "
            "diz de onde se está saindo.",
            icon="📊",
        )
        if c2.button("Preencher com as médias", width="stretch"):
            for categoria_id, pct in sugestao.items():
                st.session_state[f"meta{categoria_id}"] = float(pct)
            st.session_state["msg_orcamento"] = (
                f"{len(sugestao)} metas preenchidas com a média de {ano}. "
                "Nada foi salvo ainda — revise e clique em salvar."
            )
            st.rerun()
    recado = st.session_state.pop("msg_orcamento", None)
    if recado:
        st.success(recado, icon="📊")

    with st.form("metas"):
        novos: dict[int, float] = {}
        cabecalho = st.columns([2.1, 0.9, 1.1, 1.9, 1.1])
        for coluna, titulo in zip(
            cabecalho, ["Categoria", "% meta", "Meta R$", f"Realizado em {competencia}", "Uso"]
        ):
            coluna.markdown(f"<span class='nota'><b>{titulo}</b></span>", unsafe_allow_html=True)

        realizado = completo["realizado"]
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
                value=float(st.session_state.get(
                    f"meta{categoria['id']}", metas.get(categoria["id"], 0.0)
                )),
                key=f"meta{categoria['id']}", label_visibility="collapsed",
                help=(f"A média de {ano} é {sugestao[categoria['id']]:.1f}%"
                      if categoria["id"] in sugestao else None),
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
