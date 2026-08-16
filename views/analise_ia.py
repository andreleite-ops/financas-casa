"""Análise escrita do mês e preenchimento de subcategoria, pela Claude API.

Duas coisas moram aqui, e as duas partem do mesmo princípio: a IA não consulta
o banco nem inventa número nenhum. Ela recebe o que já foi apurado e escreve
sobre isso — e o que ela escrever fica gravado, com a data e a impressão
digital dos números que gerou o texto, para ninguém ler mês passado achando
que é hoje.
"""

from __future__ import annotations

import streamlit as st

from core import ai, analytics, repo
from core.money import fmt_brl
from ui import destinos


def _reais(centavos: int) -> str:
    """Valor pronto para entrar em texto markdown.

    O Streamlit lê um par de cifrões como fórmula LaTeX: "R$ 7.397,64 de R$
    35.208,19" some no meio da frase e vira matemática. Escapando o cifrão, a
    frase volta a ser uma frase.
    """
    return fmt_brl(centavos).replace("$", r"\$")


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        competencias = repo.competencias_disponiveis(conn)
    if not competencias:
        st.info("Sem lançamentos ainda — importe um extrato para a IA ter o que analisar.",
                icon="📥")
        return

    competencia = st.selectbox("Competência", competencias, key="ia_competencia")
    ligada = ai.disponivel()

    if not ligada:
        st.warning(
            "**Análise por IA ainda não configurada.** Adicione `ANTHROPIC_API_KEY` em "
            "`.streamlit/secrets.toml` (ou em Settings › Secrets, no Streamlit Cloud) e "
            "dê Reboot. Todo o resto do sistema funciona sem ela.",
            icon="🔌",
        )

    aba_mes, aba_ano, aba_pergunta, aba_sub = st.tabs(
        ["Leitura do mês", "Ano & padrões", "Perguntar sobre o mês", "Completar subcategorias"]
    )
    with aba_mes:
        _leitura_do_mes(engine, competencia, usuario, ligada)
    with aba_ano:
        _leitura_do_ano(engine, competencia, usuario, ligada)
    with aba_pergunta:
        _perguntar(engine, competencia, usuario, ligada)
    with aba_sub:
        _subcategorias(engine, competencia, usuario, ligada)


def _aviso_de_cobertura(engine, competencia: str) -> None:
    """O quanto do mês está classificado, dito antes de qualquer conclusão.

    Uma análise sobre um mês metade classificado descreve metade do mês. Isso
    precisa estar na tela, não só dentro do prompt: quem lê o texto tem de
    saber sobre o que ele fala.
    """
    with engine.connect() as conn:
        cobertura = analytics.cobertura_da_classificacao(conn, competencia)
    if not cobertura["gasto_total"]:
        return

    pct = cobertura["percentual_classificado"]
    texto = (
        f"**{pct:.0f}% do gasto do mês está classificado** "
        f"({_reais(cobertura['gasto_classificado'])} de {_reais(cobertura['gasto_total'])})."
    )
    if cobertura["sem_categoria"]:
        texto += (
            f" Faltam {cobertura['sem_categoria']} lançamentos na fila, "
            f"{_reais(cobertura['gasto_sem_categoria'])}."
        )
    if cobertura["sem_subcategoria"]:
        texto += f" E {cobertura['sem_subcategoria']} estão sem subcategoria."

    if pct >= 95:
        st.success(texto, icon="✅")
    else:
        st.warning(texto + " A leitura abaixo fala só do que já foi classificado.", icon="⚠️")


