"""Upload de extratos, conferência de duplicidades, crítica e cadastro de contas."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import dedup, db, reconcile, repo
from core.money import fmt_brl
from parsers import instituicoes, tabular
from parsers.base import ErroDeLeitura
from ui.graficos import MESES_PT as MESES_CURTOS
from ui.tema import selo_pessoa

PAPEIS = ["data", "descricao", "valor", "entrada", "saida", "categoria", "subcategoria",
          "pessoa", "tipo"]
ROTULOS_PAPEL = {
    "data": "Data (obrigatória)", "descricao": "Descrição", "valor": "Valor único",
    "entrada": "Entrada / crédito", "saida": "Saída / débito",
    "categoria": "Categoria (se a planilha já tiver)", "subcategoria": "Subcategoria",
    "pessoa": "Pessoa", "tipo": "Tipo (D/C)",
}


def _competencias_sugeridas() -> list[str]:
    hoje = date.today()
    saida = []
    ano, mes = hoje.year, hoje.month
    for _ in range(18):
        saida.append(f"{ano:04d}-{mes:02d}")
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return saida


def _aba_enviar(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        contas = repo.listar_contas(conn, so_ativas=True)
    if not contas:
        st.warning("Nenhuma conta ativa. Cadastre uma na aba **Contas e cartões**.")
        return

    origem = st.radio(
        "O que você está enviando",
        ["Extrato ou fatura", "Planilha da Rô (carga inicial do histórico)"],
        horizontal=True,
    )
    e_planilha = origem.startswith("Planilha")

    c1, c2 = st.columns(2)
    conta = c1.selectbox(
        "Conta / cartão",
        contas,
        format_func=lambda c: f"{c['nome']} — {c['titular']}",
    )
    competencia = c2.selectbox(
        "Competência", _competencias_sugeridas(),
        help="Mês de referência. Em extrato de conta corrente, cada lançamento usa a própria data.",
    )

    arquivo = st.file_uploader(
        "Arquivo", type=["pdf", "csv", "xlsx", "xlsm", "xls", "txt"],
        help="PDF, CSV ou Excel. Se o PDF for digitalizado (só imagem), baixe a versão CSV/Excel.",
    )
    if arquivo is None:
        return

    conteudo = arquivo.getvalue()
    parser = "generico" if e_planilha else conta["parser"]
    chave_estado = f"upload:{arquivo.name}:{conta['id']}:{competencia}:{e_planilha}"

    # planilha e CSV/XLSX genérico passam pela conferência de colunas
    precisa_mapear = e_planilha or (
        parser == "generico" and arquivo.name.lower().endswith((".csv", ".xlsx", ".xlsm", ".xls", ".txt"))
    )

    if precisa_mapear:
        try:
            df = tabular.carregar_tabela(conteudo, arquivo.name)
        except Exception as exc:
            st.error(f"Não consegui abrir o arquivo: {exc}")
            return
        sugestao = st.session_state.get(chave_estado) or tabular.sugerir_mapeamento(df.columns)

        st.markdown("#### Confira as colunas")
        st.caption(
            "O sistema tentou reconhecer sozinho. Ajuste o que estiver errado — "
            "é o que permite ler qualquer banco novo sem mexer no código."
        )
        st.dataframe(df.head(5), width="stretch", hide_index=True)

        opcoes = ["— nenhuma —", *df.columns]
        mapa: dict[str, str | None] = {}
        colunas_ui = st.columns(3)
        for i, papel in enumerate(PAPEIS):
            atual = sugestao.get(papel)
            indice = opcoes.index(atual) if atual in opcoes else 0
            escolha = colunas_ui[i % 3].selectbox(
                ROTULOS_PAPEL[papel], opcoes, index=indice, key=f"{chave_estado}:{papel}"
            )
            mapa[papel] = None if escolha == "— nenhuma —" else escolha

        inverter = st.checkbox(
            "O valor vem positivo mesmo quando é gasto (comum em fatura de cartão)",
            value=False,
            help="Marque se, na prévia acima, as compras aparecem com valor positivo.",
        )

        if st.button("Processar arquivo", type="primary"):
            try:
                lancamentos, avisos = tabular.extrair(
                    df, mapa,
                    origem="planilha" if e_planilha else "extrato",
                    competencia=None if conta["tipo"] == "corrente" else competencia,
                    ano_referencia=int(competencia[:4]),
                    inverter_sinal=inverter,
                )
            except ErroDeLeitura as exc:
                st.error(f"Não consegui ler: {exc}")
                return
            _importar(engine, conta, lancamentos, arquivo.name, usuario,
                      "planilha" if e_planilha else "extrato", competencia, avisos)
        return

    if st.button("Processar arquivo", type="primary"):
        try:
            lancamentos = instituicoes.ler_arquivo(
                parser, conteudo, arquivo.name,
                competencia=competencia, tipo_conta=conta["tipo"],
            )
        except ErroDeLeitura as exc:
            st.error(f"Não consegui ler o arquivo: {exc}")
            st.caption(
                "Se for fatura em PDF com layout diferente do esperado, envie a versão "
                "CSV/Excel — ela passa pela conferência de colunas."
            )
            return
        _importar(engine, conta, lancamentos, arquivo.name, usuario, "extrato", competencia, [])


def _importar(engine, conta, lancamentos, nome_arquivo, usuario, origem, competencia, avisos):
    if not lancamentos:
        st.error(
            "Não encontrei nenhum lançamento no arquivo. Confira se escolheu a conta certa "
            "e se as colunas de data e valor foram mapeadas."
        )
        return

    with st.spinner(f"Classificando {len(lancamentos)} lançamentos…"):
        resumo = repo.importar(
            engine,
            conta_id=conta["id"],
            lancamentos=lancamentos,
            arquivo=nome_arquivo,
            usuario=usuario["nome"],
            origem=origem,
            competencia=competencia,
        )

    st.success(f"Arquivo processado: {resumo['lidos']} lançamentos lidos.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Importados", resumo["importados"])
    c2.metric("Classificados", resumo["auto"])
    c3.metric("Para classificar", resumo["pendentes"])
    c4.metric("Duplicidades", resumo["duplicados_exatos"] + resumo["duplicados_provaveis"])

    if resumo["conferidos_planilha"]:
        st.info(
            f"{resumo['conferidos_planilha']} lançamentos já existiam na planilha da Rô e foram "
            "conferidos — a versão do extrato prevaleceu e herdou a categoria dela.",
            icon="✔️",
        )
    if resumo["duplicados_exatos"] + resumo["duplicados_provaveis"]:
        st.warning(
            f"{resumo['duplicados_exatos']} duplicata(s) exata(s) e "
            f"{resumo['duplicados_provaveis']} provável(is) ficaram fora dos relatórios "
            "aguardando sua confirmação na aba **Duplicidades**.",
            icon="🔁",
        )
    if resumo["pendentes"]:
        st.info(
            f"{resumo['pendentes']} lançamentos precisam de classificação manual — "
            "vá na tela **Classificação**.",
            icon="🏷️",
        )
    for aviso in avisos[:5]:
        st.caption(f"⚠️ {aviso}")


def _aba_mapa(engine) -> None:
    """Painel do que já foi carregado e do que falta, conta por mês."""
    c1, c2, _ = st.columns([1, 1.4, 2])
    meses = c1.selectbox("Período", [6, 12, 24], index=1,
                         format_func=lambda n: f"últimos {n} meses")
    incluir_inativas = c2.checkbox("Mostrar contas desativadas", value=False)

    hoje = date.today()
    competencias, ano, mes = [], hoje.year, hoje.month
    for _ in range(meses):
        competencias.append(f"{ano:04d}-{mes:02d}")
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    competencias.reverse()  # mais antigo à esquerda, como num calendário
    atual = f"{hoje.year:04d}-{hoje.month:02d}"

    with engine.connect() as conn:
        contas = repo.listar_contas(conn, so_ativas=not incluir_inativas)
        mapa = repo.cobertura(conn, competencias)
        mapa_planilha = repo.cobertura_planilha(conn, competencias)

    if not contas:
        st.warning("Nenhuma conta cadastrada.")
        return

    cabecalho = "".join(
        f"<th>{MESES_CURTOS[c[5:7]]}<br><span style='font-weight:400'>{c[2:4]}</span></th>"
        for c in competencias
    )

    # a planilha da carga inicial cobre o mês inteiro, de todas as contas, por
    # isso ela é uma linha à parte e não conta como arquivo faltando
    celulas_planilha = []
    for competencia in competencias:
        dados = mapa_planilha.get(competencia)
        classe = " class='futuro'" if competencia == atual else ""
        if dados:
            celulas_planilha.append(
                f"<td{classe}><span class='ok'>✓<small>{dados['total']}</small></span></td>"
            )
        else:
            celulas_planilha.append(f"<td{classe}><span class='falta'>·</span></td>")
    linha_planilha = (
        "<tr><td class='conta'><span class='nome'>Planilha (carga inicial)</span>"
        "<span class='tipo'>histórico da Rô</span></td>"
        + "".join(celulas_planilha) + "</tr>"
    )

    linhas, faltando = [], 0
    for conta in contas:
        celulas = []
        for competencia in competencias:
            dados = mapa.get((conta["id"], competencia))
            if competencia == atual:
                # mês em curso: o extrato ainda nem fechou, não conta como falta
                celulas.append(
                    f"<td class='futuro'><span class='ok'>✓<small>{dados['ativos']}</small>"
                    "</span></td>" if dados else "<td class='futuro'>em curso</td>"
                )
            elif not dados:
                faltando += 1
                celulas.append("<td><span class='falta'>·</span></td>")
            elif dados["ativos"] == 0:
                celulas.append("<td><span class='dup'>!<small>duplicado</small></span></td>")
            else:
                celulas.append(
                    f"<td><span class='ok'>✓<small>{dados['ativos']}</small></span></td>"
                )
        tipo = "cartão" if conta["tipo"] == "cartao" else "conta corrente"
        marca = "" if conta["ativa"] else " (inativa)"
        linhas.append(
            f"<tr><td class='conta'><span class='nome'>{conta['nome']}{marca}</span>"
            f"<span class='tipo'>{tipo} · {conta['titular']}</span></td>"
            + "".join(celulas) + "</tr>"
        )

    st.markdown(
        f"<div class='mapa'><table><tr><th class='conta'>Origem</th>{cabecalho}</tr>"
        + linha_planilha + "".join(linhas) + "</table></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<span class='nota'><b style='color:#14532D'>✓</b> carregado, com o número de "
        "lançamentos &nbsp;·&nbsp; <b>·</b> ainda não carregado &nbsp;·&nbsp; "
        "<b style='color:#9B1C1C'>!</b> importado, mas tudo caiu em duplicidade "
        "&nbsp;·&nbsp; hachurado = mês em curso, ainda não fechou<br>"
        "A planilha de carga inicial cobre todas as contas de uma vez, por isso fica "
        "numa linha só e não entra na conta do que falta.</span>",
        unsafe_allow_html=True,
    )

    if faltando:
        st.warning(
            f"Faltam **{faltando}** arquivos nos últimos {meses} meses, "
            "sem contar o mês em curso.",
            icon="📋",
        )
    else:
        st.success(f"Nada faltando nos últimos {meses} meses.", icon="✅")


def _aba_duplicidades(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        fila = dedup.pendentes(conn)

    if not fila:
        st.success("Nenhuma duplicidade pendente.", icon="✅")
        st.caption(
            "Quando o mesmo extrato for enviado duas vezes, os lançamentos repetidos aparecem "
            "aqui e ficam fora dos relatórios até você decidir."
        )
        return

    exatas = [linha for linha in fila if linha["tipo"] == "exata"]
    st.markdown(
        f"**{len(fila)} lançamento(s) aguardando decisão** — nenhum deles está entrando nos "
        "relatórios enquanto isso."
    )
    if exatas:
        if st.button(f"Confirmar exclusão das {len(exatas)} duplicatas exatas", type="primary"):
            with engine.begin() as conn:
                total = dedup.resolver_todas_exatas(conn, usuario["nome"])
            st.success(f"{total} duplicata(s) excluída(s).")
            st.rerun()

    for linha in fila:
        marca = "exata" if linha["tipo"] == "exata" else "provável"
        with st.container(border=True):
            c1, c2 = st.columns([3, 1.1])
            c1.markdown(
                f"<span class='pill p-alerta'>{marca}</span> "
                f"**{linha['nova_data']:%d/%m/%Y} · {linha['nova_descricao']}**<br>"
                f"<span class='nota'>{linha['conta']} · {fmt_brl(linha['nova_valor'])} · "
                f"{linha['motivo']}<br>já existe como #{linha['velha_id']} "
                f"({linha['velha_data']:%d/%m/%Y})</span>",
                unsafe_allow_html=True,
            )
            b1, b2 = c2.columns(2)
            if b1.button("Excluir", key=f"del{linha['dup_id']}", width="stretch"):
                with engine.begin() as conn:
                    dedup.resolver(conn, linha["dup_id"], "excluir", usuario["nome"])
                st.rerun()
            if b2.button("Manter", key=f"keep{linha['dup_id']}", width="stretch"):
                with engine.begin() as conn:
                    dedup.resolver(conn, linha["dup_id"], "manter", usuario["nome"])
                st.rerun()


def _aba_critica(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)

    st.caption(
        "A planilha da Rô é a carga inicial do histórico. Quando o extrato do mesmo período "
        "entra, o sistema confronta os dois — só compara meses em que as duas origens existem."
    )
    if critica["sem_conferencia"]:
        st.info(
            "Ainda não há um mesmo período com planilha **e** extrato importados. "
            "Importe a planilha e depois o extrato do mesmo mês para a crítica rodar.",
            icon="🔍",
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Conferidos", critica["conferidos"], "planilha = extrato", delta_color="off")
    c2.metric("Faltavam na planilha", len(critica["faltantes"]), "vieram do extrato",
              delta_color="off")
    c3.metric("Só na planilha", len(critica["so_planilha"]), "sem extrato", delta_color="off")
    c4.metric("Divergências", len(critica["divergencias"]), "valor ou data", delta_color="off")

    if critica["divergencias"]:
        st.markdown("#### Divergências — qual versão vale?")
        for item in critica["divergencias"]:
            planilha, extrato = item["planilha"], item["extrato"]
            with st.container(border=True):
                c1, c2 = st.columns([3, 1.1])
                c1.markdown(
                    f"**{planilha['descricao']}**<br>"
                    f"<span class='nota'>Planilha: {planilha['data']:%d/%m} · "
                    f"{fmt_brl(planilha['valor_centavos'])} &nbsp;|&nbsp; "
                    f"Extrato: {extrato['data']:%d/%m} · {fmt_brl(extrato['valor_centavos'])} "
                    f"(diferença de {fmt_brl(abs(item['diferenca']))})</span>",
                    unsafe_allow_html=True,
                )
                b1, b2 = c2.columns(2)
                if b1.button("Vale o extrato", key=f"dv{planilha['id']}",
                             width="stretch"):
                    reconcile.resolver_divergencia(
                        engine, planilha_id=planilha["id"], manter="extrato",
                        usuario=usuario["nome"],
                    )
                    st.rerun()
                if b2.button("Manter as duas", key=f"dm{planilha['id']}",
                             width="stretch"):
                    reconcile.resolver_divergencia(
                        engine, planilha_id=planilha["id"], manter="planilha",
                        usuario=usuario["nome"],
                    )
                    st.rerun()

    if critica["so_planilha"]:
        st.markdown("#### Só na planilha — pode ser gasto em dinheiro")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Data": f"{i['data']:%d/%m/%Y}", "Descrição": i["descricao"],
                     "Conta": i["conta"], "Valor": fmt_brl(i["valor_centavos"])}
                    for i in critica["so_planilha"]
                ]
            ),
            width="stretch", hide_index=True,
        )
        if st.button("Descartar todos os que só estão na planilha"):
            total = reconcile.descartar_da_planilha(
                engine, [i["id"] for i in critica["so_planilha"]], usuario["nome"]
            )
            st.success(f"{total} lançamento(s) descartado(s).")
            st.rerun()

    if critica["faltantes"]:
        st.markdown("#### Faltavam na planilha — já entraram pelo extrato")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Data": f"{i['data']:%d/%m/%Y}", "Descrição": i["descricao"],
                     "Conta": i["conta"], "Valor": fmt_brl(i["valor_centavos"])}
                    for i in critica["faltantes"][:100]
                ]
            ),
            width="stretch", hide_index=True,
        )


def _aba_contas(engine) -> None:
    with engine.connect() as conn:
        contas = repo.listar_contas(conn)

    st.caption(
        "Contas e cartões mudam com o tempo. Desativar tira a conta das opções de upload, "
        "mas o histórico dela continua em todos os relatórios."
    )
    for conta in contas:
        with st.container(border=True):
            c1, c2 = st.columns([3.2, 1])
            marca = "" if conta["ativa"] else " <span class='pill p-alerta'>inativa</span>"
            c1.markdown(
                f"**{conta['nome']}**{marca}<br><span class='nota'>"
                f"{'Cartão de crédito' if conta['tipo'] == 'cartao' else 'Conta corrente'} · "
                f"{conta['instituicao']} · leitor: "
                f"{instituicoes.ROTULOS.get(conta['parser'], conta['parser'])}</span> "
                f"{selo_pessoa(conta['titular'])}",
                unsafe_allow_html=True,
            )
            rotulo = "Desativar" if conta["ativa"] else "Reativar"
            if c2.button(rotulo, key=f"conta{conta['id']}", width="stretch"):
                repo.alternar_conta(engine, conta["id"], not conta["ativa"])
                st.rerun()

    with st.expander("➕ Incluir conta ou cartão"):
        with st.form("nova_conta"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Nome (como você chama)", placeholder="Ex.: Cartão Itaú Black")
            instituicao = c2.text_input("Instituição", placeholder="Ex.: Itaú")
            c3, c4, c5 = st.columns(3)
            tipo = c3.selectbox("Tipo", ["cartao", "corrente"],
                                format_func=lambda t: "Cartão de crédito" if t == "cartao"
                                else "Conta corrente")
            titular = c4.selectbox("Titular", db.PESSOAS)
            parser = c5.selectbox(
                "Leitor do arquivo", list(instituicoes.ROTULOS),
                format_func=lambda p: instituicoes.ROTULOS[p],
                index=list(instituicoes.ROTULOS).index("generico"),
            )
            if st.form_submit_button("Incluir", type="primary"):
                if not nome.strip() or not instituicao.strip():
                    st.error("Preencha nome e instituição.")
                else:
                    repo.salvar_conta(
                        engine, nome=nome, tipo=tipo, titular=titular,
                        instituicao=instituicao, parser=parser,
                    )
                    st.success(f"Conta {nome} incluída.")
                    st.rerun()


def _aba_historico(engine) -> None:
    with engine.connect() as conn:
        historico = repo.listar_uploads(conn)
    if not historico:
        st.caption("Nenhum arquivo importado ainda.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Arquivo": u["arquivo"], "Conta": u["conta"] or "—",
                    "Origem": u["origem"], "Competência": u["competencia"] or "—",
                    "Enviado por": u["enviado_por"], "Lidos": u["lidos"],
                    "Importados": u["importados"], "Auto": u["auto"],
                    "Pendentes": u["pendentes"], "Duplicados": u["duplicados"],
                }
                for u in historico
            ]
        ),
        width="stretch", hide_index=True,
    )
    with st.expander("Desfazer uma importação"):
        escolha = st.selectbox(
            "Arquivo", historico,
            format_func=lambda u: f"#{u['id']} · {u['arquivo']} ({u['importados']} lançamentos)",
        )
        st.caption("Apaga todos os lançamentos que entraram por esse arquivo. Não dá para desfazer.")
        if st.button("Desfazer importação", type="secondary"):
            total = repo.apagar_upload(engine, escolha["id"])
            st.success(f"{total} lançamento(s) removido(s).")
            st.rerun()


def render(engine, usuario: dict) -> None:
    with engine.connect() as conn:
        pendentes_dup = len(dedup.pendentes(conn))

    abas = st.tabs([
        "📤 Enviar arquivo",
        "🗓️ O que falta carregar",
        f"🔁 Duplicidades ({pendentes_dup})" if pendentes_dup else "🔁 Duplicidades",
        "🔍 Crítica planilha × extratos",
        "🏦 Contas e cartões",
        "🗂️ Histórico",
    ])
    with abas[0]:
        _aba_enviar(engine, usuario)
    with abas[1]:
        _aba_mapa(engine)
    with abas[2]:
        _aba_duplicidades(engine, usuario)
    with abas[3]:
        _aba_critica(engine, usuario)
    with abas[4]:
        _aba_contas(engine)
    with abas[5]:
        _aba_historico(engine)
