"""Plano de contas — visualizar e editar categorias e subcategorias."""

from __future__ import annotations

import streamlit as st

from core import repo


def _bloco(engine, categoria: dict, uso: dict) -> None:
    with st.container(border=True):
        titulo = categoria["nome"] if categoria["ativa"] else f"{categoria['nome']} (inativa)"
        st.markdown(f"**{titulo}**")
        subs = [s for s in categoria["subcategorias"] if s["ativa"]]
        if subs:
            st.markdown(
                " ".join(
                    f"<span class='pill p-neutro'>{s['nome']}</span>" for s in subs
                ),
                unsafe_allow_html=True,
            )
        else:
            st.caption("Sem subcategorias.")
        if uso["lancamentos"]:
            st.markdown(
                f"<span class='nota'>{uso['lancamentos']} lançamento(s) classificado(s) "
                "aqui</span>", unsafe_allow_html=True,
            )

        with st.expander("Editar"):
            c1, c2 = st.columns([2, 1])
            novo_nome = c1.text_input(
                "Nome da categoria", value=categoria["nome"], key=f"nome{categoria['id']}"
            )
            ativa = c2.checkbox("Ativa", value=categoria["ativa"], key=f"ativa{categoria['id']}")
            if c1.button("Salvar categoria", key=f"savecat{categoria['id']}"):
                repo.salvar_categoria(
                    engine, categoria_id=categoria["id"], nome=novo_nome,
                    natureza=categoria["natureza"], ativa=ativa,
                )
                st.success("Categoria atualizada.")
                st.rerun()

            nova_sub = st.text_input(
                "Nova subcategoria", key=f"novasub{categoria['id']}",
                placeholder="Ex.: Delivery",
            )
            if st.button("Incluir subcategoria", key=f"addsub{categoria['id']}"):
                if nova_sub.strip():
                    repo.salvar_subcategoria(
                        engine, categoria_id=categoria["id"], nome=nova_sub
                    )
                    st.success(f"Subcategoria “{nova_sub}” incluída.")
                    st.rerun()
                else:
                    st.error("Digite o nome da subcategoria.")

            if subs:
                st.markdown("**Apagar subcategoria**")
                alvo = st.selectbox(
                    "Qual", subs, format_func=lambda s: s["nome"],
                    key=f"delsubsel{categoria['id']}", label_visibility="collapsed",
                )
                if st.button("Apagar subcategoria", key=f"delsub{categoria['id']}"):
                    if repo.excluir_subcategoria(engine, alvo["id"]):
                        st.success(f"“{alvo['nome']}” apagada.")
                        st.rerun()
                    else:
                        st.error(
                            f"“{alvo['nome']}” tem lançamentos classificados nela. "
                            "Reclassifique-os primeiro, na tela Classificação."
                        )

            st.divider()
            # apagar de vez só quando nada depende dela: senão o total do mês
            # mudaria sozinho e ninguém saberia por quê
            if uso["pode_apagar"]:
                st.markdown("**Apagar esta categoria**")
                st.caption(
                    "Nada depende dela — nenhum lançamento, regra ou tradução. "
                    "Some junto com suas subcategorias."
                )
                if st.button("Apagar categoria", key=f"delcat{categoria['id']}",
                             type="primary"):
                    repo.excluir_categoria(engine, categoria["id"])
                    st.session_state["msg_plano"] = f"Categoria “{categoria['nome']}” apagada."
                    st.rerun()
            else:
                impedimentos = []
                if uso["lancamentos"]:
                    impedimentos.append(f"{uso['lancamentos']} lançamento(s)")
                if uso["regras"]:
                    impedimentos.append(f"{uso['regras']} regra(s)")
                if uso["traducoes"]:
                    impedimentos.append(f"{uso['traducoes']} tradução(ões) de rótulo")
                st.caption(
                    f"**Não dá para apagar:** {', '.join(impedimentos)} dependem desta "
                    "categoria. Apagar deixaria esses lançamentos sem gaveta e mudaria o "
                    "total do mês sem ninguém pedir. Para tirá-la do caminho, desmarque "
                    "**Ativa** acima — ela some das listas e o histórico fica de pé."
                )


def render(engine, usuario: dict) -> None:
    st.caption(
        "Todo lançamento carrega também a pessoa: André, Rô ou Casal. "
        "Renomear uma categoria preserva o histórico já classificado nela."
    )

    if st.session_state.pop("msg_plano", None):
        st.success(st.session_state.get("msg_plano", "Salvo."), icon="🗂️")

    with engine.connect() as conn:
        receitas = repo.plano_de_contas(conn, natureza="receita")
        despesas = repo.plano_de_contas(conn, natureza="despesa")
        # o uso de cada categoria numa passada só, para o bloco saber se pode
        # oferecer o botão de apagar sem consultar o banco categoria a categoria
        usos = {
            c["id"]: repo.uso_da_categoria(conn, c["id"])
            for c in (*receitas, *despesas)
        }

    aba_desp, aba_rec = st.tabs(
        [f"Despesas ({len(despesas)})", f"Receitas ({len(receitas)})"]
    )

    with aba_desp:
        colunas = st.columns(2)
        for i, categoria in enumerate(despesas):
            with colunas[i % 2]:
                _bloco(engine, categoria, usos[categoria['id']])
        with st.expander("➕ Nova categoria de despesa"):
            nome = st.text_input("Nome", key="nova_desp")
            if st.button("Criar categoria de despesa"):
                if nome.strip():
                    repo.salvar_categoria(engine, nome=nome, natureza="despesa")
                    st.success(f"Categoria “{nome}” criada.")
                    st.rerun()
                else:
                    st.error("Digite o nome da categoria.")

    with aba_rec:
        colunas = st.columns(2)
        for i, categoria in enumerate(receitas):
            with colunas[i % 2]:
                _bloco(engine, categoria, usos[categoria['id']])
        with st.expander("➕ Nova categoria de receita"):
            nome = st.text_input("Nome", key="nova_rec")
            if st.button("Criar categoria de receita"):
                if nome.strip():
                    repo.salvar_categoria(engine, nome=nome, natureza="receita")
                    st.success(f"Categoria “{nome}” criada.")
                    st.rerun()
                else:
                    st.error("Digite o nome da categoria.")