def _leitura_do_mes(engine, competencia: str, usuario: dict, ligada: bool) -> None:
    _aviso_de_cobertura(engine, competencia)

    with engine.connect() as conn:
        contexto = analytics.contexto_para_ia(conn, competencia)
        anterior = repo.ultima_analise(conn, competencia, contexto)

    if not ligada:
        with st.expander("Ver os números que seriam enviados para a análise"):
            st.code(contexto, language="text")
        return

    gerar = st.button(
        "Gerar leitura deste mês" if anterior is None else "Gerar de novo",
        type="primary", key="ia_gerar",
    )

    if anterior and not gerar:
        if anterior["desatualizada"]:
            st.info(
                "Os números mudaram desde que este texto foi escrito — houve "
                "classificação ou importação depois. Gere de novo para valer.",
                icon="🕓",
            )
        st.markdown(anterior["texto"])
        st.caption(
            f"Escrita por {anterior['gerada_por']} em "
            f"{anterior['gerada_em']:%d/%m/%Y às %H:%M} · modelo {anterior['modelo']}"
        )
        with st.expander("Números usados"):
            st.code(contexto, language="text")
        return

    if not gerar:
        st.caption(
            "A leitura usa só o que já está apurado: gasto por categoria, o que fugiu da "
            "média do ano, divisão por pessoa, metas, maiores saídas e os compromissos "
            "que se repetem todo mês."
        )
        return

    with st.spinner("Lendo o mês…"):
        texto = ai.analisar_mes(contexto)
    if _falhou(texto):
        _mostrar_falha(texto)
        return
    repo.salvar_analise(
        engine, competencia=competencia, texto=texto, modelo=ai.MODELO_ANALISE,
        contexto=contexto, usuario=usuario.get("nome", "—"),
    )
    st.markdown(texto)
    with st.expander("Números usados"):
        st.code(contexto, language="text")


def _falhou(texto: str) -> bool:
    return ai.falhou(texto)


def _mostrar_falha(texto: str) -> None:
    """Mostra o erro e o estado da configuração — e não grava nada.

    Gravar a mensagem de erro como se fosse a análise do mês faria a tela
    mostrá-la depois como texto do mês, e ainda marcá-la como atual.
    """
    st.error(texto, icon="🚫")
    with st.expander("Estado da configuração"):
        st.json(ai.diagnostico())


def _leitura_do_ano(engine, competencia: str, usuario: dict, ligada: bool) -> None:
    """A visão longa: o que se repete, o que oscila e em que meses.

    Vale uma chamada própria, e não um parágrafo a mais na do mês: a pergunta é
    outra. O mês responde para onde foi o dinheiro; a série responde o que
    acontece todo ano nesta época — e é dela que sai meta, não do último mês.
    """
    with engine.connect() as conn:
        janela = analytics.janela_de_doze_meses(conn, competencia)
        do_ano = analytics.resumo_do_ano(conn, competencia)
        contexto = analytics.contexto_do_ano(conn, competencia)
        anterior = repo.ultima_analise(conn, competencia, contexto, tipo="ano")

    if not janela:
        st.info("Sem meses lançados até esta competência.", icon="📭")
        return

    colunas = st.columns(4)
    colunas[0].metric("Receitas no ano", fmt_brl(do_ano["receitas"]))
    colunas[1].metric("Despesas no ano", fmt_brl(do_ano["despesas"]))
    colunas[2].metric(
        f"Média mensal ({do_ano['meses_decorridos']} meses)", fmt_brl(do_ano["media_despesa"])
    )
    colunas[3].metric("Poupança / receita", f"{do_ano['taxa_de_poupanca']:.1f}%")

    if len(janela) < 12:
        st.caption(
            f"Janela de {janela[0]} a {janela[-1]} — {len(janela)} meses. Com menos de "
            "doze, dá para ver concentração, não sazonalidade: para afirmar que algo "
            "acontece todo ano nesta época é preciso ver o mesmo mês repetir em anos "
            "diferentes."
        )
    else:
        st.caption(f"Últimos 12 meses: {janela[0]} a {janela[-1]}.")

    if not ligada:
        with st.expander("Ver os números da leitura do ano"):
            st.code(contexto, language="text")
        return

    gerar = st.button(
        "Ler o ano" if anterior is None else "Ler o ano de novo",
        type="primary", key="ia_gerar_ano",
    )

    if anterior and not gerar:
        if anterior["desatualizada"]:
            st.info("Os números mudaram desde este texto. Gere de novo para valer.", icon="🕓")
        st.markdown(anterior["texto"])
        st.caption(
            f"Escrita por {anterior['gerada_por']} em "
            f"{anterior['gerada_em']:%d/%m/%Y às %H:%M}"
        )
        with st.expander("Números usados"):
            st.code(contexto, language="text")
        return

    if not gerar:
        st.caption(
            "A leitura do ano olha a matriz categoria × mês, os compromissos que se "
            "repetem, o comparativo com o ano anterior e o que ainda falta classificar."
        )
        return

    with st.spinner("Lendo o ano…"):
        texto = ai.analisar_ano(contexto)
    if _falhou(texto):
        _mostrar_falha(texto)
        return
    repo.salvar_analise(
        engine, competencia=competencia, texto=texto, modelo=ai.MODELO_ANALISE,
        contexto=contexto, usuario=usuario.get("nome", "—"), tipo="ano",
    )
    st.markdown(texto)
    with st.expander("Números usados"):
        st.code(contexto, language="text")


