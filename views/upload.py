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

PAPEIS = ["data", "competencia", "descricao", "valor", "entrada", "saida", "categoria", "subcategoria",
          "pessoa", "tipo"]
ROTULOS_PAPEL = {
    "data": "Data (obrigatória)", "competencia": "Mês de referência", "descricao": "Descrição", "valor": "Valor único",
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
        ["Extrato ou fatura", "Planilha (carga inicial do histórico)"],
        horizontal=True,
    )
    e_planilha = origem.startswith("Planilha")
    contas = [c for c in contas if c["nome"] != repo.CONTA_PLANILHA]

    c1, c2 = st.columns(2)
    if e_planilha:
        # a planilha da casa mistura todas as contas e cartões, sem dizer de
        # qual saiu cada gasto; forçar a escolha de uma conta faria aquela
        # conta parecer dona de todo o histórico
        with engine.connect() as conn:
            conta = repo.conta_por_id(conn, repo.conta_da_planilha(engine))
        c1.info(
            "A carga inicial vai para uma conta própria — ela reúne todos os cartões e "
            "contas, e casa com os extratos por data e valor.",
            icon="📚",
        )
    else:
        conta = c1.selectbox(
            "Conta / cartão",
            contas,
            format_func=lambda c: f"{c['nome']} — {c['titular']}",
        )
    if e_planilha:
        # a carga inicial traz o ano inteiro: cada linha já diz o próprio mês.
        # Pedir uma competência aqui só induziria ao erro — ela sugere que o
        # arquivo é de um mês só, e o campo não tem efeito nenhum sobre ele.
        competencia = None
        c2.caption(
            "Sem competência: a planilha traz vários meses, e o mês de cada lançamento "
            "sai da própria linha."
        )
    else:
        competencia = c2.selectbox(
            "Competência", _competencias_sugeridas(),
            help="Mês de referência. Em extrato de conta corrente, cada lançamento usa a própria data.",
        )
    # só serve para completar data sem ano, o caso da fatura de cartão
    ano_referencia = int(competencia[:4]) if competencia else date.today().year

    if e_planilha:
        # planilha de receitas costuma ser de uma pessoa só; a da casa, do casal.
        # Sem isso, tudo entraria como "Casal" e o relatório por pessoa não
        # separaria o que é de quem
        pessoa_arquivo = st.radio(
            "De quem é este arquivo", db.PESSOAS, index=2, horizontal=True,
            help="Vale para as linhas que não tiverem uma coluna de pessoa.",
        )
    else:
        pessoa_arquivo = None

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
        # tabela cruzada: meses nas colunas, um tipo de lançamento por linha —
        # formato comum em planilha de salário e bônus
        meses_detectados = tabular.colunas_de_mes(df.columns)
        if meses_detectados:
            st.info(
                f"Reconheci **{len(meses_detectados)} colunas de mês** "
                f"({', '.join(list(meses_detectados)[:3])}…). Este arquivo parece uma tabela "
                "cruzada, com os meses nas colunas.",
                icon="📅",
            )
            cruzada = st.checkbox(
                "Ler como tabela cruzada (uma linha por mês de cada item)", value=True,
                key=f"{chave_estado}:cruzada",
            )
            if cruzada:
                coluna_desc = st.selectbox(
                    "Coluna que identifica o lançamento",
                    [c for c in df.columns if c not in meses_detectados],
                    key=f"{chave_estado}:desc_cruzada",
                    help="A coluna com os nomes das linhas: Salário, Bônus, etc.",
                )
                dia = st.number_input(
                    "Dia do mês a usar na data", min_value=1, max_value=28, value=1,
                    help="A tabela não diz o dia. Quando o extrato do mesmo mês entrar, "
                         "a versão dele prevalece.",
                    key=f"{chave_estado}:dia_cruzada",
                )
                try:
                    df = tabular.desempilhar(df, coluna_desc, dia=int(dia))
                except ErroDeLeitura as exc:
                    st.error(f"Não consegui desempilhar: {exc}")
                    return
                st.caption(
                    f"Desempilhado: **{len(df)} lançamentos** a partir de "
                    f"{len(meses_detectados)} meses. Colunas de total foram descartadas."
                )

        sugestao = st.session_state.get(chave_estado) or tabular.sugerir_mapeamento(df.columns, df)

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
            # a sugestão entra na chave do campo de propósito: sem isso o
            # Streamlit restaura a escolha da vez anterior para o mesmo arquivo
            # e a detecção nova é silenciosamente ignorada — foi assim que uma
            # planilha voltou a ser lida sem a coluna de despesa/receita
            escolha = colunas_ui[i % 3].selectbox(
                ROTULOS_PAPEL[papel], opcoes, index=indice,
                key=f"{chave_estado}:{papel}:{atual}",
            )
            mapa[papel] = None if escolha == "— nenhuma —" else escolha

        if not mapa.get("tipo") and not mapa.get("entrada") and not mapa.get("saida"):
            st.warning(
                "Nenhuma coluna indica se a linha é **despesa ou receita**. Se a planilha "
                "tiver uma coluna com DESP/REC (ou D/C), escolha-a em **Tipo (D/C)** — sem "
                "isso tudo entra com o mesmo sinal e os totais saem errados.",
                icon="⚠️",
            )

        # a inversão só faz sentido quando nada no arquivo diz o sinal. Com uma
        # coluna de tipo mapeada, os dois controles disputam a mesma decisão e
        # a inversão desfaz o que a coluna acabou de definir
        tem_coluna_de_sinal = bool(mapa.get("tipo") or mapa.get("entrada") or mapa.get("saida"))
        inverter = st.checkbox(
            "O valor vem positivo mesmo quando é gasto (comum em fatura de cartão)",
            value=False,
            disabled=tem_coluna_de_sinal,
            help=(
                "Desativado porque o próprio arquivo já diz o que é despesa e o que é receita, "
                "na coluna mapeada acima — inverter aqui desfaria isso."
                if tem_coluna_de_sinal
                else "Marque se, na prévia abaixo, as compras aparecerem como ENTRADA."
            ),
        )
        if tem_coluna_de_sinal:
            inverter = False

        # prévia do resultado, não do arquivo: mostra como cada linha vai ficar
        # depois de lida. É o único jeito de ver um erro de sinal ou de coluna
        # antes de gravar milhares de lançamentos e ter de desfazer tudo.
        try:
            previa, _ = tabular.extrair(
                df.head(8), mapa,
                origem="planilha" if e_planilha else "extrato",
                competencia=None if conta["tipo"] == "corrente" else competencia,
                ano_referencia=ano_referencia,
                inverter_sinal=inverter,
            )
        except ErroDeLeitura as exc:
            st.error(f"Com esse mapeamento não dá para ler: {exc}")
            return

        st.markdown("#### Como vai ficar")
        if not previa:
            st.error("Nenhuma linha foi reconhecida. Revise as colunas de data e valor.")
            return

        entradas = sum(1 for lan in previa if lan.valor_centavos > 0)
        st.dataframe(
            pd.DataFrame([
                {
                    "Data": f"{lan.data:%d/%m/%Y}",
                    "Descrição": lan.descricao,
                    "Entra ou sai": "↑ ENTRADA" if lan.valor_centavos > 0 else "↓ SAÍDA",
                    "Valor": fmt_brl(abs(lan.valor_centavos)),
                    "Categoria da origem": lan.categoria_hint or "—",
                }
                for lan in previa
            ]),
            width="stretch", hide_index=True,
        )
        if entradas == len(previa):
            st.warning(
                "**Todas as linhas da amostra estão como ENTRADA.** Numa planilha de gastos "
                "isso quase nunca está certo — confira a coluna **Tipo (D/C)**.",
                icon="⚠️",
            )
        else:
            st.caption(
                "Confira a coluna **Entra ou sai** antes de processar: é ela que decide se o "
                "lançamento soma nas receitas ou nas despesas."
            )

        if st.button("Processar arquivo", type="primary"):
            try:
                lancamentos, avisos = tabular.extrair(
                    df, mapa,
                    origem="planilha" if e_planilha else "extrato",
                    competencia=None if conta["tipo"] == "corrente" else competencia,
                    ano_referencia=ano_referencia,
                    inverter_sinal=inverter,
                )
            except ErroDeLeitura as exc:
                st.error(f"Não consegui ler: {exc}")
                return
            _importar(engine, conta, lancamentos, arquivo.name, usuario,
                      "planilha" if e_planilha else "extrato", competencia, avisos,
                      pessoa_padrao=pessoa_arquivo)
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


