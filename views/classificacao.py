"""Fila de pendências e reclassificação de qualquer lançamento."""

from __future__ import annotations

import streamlit as st

from core import classify, db, repo
from core.money import fmt_brl

SEM_SUB = "— sem subcategoria —"
ESCOLHER = {"id": None, "nome": "— escolher categoria —", "subcategorias": []}


def _opcoes(plano, natureza: str):
    return [cat for cat in plano if cat["natureza"] == natureza and cat["ativa"]]


def _editor(engine, usuario, item, plano, prefixo: str, sugestao: str = "") -> None:
    """Bloco de edição de um lançamento. Usado na fila e na busca."""
    natureza = "receita" if item["valor_centavos"] > 0 else "despesa"
    categorias = _opcoes(plano, natureza)
    if not categorias:
        st.error("Nenhuma categoria cadastrada para esta natureza.")
        return
    # sem categoria ainda: exige escolha explícita, para ninguém salvar sem
    # querer a primeira categoria da lista
    if not item.get("categoria_id"):
        categorias = [ESCOLHER, *categorias]

    with st.container(border=True):
        cabecalho, corpo = st.columns([1.5, 2.6])
        # o rótulo da origem é a melhor pista do que a linha é: a pensão vinha
        # como "CONTRIBUIÇÃO MENSAL", e sem mostrá-lo a descrição fica sozinha
        rotulo = item.get("classificacao_origem")
        cabecalho.markdown(
            f"**{item['data']:%d/%m/%Y}**<br>{item['descricao']}<br>"
            + (f"<span class='pill p-neutro'>{rotulo}</span><br>" if rotulo else "")
            + f"<span class='nota'>{item.get('conta', '')} · "
            f"{'entrada' if natureza == 'receita' else 'saída'} de "
            f"{fmt_brl(abs(item['valor_centavos']))}"
            + (f"<br>{sugestao}" if sugestao else "")
            + "</span>",
            unsafe_allow_html=True,
        )
        with corpo:
            c1, c2, c3 = st.columns([1.3, 1.3, 0.8])
            atual = item.get("categoria_id")
            indice = next((i for i, c in enumerate(categorias) if c["id"] == atual), 0)
            categoria = c1.selectbox(
                "Categoria", categorias, index=indice,
                format_func=lambda c: c["nome"], key=f"{prefixo}cat{item['id']}",
            )
            subs = [s for s in categoria["subcategorias"] if s["ativa"]]
            opcoes_sub = [SEM_SUB, *[s["nome"] for s in subs]]
            atual_sub = next(
                (s["nome"] for s in subs if s["id"] == item.get("subcategoria_id")), SEM_SUB
            )
            sub_nome = c2.selectbox(
                "Subcategoria", opcoes_sub, index=opcoes_sub.index(atual_sub),
                key=f"{prefixo}sub{item['id']}",
            )
            pessoa = c3.selectbox(
                "Pessoa", db.PESSOAS,
                index=db.PESSOAS.index(item["pessoa"]) if item.get("pessoa") in db.PESSOAS else 2,
                key=f"{prefixo}pes{item['id']}",
            )
            b1, b2, b3 = st.columns([1, 1.9, 0.8])
            if b3.button("Excluir", key=f"{prefixo}del{item['id']}", width="stretch",
                         help="Apaga este lançamento. Use quando duas fontes descreverem "
                              "o mesmo dinheiro e você quiser ficar com uma só."):
                repo.excluir_transacao(engine, item["id"])
                st.session_state["msg_classificacao"] = (
                    f"Lançamento de {item['data']:%d/%m/%Y} — {item['descricao'][:40]} — excluído."
                )
                st.rerun()
            if b1.button("Salvar", key=f"{prefixo}ok{item['id']}", type="primary",
                         width="stretch"):
                if categoria["id"] is None:
                    st.warning("Escolha uma categoria antes de salvar.")
                    return
                sub_id = next((s["id"] for s in subs if s["nome"] == sub_nome), None)
                repo.reclassificar(
                    engine, item["id"], categoria_id=categoria["id"], subcategoria_id=sub_id,
                    pessoa=pessoa, usuario=usuario["nome"], criar_regra=True,
                )
                st.session_state["msg_classificacao"] = (
                    f"Salvo. O sistema vai reconhecer “{item['descricao'][:40]}” sozinho "
                    "na próxima importação."
                )
                st.rerun()
            b2.caption("Ao salvar, a correção vira memória e vale para as próximas faturas.")


def _listar(engine, usuario, fila, plano) -> None:
    """Doze por vez, e não quarenta.

    Cada lançamento na tela são cinco campos, e o Streamlit redesenha todos a
    cada toque: com quarenta eram duzentos campos por clique, e a tela demorava
    a transicionar de um campo para o seguinte. Doze cabem numa tela e
    respondem.
    """
    por_pagina = 12
    paginas = (len(fila) + por_pagina - 1) // por_pagina
    pagina = 1
    if paginas > 1:
        pagina = st.number_input(
            f"Página (de {paginas})", min_value=1, max_value=paginas, value=1, step=1,
            help="Ao salvar, o lançamento sai da fila e os próximos sobem sozinhos.",
        )
    inicio = (int(pagina) - 1) * por_pagina
    for item in fila[inicio:inicio + por_pagina]:
        _editor(engine, usuario, item, plano, "f", item.get("observacao") or "")
    if paginas > 1:
        st.caption(
            f"Mostrando {inicio + 1}–{min(inicio + por_pagina, len(fila))} de {len(fila)}."
        )


