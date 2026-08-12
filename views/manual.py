"""Lançamento digitado à mão — o que não passa por extrato nenhum.

Duas necessidades, a mesma tela. A Rô recebe dos pacientes em dezenas de
valores pequenos, e um total por mês responde a mesma pergunta com uma linha.
E há gasto que extrato nenhum explica: os euros comprados em espécie saem da
conta como um saque e viram uma viagem — só quem gastou sabe disso.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import db, repo
from core.money import fmt_brl
from ui.tema import selo_pessoa

ROTULOS = {
    "despesa": {
        "titulo": "➖ Lançar despesa à mão",
        "ajuda": "Para o gasto que não aparece em extrato: dinheiro em espécie, euros "
                 "comprados para a viagem, a parte de uma conta dividida com alguém.",
        "descricao": "COMPRA DE EUROS",
        "verbo": "Gastei",
    },
    "receita": {
        "titulo": "➕ Lançar receita à mão",
        "ajuda": "Para o que chega picado e não vale a pena importar linha a linha — os "
                 "atendimentos da Rô, por exemplo. Um lançamento por mês, com o total.",
        "descricao": "ATENDIMENTOS",
        "verbo": "Recebi",
    },
}


def _destinos(plano, natureza: str) -> dict[str, tuple[int, int | None] | None]:
    """Categoria e subcategoria numa lista só, achatada.

    Dois campos encadeados obrigam a escolher, esperar a tela recarregar e só
    então ver as subcategorias. Aqui o destino inteiro é uma escolha.
    """
    saida: dict[str, tuple[int, int | None] | None] = {"— escolher —": None}
    for categoria in plano:
        if categoria["natureza"] != natureza or not categoria["ativa"]:
            continue
        saida[categoria["nome"]] = (categoria["id"], None)
        for sub in categoria["subcategorias"]:
            if sub["ativa"]:
                saida[f"    ↳ {categoria['nome']} › {sub['nome']}"] = (
                    categoria["id"], sub["id"]
                )
    return saida


def formulario(engine, usuario: dict, ano: int, natureza: str) -> None:
    rotulo = ROTULOS[natureza]
    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn, natureza=natureza)
        ja_lancados = repo.lancamentos_manuais(conn, ano, natureza=natureza)

    with st.expander(rotulo["titulo"], expanded=not ja_lancados):
        st.caption(rotulo["ajuda"])
        destinos = _destinos(plano, natureza)

        with st.form(f"manual_{natureza}", clear_on_submit=True):
            c1, c2, c3 = st.columns([1, 1, 1.2])
            meses = [f"{ano}-{m:02d}" for m in range(1, 13)]
            competencia = c1.selectbox(
                "Mês", meses, index=min(date.today().month, 12) - 1,
                key=f"m_mes_{natureza}",
                help="É este mês que manda no relatório.",
            )
            pessoa = c2.selectbox(
                "De quem é", db.PESSOAS,
                index=2 if natureza == "despesa" else 1,
                key=f"m_pessoa_{natureza}",
                help="Gasto da casa fica como Casal — é o padrão do sistema."
                     if natureza == "despesa" else None,
            )
            valor = c3.number_input(
                f"{rotulo['verbo']} (R$)", min_value=0.0, step=100.0, format="%.2f",
                key=f"m_valor_{natureza}",
                help="Digite sempre positivo. O sinal sai da natureza do lançamento.",
            )
            escolha = st.selectbox("Vai para", list(destinos), key=f"m_dest_{natureza}")
            descricao = st.text_input(
                "Descrição", value=rotulo["descricao"], key=f"m_desc_{natureza}",
                help="Como vai aparecer nas listas, igual à descrição de um extrato.",
            )

            if st.form_submit_button("Lançar", type="primary"):
                destino = destinos[escolha]
                if valor <= 0:
                    st.error("Informe um valor maior que zero.")
                elif destino is None:
                    st.error("Escolha para onde vai.")
                else:
                    centavos = int(round(valor * 100))
                    repo.lancar_manual(
                        engine, competencia=competencia, valor_centavos=centavos,
                        pessoa=pessoa, categoria_id=destino[0], subcategoria_id=destino[1],
                        descricao=descricao, usuario=usuario["nome"], natureza=natureza,
                    )
                    st.success(f"{fmt_brl(centavos)} lançado em {competencia} para {pessoa}.")
                    st.rerun()

        if ja_lancados:
            st.markdown(f"**Já lançado à mão em {ano}**")
            for item in ja_lancados:
                linha, botao = st.columns([5, 1])
                destino = item["subcategoria"] or item["categoria"] or "—"
                linha.markdown(
                    f"{item['competencia']} &nbsp; {selo_pessoa(item['pessoa'])} &nbsp; "
                    f"{item['descricao']} — **{fmt_brl(abs(item['valor_centavos']))}** "
                    f"<span class='nota'>({destino})</span>",
                    unsafe_allow_html=True,
                )
                if botao.button("Apagar", key=f"del_manual_{item['id']}"):
                    repo.excluir_transacao(engine, item["id"])
                    st.rerun()