def _importar(engine, conta, lancamentos, nome_arquivo, usuario, origem, competencia,
              avisos, pessoa_padrao=None):
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
            pessoa_padrao=pessoa_padrao,
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
            f"{resumo['duplicados_exatos']} duplicata(s) exata(s) — repetição de arquivo já "
            f"enviado, essas ficaram fora dos relatórios — e {resumo['duplicados_provaveis']} "
            "suspeita(s) provável(is), que **continuam contando** até você decidir. "
            "Revise na aba **Duplicidades**.",
            icon="🔁",
        )
        st.caption(
            "Suspeita provável entra valendo de propósito: assim o total logo após o upload "
            "bate com o do arquivo, e nenhuma diferença aparece sem explicação."
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
        contas = [c for c in repo.listar_contas(conn, so_ativas=not incluir_inativas)
                  if c['nome'] != repo.CONTA_PLANILHA]
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
            "aqui para você decidir."
        )
        return

    exatas = [linha for linha in fila if linha["tipo"] == "exata"]
    provaveis = [linha for linha in fila if linha["tipo"] != "exata"]
    st.markdown(f"**{len(fila)} lançamento(s) aguardando decisão.**")
    st.caption(
        f"As {len(exatas)} **exatas** repetem arquivo já enviado e estão fora dos relatórios. "
        f"As {len(provaveis)} **prováveis** continuam contando: são só suspeitas, e tirá-las "
        "sozinho faria o total do mês ficar menor que o do arquivo sem nada explicar."
    )
    b1, b2, _ = st.columns([1.5, 1.5, 1])
    if exatas and b1.button(f"Excluir as {len(exatas)} duplicatas exatas", type="primary",
                            width="stretch"):
        with engine.begin() as conn:
            total = dedup.resolver_em_lote(conn, "exata", "excluir", usuario["nome"])
        st.success(f"{total} duplicata(s) excluída(s).")
        st.rerun()
    if provaveis and b2.button(f"Manter as {len(provaveis)} prováveis (são gastos distintos)",
                               width="stretch"):
        with engine.begin() as conn:
            total = dedup.resolver_em_lote(conn, "provavel", "manter", usuario["nome"])
        st.success(f"{total} lançamento(s) devolvido(s) aos relatórios.")
        st.rerun()

    if provaveis:
        st.caption(
            "As **prováveis** costumam ser gastos legítimos parecidos — dois cafés iguais no "
            "mesmo dia, duas idas à padaria. Numa planilha de família, em que a descrição é "
            "curta e digitada à mão, elas aparecem bastante. Revise algumas abaixo; se o padrão "
            "se confirmar, use o botão para devolver todas de uma vez."
        )

    for linha in fila[:60]:
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

    if len(fila) > 60:
        st.caption(
            f"Mostrando 60 de {len(fila)}. Resolva estes ou use os botões acima para decidir "
            "todos de uma vez."
        )


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
