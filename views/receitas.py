"""Receitas — quanto cada um trouxe, de onde, em cada mês."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import analytics, db
from core.money import fmt_brl, fmt_brl_md, fmt_mil
from ui import dados, graficos
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


ROTULO_ORIGEM = {
    "manual": "Lançado à mão (previsão)",
    "planilha": "Carga inicial (planilha)",
    "extrato": "Extrato do banco",
}


def _de_onde_veio(composicao: list[dict], rotulo: str, dobradas: list[dict]) -> None:
    """A quebra da receita por origem, e o alerta de renda contada duas vezes.

    O cartão do topo diz quanto entrou; ele nunca disse de onde, e é essa a
    pergunta quando o número parece grande demais. Renda dobrada tem uma
    assinatura estreita: a mesma pessoa, no mesmo mês já fechado, com a
    previsão e o extrato valendo ao mesmo tempo. Somar o ano inteiro não
    serve — a planilha é a verdade até julho, o extrato é a de agosto, e o
    alarme acusava 1,8 milhão de dobra num ano sem dobra nenhuma.
    """
    if not composicao:
        return

    por_origem: dict[str, dict] = {}
    for linha in composicao:
        if not linha["no_total"]:
            continue
        acumulado = por_origem.setdefault(linha["origem"], {"total": 0, "quantos": 0})
        acumulado["total"] += linha["total"]
        acumulado["quantos"] += linha["quantos"]

    if dobradas:
        casos = " · ".join(
            f"**{graficos.rotulo_mes(d['competencia'])}/{d['competencia'][2:4]}**, "
            f"{d['pessoa']}: {fmt_brl_md(d['previsto'])} previstos e "
            f"{fmt_brl_md(d['realizado'])} do extrato"
            for d in dobradas[:6]
        )
        st.error(
            "**Esta renda pode estar contada duas vezes.** Num mês que já fechou, a "
            "previsão da pessoa e o extrato dela estão valendo ao mesmo tempo: "
            f"{casos}. O upload casa sozinho o que cai no mesmo mês com valor "
            "parecido; o que sobrou aqui não casou. Se for o mesmo dinheiro, desative "
            "a previsão: a lançada à mão em **Upload → Lançar à mão**, com **Apagar**; "
            "a da planilha, aqui embaixo em **Lançamentos de receita**, com **Desativar**.",
            icon="🚨",
        )

    with st.expander(f"De onde veio a receita de {rotulo}", expanded=bool(dobradas)):
        st.caption(
            "A mesma conta do cartão lá em cima, aberta por origem. **Quantos** importa tanto "
            "quanto o valor: trinta créditos pequenos são pacientes; um crédito só é salário."
        )
        st.dataframe(
            pd.DataFrame([
                {
                    "Origem": ROTULO_ORIGEM.get(linha["origem"], linha["origem"]),
                    "Conta": linha["conta"],
                    "Tipo": linha["categoria"],
                    "Lançamentos": linha["quantos"],
                    "Período": (
                        f"{linha['primeiro']:%d/%m}"
                        if linha["primeiro"] == linha["ultimo"]
                        else f"{linha['primeiro']:%d/%m} a {linha['ultimo']:%d/%m}"
                    ),
                    "Valor": fmt_brl(linha["total"]),
                    "No total": "sim" if linha["no_total"] else "não (transferência)",
                }
                for linha in composicao
            ]),
            width="stretch", hide_index=True,
        )


def render(engine, usuario: dict) -> None:
    competencias = dados.competencias(engine, dados.versao())
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

    painel = dados.painel_de_receitas(
        engine, dados.versao(), ano, filtro.get("competencia")
    )
    total, por_pessoa = painel["total"], painel["por_pessoa"]
    matriz, itens = painel["matriz"], painel["itens"]

    _cartoes_por_pessoa(por_pessoa, total)
    _de_onde_veio(painel["composicao"], rotulo, painel["dobradas"])
    # o formulario de lancar a mao mora numa tela so (Upload → Lançar à mão);
    # aqui ele era uma copia, com a lista e o Apagar duas vezes
    st.caption("Para lançar uma receita à mão, use **Upload → ✍️ Lançar à mão**.")

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

    st.caption(
        "Escolha o tipo para ver só o que está dentro dele. Cada lançamento traz o "
        "editor: corrija a categoria, a pessoa, ou **Desative** o que for a mesma "
        "receita vinda de duas fontes."
    )
    if recado := st.session_state.pop("msg_classificacao", None):
        st.success(recado)
    plano = dados.plano_de_contas(engine, dados.versao())
    pessoas = sorted({item["pessoa"] for item in itens})
    abas = st.tabs([f"{p} ({sum(1 for i in itens if i['pessoa'] == p)})" for p in pessoas])
    for aba, pessoa in zip(abas, pessoas):
        with aba:
            _lancamentos_da_pessoa(
                engine, usuario, pessoa,
                [item for item in itens if item["pessoa"] == pessoa], plano,
            )


SEM_TIPO = "— sem tipo —"
# quantos editores cabem numa aba sem a tela ficar pesada; o resto sai pelo
# filtro de tipo, que e como se chega ao lancamento especifico
POR_VEZ = 40


def _lancamentos_da_pessoa(engine, usuario: dict, pessoa: str, itens: list[dict],
                           plano: list[dict]) -> None:
    """Os lançamentos de receita de uma pessoa, com o editor em cada um.

    O mesmo que a Visão Geral faz com a despesa: ver o que está dentro do
    número, linha a linha, e corrigir ali mesmo. Na receita isso vale ainda
    mais, porque é aqui que se desativa a previsão que ficou valendo ao lado
    do extrato — o dinheiro que o alerta lá em cima acusa.
    """
    from views.classificacao import _editor

    tipos = sorted({i["subcategoria"] or i["categoria"] or SEM_TIPO for i in itens})
    escolha = st.selectbox(
        "Tipo", ["Todos", *tipos], key=f"rec_tipo_{pessoa}",
        help="Filtra esta lista. O total ao lado acompanha o filtro.",
    )
    filtrados = (itens if escolha == "Todos"
                 else [i for i in itens
                       if (i["subcategoria"] or i["categoria"] or SEM_TIPO) == escolha])
    filtrados = sorted(filtrados, key=lambda i: -i["valor_centavos"])
    st.markdown(
        f"{selo_pessoa(pessoa)} &nbsp; "
        f"**{fmt_brl(sum(i['valor_centavos'] for i in filtrados))}** "
        f"em {len(filtrados)} lançamento(s)",
        unsafe_allow_html=True,
    )
    with st.expander("Tabela", expanded=False):
        st.dataframe(
            pd.DataFrame([
                {
                    "Data": f"{i['data']:%d/%m/%Y}",
                    "Descrição": i["descricao"],
                    "Tipo": i["subcategoria"] or i["categoria"] or "—",
                    "Conta": i["conta"],
                    "Valor": fmt_brl(i["valor_centavos"]),
                    "Arquivo": i["arquivo"] or i["origem"],
                }
                for i in filtrados
            ]),
            width="stretch", hide_index=True,
        )
    for item in filtrados[:POR_VEZ]:
        _editor(engine, usuario, item, plano, prefixo="rec")
    if len(filtrados) > POR_VEZ:
        st.caption(f"Mostrando os {POR_VEZ} maiores de {len(filtrados)}. Filtre pelo tipo "
                   "para chegar aos demais.")
