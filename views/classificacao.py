"""Fila de pendências e reclassificação de qualquer lançamento."""

from __future__ import annotations

import streamlit as st

from core import classify, db, repo
from core.money import fmt_brl
from core.texto import sem_marcacao
from ui import dados, destinos

ESCOLHER = {"id": None, "nome": "— escolher categoria —", "subcategorias": []}


def _opcoes(plano, natureza: str):
    return [cat for cat in plano if cat["natureza"] == natureza and cat["ativa"]]


def _editor(engine, usuario, item, plano, prefixo: str, sugestao: str = "") -> None:
    """Bloco de edição de um lançamento. Usado na fila e na busca.

    Um campo só, "Categoria › Subcategoria", dentro de um formulário. As duas
    coisas andam juntas e vieram da mesma medição: com dois campos encadeados,
    a subcategoria tinha de ser recalculada a partir da categoria escolhida no
    mesmo rerun — e isso obrigava cada toque de campo a re-executar a tela
    inteira. Medido aqui dentro: 872ms por toque, contra 24ms na tela de metas,
    que já usava formulário. A conta de classificar cinquenta lançamentos era
    essa diferença, cinquenta vezes.

    Com o destino achatado não há mais encadeamento, e o formulário passa a
    caber: escolher não recarrega nada, só o Salvar recarrega. É a mesma lista
    que a tela de de-para, o lançamento manual e a Análise IA já usam.
    """
    natureza = "receita" if item["valor_centavos"] > 0 else "despesa"
    if not _opcoes(plano, natureza):
        st.error("Nenhuma categoria cadastrada para esta natureza.")
        return

    atual = (item.get("categoria_id"), item.get("subcategoria_id"))
    lista = destinos.do_plano(plano, natureza, primeiro=item.get("categoria_id"))
    indice = next((i for i, d in enumerate(lista.values()) if d == atual), None)
    if indice is None:
        # sem categoria ainda (ou numa categoria já desativada): exige escolha
        # explícita, para ninguém salvar sem querer o primeiro destino da lista
        lista = {ESCOLHER["nome"]: None, **lista}
        indice = 0

    # a chave vira uma classe no HTML (st-key-linha…), e é por ela que o tema
    # apaga a moldura do formulário aqui dentro: ele existe pelo comportamento
    # (não recarregar a tela a cada escolha), não para virar caixa dentro de caixa
    with st.container(border=True, key=f"linha{prefixo}{item['id']}"):
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
            # border=False porque o container acima já desenha a moldura do
            # bloco; o formulário aqui existe pelo comportamento, não pela caixa
            with st.form(f"{prefixo}form{item['id']}", border=False):
                c1, c2 = st.columns([2.6, 0.8])
                escolha = c1.selectbox(
                    "Vai para", list(lista), index=indice,
                    key=f"{prefixo}dest{item['id']}",
                    help="Escolha a subcategoria quando ela importar. A categoria "
                         "sozinha já tira o lançamento da fila.",
                )
                pessoa = c2.selectbox(
                    "Pessoa", db.PESSOAS,
                    index=(db.PESSOAS.index(item["pessoa"])
                           if item.get("pessoa") in db.PESSOAS else 2),
                    key=f"{prefixo}pes{item['id']}",
                )
                b1, b2, b3 = st.columns([1, 1.9, 0.8])
                salvar = b1.form_submit_button("Salvar", type="primary", width="stretch")
                b2.caption("Ao salvar, a correção vira memória e vale para as próximas faturas.")
                # Excluir também é submit do formulário: fora dele, o botão
                # apagaria o lançamento com o destino que estava na tela antes
                # da escolha atual — e voltaria a fazer a tela recarregar a cada
                # toque de campo, que é justamente o que se veio consertar
                excluir = b3.form_submit_button(
                    "Excluir", width="stretch",
                    help="Apaga este lançamento. Use quando duas fontes descreverem "
                         "o mesmo dinheiro e você quiser ficar com uma só.",
                )

            if excluir:
                repo.excluir_transacao(engine, item["id"])
                st.session_state["msg_classificacao"] = (
                    f"Lançamento de {item['data']:%d/%m/%Y} — "
                    f"{sem_marcacao(item['descricao'][:40])} — excluído."
                )
                st.rerun()
            if salvar:
                destino = lista[escolha]
                if destino is None:
                    st.warning("Escolha uma categoria antes de salvar.")
                    return
                virou_regra = repo.reclassificar(
                    engine, item["id"], categoria_id=destino[0], subcategoria_id=destino[1],
                    pessoa=pessoa, usuario=usuario["nome"], criar_regra=True,
                )
                st.session_state["msg_classificacao"] = (
                    f"Salvo. O sistema vai reconhecer "
                    f"“{sem_marcacao(item['descricao'][:40])}” sozinho "
                    "na próxima importação."
                    if virou_regra else
                    "Salvo — só este lançamento. A descrição diz o meio de pagamento "
                    "(PIX, TED, débito automático) e não o estabelecimento: guardá-la "
                    "como regra faria todo lançamento parecido herdar esta classificação."
                )
                st.rerun()


