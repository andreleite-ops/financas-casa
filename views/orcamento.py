"""Orçamento & Metas — % da renda por categoria, com a Poupança como meta."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import repo
from core.money import fmt_brl
from ui import dados
from ui.tema import ACENTO, CRITICO


JANELAS = {"Mês": "mes", "Ano civil": "ano", "Últimos 12 meses": "12m"}


def _meses_do_ano(competencias, ano: int) -> tuple[list[str], str]:
    """Os meses do ano em ordem, e em qual a tela abre.

    Abre no mês em curso, ou no último que já aconteceu — nunca no mais
    recente da base, porque a planilha traz previsão até dezembro e
    "realizado em dezembro" seria previsão apresentada como gasto.

    A ordem crescente não é estética: a lista do banco vem do mais recente
    para o mais antigo, e pegar "o último que já aconteceu" dela abria a tela
    em janeiro. Com a janela do ano, o período inteiro valia um mês só.
    """
    do_ano = sorted(c for c in competencias if c.startswith(str(ano))) or [f"{ano}-01"]
    hoje = date.today().strftime("%Y-%m")
    ja_aconteceram = [c for c in do_ano if c <= hoje]
    return do_ano, (ja_aconteceram[-1] if ja_aconteceram else do_ano[-1])
# metade do gasto do período num mês só: a média mensal daquela categoria não
# descreve mês nenhum, e dizer isso vale mais do que a média
CONCENTRADO = 50


def _quando(linha: dict, escopo: str, meses: int) -> str:
    """Em que meses do período o gasto aconteceu, em uma linha."""
    if escopo == "mes":
        return "—"
    houve = linha.get("meses_com_gasto", 0)
    if not houve:
        return "não houve gasto"
    if linha.get("concentracao", 0) >= CONCENTRADO and meses > 1:
        return (f"{linha['concentracao']}% em {linha['pico_mes']} · "
                f"{houve} de {meses} meses")
    return f"{houve} de {meses} meses · {fmt_brl(linha.get('ritmo', 0))}/mês"


def _fechamento_do_periodo(por_id, renda_base: int, total_pct: float, meses: int,
                           periodo: str) -> None:
    """O período inteiro em duas linhas: o que coube e o que não coube.

    É a conta que o mês não responde. Uma categoria pode estourar o mês da
    viagem em 2.700% e ainda assim caber no ano — e outra pode passar de pouco
    todo mês e estourar o ano sem nunca ter acendido um alarme mensal.
    """
    linhas = [l for l in por_id.values() if l.get("meta")]
    if not linhas:
        return
    meta_total = sum(l["meta"] for l in linhas)
    realizado_total = sum(l["realizado"] for l in linhas)
    estouraram = [l for l in linhas if l.get("estourou")]
    recado = (
        f"**No período ({periodo}), com renda de {fmt_brl(renda_base)}/mês:** "
        f"meta de {fmt_brl(meta_total)} e realizado de {fmt_brl(realizado_total)} "
        f"({realizado_total / meta_total * 100:.0f}% da meta do período)."
    )
    if estouraram:
        nomes = ", ".join(
            f"{l['categoria']} ({l['uso']:.0f}%)"
            for l in sorted(estouraram, key=lambda l: -(l["realizado"] - l["meta"]))[:4]
        )
        recado += f" Passaram da meta no período: {nomes}."
    concentradas = [l for l in linhas if l.get("concentracao", 0) >= CONCENTRADO
                    and l["meses_com_gasto"] > 0 and meses > 1]
    if concentradas:
        nomes = ", ".join(
            f"{l['categoria']} ({l['concentracao']}% em {l['pico_mes']})"
            for l in sorted(concentradas, key=lambda l: -l["realizado"])[:3]
        )
        recado += (f" Concentradas num mês só: {nomes} — nelas o alarme mensal não "
                   "diz nada, e o do período diz.")
    if realizado_total > meta_total:
        st.error(recado)
    else:
        st.success(recado)
    if abs(total_pct - 100) > 0.01:
        st.caption(
            "As metas somam "
            f"{total_pct:.1f}% da renda; o período compara contra elas, não contra 100%."
        )


def render(engine, usuario: dict) -> None:
    competencias = dados.competencias(engine, dados.versao())
    anos = sorted({int(c[:4]) for c in competencias}, reverse=True) or [date.today().year]
    plano = dados.plano_de_contas(engine, dados.versao(), natureza="despesa")

    c1, c2, c3 = st.columns([1, 1.2, 1.6])
    ano = c1.selectbox("Ano", anos)
    do_ano, inicial = _meses_do_ano(competencias, ano)
    competencia = c2.selectbox("Mês de referência", do_ano, index=do_ano.index(inicial))

    # metas e média do ano, guardadas até alguém gravar: eram sete idas ao
    # banco por toque de campo nesta tela
    painel = dados.metas_do_ano(engine, dados.versao(), ano)
    metas = painel["metas"]
    media = painel["media"]
    meses_com_dado = len({c for c in competencias if c.startswith(str(ano))}) or 1
    # venda de bem fica de fora por não se repetir, não por valer menos:
    # meia dúzia de meses de meta em % não pode se apoiar num ganho único
    renda_media = media["renda_recorrente"] // meses_com_dado
    # a renda com que a casa decidiu planejar é decisão, não média: ela é
    # gravada. Enquanto não houver uma gravada, a média do ano serve de
    # sugestão — antes ela voltava a mandar a cada visita, e as metas em reais
    # mudavam sozinhas como se nada tivesse sido salvo
    renda_salva = painel.get("renda_base")
    base = c3.number_input(
        "Renda mensal considerada (R$)",
        min_value=0.0, step=500.0,
        value=float((renda_salva if renda_salva is not None else renda_media) / 100),
        key=f"renda_base_{ano}",
        help="É ela que transforma a meta em % em reais. Fica salva com as metas; "
             "enquanto não houver uma salva, vale a média da renda do ano.",
    )
    renda_base = int(round(base * 100))
    if renda_salva is None:
        c3.markdown(
            f"<span class='nota'>Sugestão: média de {ano}. Clique em salvar para "
            "fixar a renda com que vocês querem planejar.</span>",
            unsafe_allow_html=True,
        )
    elif renda_salva != renda_base:
        c3.markdown(
            f"<span class='nota'>Salva: {fmt_brl(renda_salva)}/mês. Salve de novo "
            "para trocar.</span>", unsafe_allow_html=True,
        )

    janela = st.radio(
        "Comparar com", list(JANELAS), horizontal=True, key="orc_janela",
        help="No mês, no ano civil ou nos últimos doze meses. Há gasto que não cabe "
             "num mês: a viagem do ano inteiro acontece em julho, e só a soma do "
             "período diz se ela coube no orçamento.",
    )
    escopo = JANELAS[janela]

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
    if escopo == "mes":
        por_id = {linha["categoria_id"]: linha for linha in completo["realizado"]}
        meses_do_periodo, periodo = 1, competencia
    else:
        do_periodo = dados.orcamento_do_periodo(
            engine, dados.versao(), competencia, escopo, ano, renda_base
        )
        por_id = {linha["categoria_id"]: linha for linha in do_periodo["linhas"]}
        janela_meses = do_periodo["competencias"]
        meses_do_periodo = len(janela_meses) or 1
        periodo = (f"{janela_meses[0]} a {janela_meses[-1]}" if janela_meses else competencia)
        st.caption(
            f"Somando {meses_do_periodo} "
            f"{'meses' if meses_do_periodo > 1 else 'mês'} ({periodo}). A meta do "
            "período é a meta mensal repetida pelos meses — é contra ela que o gasto "
            "concentrado num mês só tem de ser medido."
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

    larguras = [2.1, 0.9, 1.2, 1.7, 1.1, 1.3]
    with st.form("metas"):
        novos: dict[int, float] = {}
        cabecalho = st.columns(larguras)
        titulos = [
            "Categoria", "% meta",
            "Meta no mês" if escopo == "mes" else f"Meta em {meses_do_periodo} meses",
            f"Realizado em {competencia}" if escopo == "mes" else f"Realizado em {periodo}",
            "Uso", "Quando aconteceu",
        ]
        for coluna, titulo in zip(cabecalho, titulos):
            coluna.markdown(f"<span class='nota'><b>{titulo}</b></span>", unsafe_allow_html=True)

        ordenadas = sorted(
            [cat for cat in plano if cat["ativa"]],
            key=lambda cat: -metas.get(cat["id"], 0),
        )
        for categoria in ordenadas:
            linha = por_id.get(
                categoria["id"],
                {"meta": 0, "realizado": 0, "uso": None, "meta_e_piso": False},
            )
            colunas = st.columns(larguras)
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
            meta_valor = int(round(renda_base * novos[categoria["id"]] / 100)) * meses_do_periodo
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
            # onde o gasto aconteceu dentro do período: é a resposta para a
            # categoria que estourou o mês da viagem e cabe no ano
            colunas[5].markdown(
                f"<span class='nota'>{_quando(linha, escopo, meses_do_periodo)}</span>",
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

    if escopo != "mes":
        _fechamento_do_periodo(por_id, renda_base, total, meses_do_periodo, periodo)

    if salvou:
        repo.salvar_metas(engine, ano, novos)
        repo.salvar_renda_base(engine, ano, renda_base)
        st.success(
            f"Metas salvas, com a renda de {fmt_brl(renda_base)} por mês. "
            "As duas coisas voltam assim na próxima visita."
        )
        st.rerun()
