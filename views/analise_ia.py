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

    aba_mes, aba_pergunta, aba_sub = st.tabs(
        ["Leitura do mês", "Perguntar sobre o mês", "Completar subcategorias"]
    )
    with aba_mes:
        _leitura_do_mes(engine, competencia, usuario, ligada)
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
    return texto.startswith("**Não consegui falar com a IA")


def _mostrar_falha(texto: str) -> None:
    """Mostra o erro e o estado da configuração — e não grava nada.

    Gravar a mensagem de erro como se fosse a análise do mês faria a tela
    mostrá-la depois como texto do mês, e ainda marcá-la como atual.
    """
    st.error(texto, icon="🚫")
    with st.expander("Estado da configuração"):
        st.json(ai.diagnostico())


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
    with engine.connect() as conn:
        faltando = repo.sem_subcategoria(conn, competencia=competencia, limite=500)
    if not faltando:
        st.success("Nada sem subcategoria neste mês.", icon="✅")
        return

    total = sum(abs(item["valor_centavos"]) for item in faltando)
    st.caption(
        f"{len(faltando)} lançamentos com categoria e sem subcategoria neste mês, "
        f"{_reais(total)}. A categoria escolhida não é alterada."
    )
    if not ligada:
        st.dataframe(
            [
                {
                    "Data": f"{item['data']:%d/%m}",
                    "Descrição": item["descricao"][:50],
                    "Categoria": item["categoria"],
                    "Valor": fmt_brl(item["valor_centavos"]),
                }
                for item in faltando[:50]
            ],
            width="stretch", hide_index=True,
        )
        return

    if st.button("Sugerir subcategorias (até 60 por vez)", type="primary", key="ia_subs"):
        with engine.connect() as conn:
            with st.spinner("Perguntando à IA…"):
                st.session_state["sugestoes_sub"] = repo.sugerir_subcategorias(
                    conn, competencia=competencia, limite=60
                )

    sugestoes = st.session_state.get("sugestoes_sub") or []
    if not sugestoes:
        return

    st.caption(
        "Confira antes de aplicar. Confiança baixa quer dizer que a descrição não "
        "permitia decidir — desmarque e deixe para a fila."
    )
    escolhas: dict[int, str] = {}
    for item in sugestoes:
        colunas = st.columns([0.5, 3, 2, 2, 1])
        aceitar = colunas[0].checkbox(
            "aceitar", value=item["confianca"] >= 0.8, key=f"sub_{item['id']}",
            label_visibility="collapsed",
        )
        colunas[1].write(f"{item['data']:%d/%m} {item['descricao'][:40]}")
        colunas[2].write(item["categoria"])
        colunas[3].write(f"**{item['subcategoria']}**")
        colunas[4].write(f"{item['confianca']:.0%}")
        if aceitar:
            escolhas[item["id"]] = item["subcategoria"]

    if st.button(f"Aplicar {len(escolhas)} subcategorias", key="ia_aplicar_subs"):
        gravadas = repo.aplicar_subcategorias(engine, escolhas, usuario.get("nome", "—"))
        st.session_state.pop("sugestoes_sub", None)
        st.success(f"{gravadas} subcategoria(s) preenchida(s).")
        st.rerun()