def _perguntar(engine, competencia: str, usuario: dict, ligada: bool) -> None:
    st.caption(
        "Pergunta livre sobre este mês. A resposta sai só dos números apurados — "
        "quando a resposta não estiver neles, ela diz o que falta classificar."
    )
    pergunta = st.text_input(
        "Sua pergunta", placeholder="Por que este mês ficou mais caro que a média?",
        key="ia_pergunta", disabled=not ligada,
    )
    if st.button("Perguntar", key="ia_perguntar", disabled=not ligada) and pergunta.strip():
        with engine.connect() as conn:
            contexto = analytics.contexto_para_ia(conn, competencia)
        with st.spinner("Consultando os números…"):
            resposta = ai.responder_pergunta(contexto, pergunta)
        if _falhou(resposta):
            _mostrar_falha(resposta)
        else:
            repo.salvar_analise(
                engine, competencia=competencia, texto=resposta, modelo=ai.MODELO_ANALISE,
                contexto=contexto, usuario=usuario.get("nome", "—"),
                pergunta=pergunta.strip(),
            )
            st.markdown(resposta)

    with engine.connect() as conn:
        anteriores = repo.perguntas_anteriores(conn, competencia)
    if anteriores:
        st.divider()
        st.caption("Perguntas anteriores deste mês")
        for item in anteriores:
            with st.expander(
                f"{item['pergunta'][:80]} — {item['gerada_por']}, "
                f"{item['gerada_em']:%d/%m %H:%M}"
            ):
                st.markdown(item["texto"])


def _subcategorias(engine, competencia: str, usuario: dict, ligada: bool) -> None:
    """Preenche a subcategoria do que já foi classificado até a categoria.

    Classificar em volume termina assim: a categoria escolhida (que é a que
    soma no relatório) e a subcategoria em branco. Aqui a pergunta à IA é
    estreita — dentro de Saúde, isto é Farmácia ou Consulta? — porque a
    categoria já foi decidida por gente e não está em jogo.
    """
    # o recado de quem acabou de aplicar: st.success antes do rerun some junto
    # com a tela que o escreveu, e o resultado da gravação nunca era lido
    recado = st.session_state.pop("msg_subcategorias", None)
    if recado:
        st.success(recado, icon="✅")

    with engine.connect() as conn:
        faltando = repo.sem_subcategoria(conn, competencia=competencia, limite=500)
    if not faltando:
        st.success("Nada sem subcategoria neste mês.", icon="✅")
        return

    total = sum(abs(item["valor_centavos"]) for item in faltando)
    st.caption(
        f"{len(faltando)} lançamentos com categoria e sem subcategoria neste mês, "
        f"{_reais(total)}. A sugestão da IA fica dentro da categoria já escolhida, "
        "mas a lista ao lado tem o plano inteiro: se o grupo veio errado, "
        "corrija aqui mesmo."
    )
    # sem chave a tela continua servindo: a lista de subcategorias vem do plano
    # de contas, não da IA. Muda só o que aparece preenchido.
    rotulo = (
        "Sugerir subcategorias (até 60 por vez)" if ligada
        else "Listar 60 para preencher à mão"
    )
    if st.button(rotulo, type="primary", key="ia_subs"):
        with engine.connect() as conn:
            with st.spinner("Perguntando à IA…" if ligada else "Montando a lista…"):
                st.session_state["sugestoes_sub"] = repo.sugerir_subcategorias(
                    conn, competencia=competencia, limite=60
                )

    sugestoes = st.session_state.get("sugestoes_sub") or []
    if not sugestoes:
        return

    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)
    _tabela_de_subcategorias(engine, sugestoes, plano, usuario)


DEIXAR_PARA_DEPOIS = "— deixar para depois —"
CONFIANCA_PARA_PREENCHER = 0.8