def _destinos(plano, natureza: str) -> dict[str, tuple[int, int | None] | None]:
    """Rótulo legível -> (categoria_id, subcategoria_id), achatado num nível só.

    A lista mora em ui.destinos porque a revisão de subcategorias da Análise IA
    usa a mesma; aqui só entra a linha de "ainda não escolhi" na frente.
    """
    return {ESCOLHER["nome"]: None, **destinos.do_plano(plano, natureza)}


def _aba_de_para(engine, usuario: dict, plano, rotulos, traduzidos) -> None:
    """Traduz o vocabulário da Rô para o plano de contas, um rótulo por vez.

    Treze rótulos cobrem os 441 pendentes da carga inicial. Decidir treze vezes
    é trabalho de minutos; decidir 441 vezes é trabalho que não acontece — e a
    tradução fica guardada, então a próxima importação já entra classificada.

    Os rótulos chegam prontos de quem chamou: a `render` já precisa deles para
    escrever a contagem no título da aba, e buscá-los de novo aqui era a mesma
    pergunta indo ao banco duas vezes no mesmo rerun.
    """

    st.caption(
        "A Rô classifica com o vocabulário dela — CUIDADOS PESSOAIS, INFRA, TAXAS. "
        "Aqui cada rótulo é traduzido **uma vez** para o plano de contas, e a tradução "
        "vale para todos os lançamentos dele e para as próximas importações."
    )

    if traduzidos:
        with st.expander(f"Traduções já feitas ({len(traduzidos)})"):
            for rotulo, traducao in sorted(traduzidos.items()):
                linha, botao = st.columns([5, 1])
                destino = traducao["categoria"] + (
                    f" › {traducao['subcategoria']}" if traducao["subcategoria"] else ""
                )
                linha.markdown(f"**{rotulo}** → {destino}")
                if botao.button("Desfazer", key=f"dp_del_{rotulo}"):
                    devolvidos = repo.apagar_de_para(engine, rotulo)
                    st.session_state["msg_classificacao"] = (
                        f"“{rotulo}” desfeito: {devolvidos} lançamento(s) voltaram para a fila."
                    )
                    st.rerun()
            st.caption(
                "Desfazer devolve à fila os lançamentos que a tradução classificou. O que "
                "você corrigiu à mão depois, para outra categoria, fica como está."
            )

    if not rotulos:
        st.success("Nenhum rótulo pendente de tradução.", icon="✅")
        return

    st.markdown(f"### {len(rotulos)} rótulos a traduzir")
    for item in rotulos:
        rotulo = item["rotulo"]
        with st.container(border=True):
            esquerda, direita = st.columns([1.4, 2.6])
            esquerda.markdown(
                f"<span class='pill p-alerta'>{rotulo}</span><br>"
                f"<span class='nota'>{item['quantidade']} lançamentos · "
                f"{fmt_brl(abs(item['total']))}</span>",
                unsafe_allow_html=True,
            )
            with direita:
                # o sinal do total diz o lado: rótulo de saída não pode virar receita
                natureza = "receita" if item["total"] > 0 else "despesa"
                # um seletor só, com "Categoria › Subcategoria" achatado. Dois
                # campos encadeados obrigavam a escolher a categoria, esperar a
                # tela recarregar e só então ver as subcategorias — e, com a
                # lista de opções mudando debaixo do widget, elas nem sempre
                # apareciam. Aqui o destino inteiro é uma escolha só.
                lista = _destinos(plano, natureza)
                c1, c2 = st.columns([3.2, 0.9])
                escolha = c1.selectbox(
                    "Vai para", list(lista), key=f"dp_dest_{rotulo}",
                    help="Escolha a subcategoria quando ela importar. A categoria "
                         "sozinha já tira o lançamento da fila.",
                )
                if c2.button("Aplicar", key=f"dp_ok_{rotulo}", type="primary",
                             width="stretch"):
                    destino = lista[escolha]
                    if destino is None:
                        st.warning("Escolha o destino antes de aplicar.")
                    else:
                        aplicados = repo.salvar_de_para(
                            engine, rotulo=rotulo, categoria_id=destino[0],
                            subcategoria_id=destino[1], usuario=usuario["nome"],
                        )
                        st.session_state["msg_classificacao"] = (
                            f"“{rotulo}”: {aplicados} lançamento(s) na categoria. "
                            "Eles continuam na fila para você escolher a subcategoria "
                            "caso a caso — já com a categoria preenchida."
                            if destino[1] is None
                            else f"“{rotulo}”: {aplicados} lançamento(s) classificados."
                        )
                        st.rerun()


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
    # o `pop` tira o recado; ler de novo pelo `get` só devolvia o texto padrão,
    # e a frase que explica se a correção virou memória nunca chegava à tela
    recado = st.session_state.pop("msg_classificacao", None)
    if recado:
        st.success(recado, icon="🧠")

    # a tela inteira lê do cache, que se invalida sozinho a cada gravação.
    # Trocar de categoria, digitar no filtro ou trocar de aba não vão ao banco;
    # salvar vai. Classificar cinquenta lançamentos são dezenas de reruns e
    # pouco mais de uma dúzia de idas ao Supabase.
    plano = dados.plano_de_contas(engine, dados.versao())
    painel = dados.painel_de_classificacao(engine, dados.versao())
    por_mes = painel["por_mes"]
    donos_errados, orfaos = painel["donos_errados"], painel["orfaos"]
    rotulos, traduzidos = painel["rotulos"], painel["traduzidos"]


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
    # Seções em vez de abas, por um motivo prático: `st.tabs` não guarda qual
    # aba estava aberta, e todo `st.rerun()` devolve a tela para a primeira.
    # Quem classifica salva um lançamento e é jogado de volta para o de-para,
    # cinquenta vezes seguidas. Com a escolha guardada no estado, salvar deixa
    # a pessoa onde ela estava.
    #
    # De quebra, só a seção aberta é desenhada. Com abas, o Streamlit executa
    # as três em todo rerun — inclusive os quarenta editores da busca, que
    # ninguém pediu.
    faltam_sub = dados.sem_subcategoria_por_mes(engine, dados.versao())
    SECOES = {
        f"🔁 De-para de rótulos ({len(rotulos)})": "de_para",
        f"📌 Fila de pendências ({total_pendente})": "fila",
        f"🧩 Sem subcategoria ({sum(faltam_sub.values())})": "sem_sub",
        "🔎 Reclassificar qualquer lançamento": "busca",
    }
    if st.session_state.get("secao_classificacao") not in SECOES.values():
        st.session_state["secao_classificacao"] = "fila"
    rotulo_atual = next(
        r for r, chave in SECOES.items()
        if chave == st.session_state["secao_classificacao"]
    )
    escolhida = st.segmented_control(
        "Seção", list(SECOES), default=rotulo_atual,
        key="secao_rotulo", label_visibility="collapsed",
    )
    if escolhida:
        st.session_state["secao_classificacao"] = SECOES[escolhida]
    secao = st.session_state["secao_classificacao"]

    if secao == "de_para":
        _aba_de_para(engine, usuario, plano, rotulos, traduzidos)

    if secao == "fila":
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
                with engine.begin() as escrita:
                    resolvidas = classify.reclassificar_pendentes(escrita)
                st.success(f"{resolvidas} lançamento(s) resolvido(s) pelas regras atuais.")
                st.rerun()

            # a fila inteira numa consulta só: são centenas de linhas leves,
            # e um teto de 200 fazia a contagem da tela mentir sobre o total
            fila = dados.fila_de_pendencias(
                engine, dados.versao(), competencia, termo
            )
            if not fila:
                st.info("Nenhum pendente com esse filtro.", icon="🔎")
            else:
                st.caption(
                    f"{len(fila)} lançamento(s) aqui. Cada correção vira memória: da "
                    "próxima vez o sistema reconhece sozinho."
                )
                _listar(engine, usuario, fila, plano)

    if secao == "sem_sub":
        _secao_sem_subcategoria(engine, usuario, plano, faltam_sub)

    if secao == "busca":
        termo = st.text_input(
            "Buscar", placeholder="Ex.: iFood, Uber, mercado…",
            help="Busca na descrição do extrato.",
        )
        achados = dados.busca(engine, dados.versao(), termo)
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