def render(engine, usuario: dict) -> None:
    if st.session_state.pop("msg_classificacao", None):
        st.success(st.session_state.get("msg_classificacao", "Salvo."), icon="🧠")

    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)
        por_mes = repo.pendentes_por_competencia(conn)
        donos_errados = repo.dono_pela_descricao(conn)

    # a carga inicial atribuiu tudo ao dono do arquivo, mas a Rô escreve de quem
    # é o gasto no fim da descrição. Corrigir isso é uma decisão dele, não minha
    if donos_errados:
        total = sum(donos_errados.values())
        detalhe = ", ".join(f"{n} para {p}" for p, n in sorted(donos_errados.items()))
        c1, c2 = st.columns([3, 1])
        c1.info(
            f"**{total} lançamentos dizem na descrição de quem são** e estão atribuídos a "
            f"outra pessoa ({detalhe}). É a Rô escrevendo o dono no fim — "
            "“ALMOÇO ANDRÉ”, “CONSULTA RO”.",
            icon="👤",
        )
        if c2.button("Corrigir o dono", type="primary", width="stretch"):
            mudados = repo.corrigir_dono_pela_descricao(engine)
            st.success(f"{mudados} lançamento(s) com o dono corrigido.")
            st.rerun()

    # o gasto da casa que ficou com uma pessoa só porque o upload perguntou
    # "de quem é este arquivo": é o que faz o relatório por pessoa mentir feio
    with engine.connect() as conn:
        orfaos = repo.sem_dono_declarado(conn)
    if orfaos["quantidade"]:
        c1, c2 = st.columns([3, 1])
        c1.warning(
            f"**{orfaos['quantidade']} lançamentos da planilha não dizem de quem são** e estão "
            f"atribuídos a uma pessoa — {fmt_brl(orfaos['despesas'])} de despesa. Isso vem da "
            "resposta a “de quem é este arquivo”, que valeu para todas as linhas. Gasto da "
            "casa deveria ficar como **Casal**.",
            icon="👥",
        )
        if c2.button("Passar para o Casal", width="stretch"):
            mudados = repo.atribuir_ao_casal(engine)
            st.success(f"{mudados} lançamento(s) agora são do casal.")
            st.rerun()

    total_pendente = sum(por_mes.values())
    aba_fila, aba_busca = st.tabs([
        f"📌 Fila de pendências ({total_pendente})", "🔎 Reclassificar qualquer lançamento"
    ])

    with aba_fila:
        if not total_pendente:
            st.success("Nada pendente — tudo classificado.", icon="✅")
            st.caption(
                "Lançamentos com descrição genérica (PIX, transferência, código sem nome) "
                "caem aqui quando as regras e a IA não têm certeza."
            )
        else:
            # mês a mês é como o André trabalha, e o gasto esporádico com os
            # filhos está espalhado entre centenas de linhas: sem cortar por
            # mês, achar as três de agosto é rolar a lista inteira
            c1, c2, c3 = st.columns([1.3, 1.7, 1.2])
            TODOS = f"Todos os meses ({total_pendente})"
            opcoes = [TODOS] + [f"{mes} ({n})" for mes, n in por_mes.items()]
            escolha = c1.selectbox("Mês", opcoes)
            competencia = None if escolha == TODOS else escolha.split(" ")[0]
            termo = c2.text_input(
                "Filtrar", placeholder="Ex.: colégio, pensão, farmácia…",
                help="Busca na descrição e no rótulo que a planilha usou.",
            )
            if c3.button("Reaplicar regras na fila", width="stretch"):
                with engine.begin() as conn:
                    resolvidas = classify.reclassificar_pendentes(conn)
                st.success(f"{resolvidas} lançamento(s) resolvido(s) pelas regras atuais.")
                st.rerun()

            with engine.connect() as conn:
                # a fila inteira numa consulta só: são centenas de linhas leves,
                # e um teto de 200 fazia a contagem da tela mentir sobre o total
                fila = repo.fila_pendentes(
                    conn, limite=5000, competencia=competencia, termo=termo
                )
            if not fila:
                st.info("Nenhum pendente com esse filtro.", icon="🔎")
            else:
                st.caption(
                    f"{len(fila)} lançamento(s) aqui. Cada correção vira memória: da "
                    "próxima vez o sistema reconhece sozinho."
                )
                _listar(engine, usuario, fila, plano)

    with aba_busca:
        termo = st.text_input(
            "Buscar", placeholder="Ex.: iFood, Uber, mercado…",
            help="Busca na descrição do extrato.",
        )
        with engine.connect() as conn:
            achados = repo.buscar_transacoes(conn, termo, limite=40)
        if not achados:
            st.caption("Nenhum lançamento encontrado.")
        else:
            st.caption(f"{len(achados)} lançamento(s). Qualquer um pode ser reclassificado.")
            for item in achados:
                marca = (f"origem: {item['classificacao_origem']} · "
                         if item.get("classificacao_origem") else "")
                if item["categoria"]:
                    atual = (
                        marca + item["categoria"]
                        + (f" › {item['subcategoria']}" if item["subcategoria"] else "")
                        + f" ({item['status'].replace('_', ' ')})"
                    )
                else:
                    atual = marca + "ainda sem categoria"
                _editor(engine, usuario, item, plano, "b", atual)