def _tabela_de_subcategorias(engine, sugestoes: list[dict], plano, usuario: dict) -> None:
    """Uma linha por lançamento, com o destino ao lado — sugerido ou não.

    O campo é um só, "Categoria › Subcategoria", com o plano inteiro dentro
    dele e o bloco da categoria atual no topo. A classificação em volume erra
    o grupo de vez em quando (foi para Casa e era Lazer), e essa linha só
    reaparece aqui, faltando subcategoria. Oferecer só as subcategorias do
    grupo errado obrigaria a corrigir o grupo noutra tela, depois — e "depois"
    é o que esta tela existe para evitar.

    Os que a IA não soube dizer aparecem aqui do mesmo jeito, com o campo em
    branco. A confiança alta já vem preenchida (é aceitar em bloco); a baixa
    vem em branco de propósito — é ela que precisa de gente, e um valor
    preenchido seria aceito no automático justamente onde a IA disse não saber.
    """
    sem_sugestao = [item for item in sugestoes if not item["subcategoria"]]
    st.caption(
        f"{len(sugestoes)} lançamentos. Os de confiança alta já vêm preenchidos; "
        f"confira e aplique. "
        + (
            f"Em {len(sem_sugestao)} a IA não soube decidir — escolha na lista ao lado."
            if sem_sugestao else ""
        )
    )

    escolhas: dict[int, tuple[int, int | None]] = {}
    trocas = 0
    larguras = [2.6, 1.6, 3.4, 1.2, 1]
    cabecalho = st.columns(larguras)
    for coluna, titulo in zip(cabecalho, ("Lançamento", "Categoria hoje", "Destino",
                                          "Confiança", "Valor")):
        coluna.caption(f"**{titulo}**")

    for item in sugestoes:
        colunas = st.columns(larguras)
        colunas[0].write(f"{item['data']:%d/%m} {item['descricao'][:34]}")
        colunas[1].write(item["categoria"])

        # o plano inteiro da natureza do lançamento, com o bloco da categoria
        # atual na frente: o caso comum continua a um clique, e o grupo errado
        # tem para onde ir
        destino_por_rotulo = destinos.do_plano(
            plano, item["natureza"], primeiro=item["categoria_id"]
        )
        opcoes = [DEIXAR_PARA_DEPOIS, *destino_por_rotulo]

        sugerida = item["subcategoria"]
        preencher = bool(sugerida) and item["confianca"] >= CONFIANCA_PARA_PREENCHER
        rotulo_sugerido = destinos.rotulo(item["categoria"], sugerida) if sugerida else ""
        indice = (
            opcoes.index(rotulo_sugerido)
            if preencher and rotulo_sugerido in opcoes else 0
        )
        escolha = colunas[2].selectbox(
            "destino", opcoes, index=indice, key=f"sub_{item['id']}",
            label_visibility="collapsed",
        )

        if sugerida and not preencher:
            colunas[3].caption(f"IA: {sugerida} ({item['confianca']:.0%})")
        elif sugerida:
            colunas[3].caption(f"{item['confianca']:.0%}")
        else:
            colunas[3].caption("—")
        colunas[4].caption(fmt_brl(item["valor_centavos"]))

        if escolha != DEIXAR_PARA_DEPOIS:
            destino = destino_por_rotulo[escolha]
            escolhas[item["id"]] = destino
            trocas += destino[0] != item["categoria_id"]

    st.divider()
    if st.button(
        f"Aplicar {len(escolhas)} classificações", key="ia_aplicar_subs",
        type="primary", disabled=not escolhas,
        help=(f"{trocas} deles mudam de categoria, e a troca vira memória "
              "para as próximas faturas." if trocas else None),
    ):
        contas = repo.aplicar_destinos(engine, escolhas, usuario.get("nome", "—"))
        st.session_state.pop("sugestoes_sub", None)
        gravadas, trocadas, memorias = (
            contas["gravadas"], contas["categorias"], contas["memorias"]
        )
        recado = (
            "1 lançamento classificado." if gravadas == 1
            else f"{gravadas} lançamentos classificados."
        )
        if trocadas:
            recado += (
                " 1 mudou de categoria" if trocadas == 1
                else f" {trocadas} mudaram de categoria"
            )
            recado += (
                (f", e {'a correção virou memória' if memorias == 1 else f'{memorias} viraram memória'}"
                 " — a próxima fatura já reconhece o estabelecimento no grupo certo.")
                if memorias
                else " (sem virar memória: a descrição só diz o meio de pagamento)."
            )
        st.session_state["msg_subcategorias"] = recado
        st.rerun()