def _secao_sem_subcategoria(engine, usuario: dict, plano, por_mes: dict[str, int]) -> None:
    """O que está classificado até a categoria e parou ali.

    Existe porque achar o lançamento era o trabalho: o relatório dizia "R$ 231
    em 1 lançamento ainda sem subcategoria" e não dizia qual. Sobrava abrir a
    fila — onde ele não está, porque já tem categoria — ou caçar pela busca sem
    saber o nome. Aqui ele aparece, com o mês já escolhido por quem mandou
    para cá.
    """
    if not por_mes:
        st.success("Nada sem subcategoria — está tudo detalhado.", icon="✅")
        st.caption(
            "Categoria é o que soma no relatório; subcategoria é o detalhe que "
            "explica de onde veio o número."
        )
        return

    foco = st.session_state.pop("foco_sem_sub", None) or {}
    if foco.get("competencia") in por_mes:
        st.session_state["mes_sem_sub"] = f"{foco['competencia']} ({por_mes[foco['competencia']]})"
    categoria_id = foco.get("categoria_id") or st.session_state.get("categoria_sem_sub")
    st.session_state["categoria_sem_sub"] = categoria_id

    total = sum(por_mes.values())
    TODOS = f"Todos os meses ({total})"
    opcoes = [TODOS] + [f"{mes} ({n})" for mes, n in por_mes.items()]
    if st.session_state.get("mes_sem_sub") not in opcoes:
        st.session_state["mes_sem_sub"] = TODOS

    c1, c2 = st.columns([1.6, 2.4])
    escolha = c1.selectbox("Mês", opcoes, key="mes_sem_sub")
    competencia = None if escolha == TODOS else escolha.split(" ")[0]

    nome_categoria = next(
        (c["nome"] for c in plano if c["id"] == categoria_id), None
    ) if categoria_id else None
    if nome_categoria:
        c2.markdown("&nbsp;", unsafe_allow_html=True)
        if c2.button(f"Mostrando só **{nome_categoria}** — ver todas as categorias",
                     width="stretch"):
            st.session_state["categoria_sem_sub"] = None
            st.rerun()

    faltando = dados.faltando_subcategoria(
        engine, dados.versao(), competencia, categoria_id
    )
    if not faltando:
        st.info("Nada sem subcategoria com este filtro.", icon="🔎")
        return

    st.caption(
        f"{len(faltando)} lançamento(s) com categoria e sem subcategoria. O campo já vem "
        "no grupo certo: é só abrir e escolher o detalhe. A categoria também pode mudar, "
        "se ela é que estiver errada."
    )
    _listar(engine, usuario, faltando, plano)
