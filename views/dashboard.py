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


def _celula(valor: int, nota: str = "", classe: str = "neutro", abre: bool = False) -> str:
    extra = " abre" if abre else ""
    rodape = f"<span class='var {classe}'>{nota}</span>" if nota else ""
    return f"<td class='valor{extra}'>{fmt_brl(valor)}{rodape}</td>"


def _tabela_resumo(competencia, ano, mes, ano_todo, variacao, meses, meta_poupanca) -> str:
    """Receitas, despesas, poupança e sobra — no mês e no ano, lado a lado.

    Quatro cartões só do mês enganavam: um bônus em janeiro fazia o mês parecer
    a regra, e um mês magro parecia um problema. Com as duas colunas na mesma
    linha, dá para ver de uma vez o que aconteceu e o que vinha acontecendo.
    """
    def var_mes(chave: str) -> tuple[str, str]:
        texto = variacao(chave)
        if not texto:
            return "", "neutro"
        subiu = texto.startswith("+")
        # em despesa, subir é ruim; em receita e poupança, é bom
        bom = subiu if chave in ("receitas", "poupanca") else not subiu
        return texto, "sobe" if bom else "desce"

    def nota_da_poupanca() -> tuple[str, str]:
        # na poupança a comparação que importa não é com o mês passado, é com a
        # meta: ela é um compromisso, não uma consequência
        if not meta_poupanca:
            return "sem meta definida", "neutro"
        atingido = mes["poupanca"] / meta_poupanca * 100
        if atingido >= 100:
            return f"✔ {atingido:.0f}% da meta", "sobe"
        return (
            f"{atingido:.0f}% da meta — faltam "
            f"{fmt_brl(meta_poupanca - mes['poupanca'])}", "desce",
        )

    linhas = []
    for chave, rotulo in (
        ("receitas", "Receitas"), ("despesas", "Despesas"), ("poupanca", "Poupança")
    ):
        nota, classe = nota_da_poupanca() if chave == "poupanca" else var_mes(chave)
        rodape_ano = (
            f"meta {fmt_brl(meta_poupanca)}/mês" if chave == "poupanca" and meta_poupanca
            else f"média {fmt_brl(ano_todo[chave] // meses)}/mês"
        )
        linhas.append(
            f"<tr><td class='conta'>{rotulo}</td>"
            + _celula(mes[chave], nota, classe)
            + _celula(ano_todo[chave], rodape_ano, "neutro", abre=True)
            + "</tr>"
        )

    linhas.append(
        "<tr class='fecha'><td class='conta'>Sobra livre</td>"
        + _celula(mes["sobra"], "", "neutro")
        + _celula(ano_todo["sobra"], "", "neutro", abre=True)
        + "</tr>"
    )
    rotulo_mes = f"{graficos.rotulo_mes(competencia)}/{competencia[2:4]}"
    return (
        "<table class='resumo'><thead><tr>"
        "<th></th>"
        f"<th class='grupo'>{rotulo_mes}</th>"
        f"<th class='grupo'>Ano {ano} · acumulado</th>"
        "</tr></thead><tbody>" + "".join(linhas) + "</tbody></table>"
    )


def _competencia_de_abertura(engine, competencias: list[str]) -> str:
    """Em que mês a tela abre.

    A planilha traz lançamento agendado até dezembro, então o mês mais recente
    da base é um mês que ainda não aconteceu: abrir nele mostrava despesa zero
    e um gráfico vazio, como se a casa não tivesse gastado nada. Abre no mês de
    hoje; se ele ainda não tiver gasto lançado, no último que teve.
    """
    hoje = date.today().strftime("%Y-%m")
    if hoje in competencias:
        with engine.connect() as conn:
            if analytics.resumo(conn, competencia=hoje)["despesas"]:
                return hoje
    passados = [c for c in competencias if c <= hoje]
    with engine.connect() as conn:
        for competencia in passados:   # a lista vem do mais recente para o mais antigo
            if analytics.resumo(conn, competencia=competencia)["despesas"]:
                return competencia
    return passados[0] if passados else competencias[0]


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
    inicial = _competencia_de_abertura(engine, competencias)
    competencia = coluna_mes.selectbox(
        "Competência", competencias, index=competencias.index(inicial)
    )
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

    meta_poupanca = next(
        (o["meta"] for o in orcamento if o["categoria"] == analytics.CATEGORIA_POUPANCA), 0
    )
    meses_no_ano = len([m for m in serie if m["receitas"] or m["despesas"]]) or 1
    st.markdown(
        _tabela_resumo(
            competencia, ano, atual, acumulado, variacao, meses_no_ano, meta_poupanca
        ),
        unsafe_allow_html=True,
    )

    estouradas = [o for o in orcamento if o["estourou"]]
    if estouradas:
        st.markdown(
            f"<p class='nota' style='color:{CRITICO};margin-top:-1rem'>"
            f"{len(estouradas)} categoria(s) acima da meta neste mês: "
            + ", ".join(o["categoria"] for o in estouradas)
            + "</p>",
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
    # sem nenhum gasto no mês, listar todas as categorias produzia uma parede de
    # "R$ 0,00" com traços de meta soltos — desenho de gráfico quebrado para
    # dizer uma coisa simples: não teve gasto. Melhor dizer a frase.
    com_gasto = [o for o in orcamento if o["realizado"]]
    if com_gasto:
        com_gasto.sort(key=lambda linha: -linha["realizado"])
        teto = max(max(o["realizado"] for o in com_gasto),
                   max(o["meta"] for o in com_gasto), 1)
        st.markdown(
            "".join(_barra_categoria(item, teto) for item in com_gasto),
            unsafe_allow_html=True,
        )
        sem_gasto = [o["categoria"] for o in orcamento if o["meta"] and not o["realizado"]]
        if sem_gasto:
            st.caption(f"Sem gasto neste mês: {', '.join(sorted(sem_gasto))}.")
    elif competencia > date.today().strftime("%Y-%m"):
        st.info(
            f"**{graficos.rotulo_mes(competencia)} ainda não aconteceu.** O que aparece aqui "
            "são lançamentos já agendados na planilha.",
            icon="📅",
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
