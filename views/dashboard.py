"""Visão Geral: cards do mês, categoria x meta, evolução e tabelas."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import analytics, db, reconcile, repo
from core.money import fmt_brl, fmt_mil
from ui import dados, graficos
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
            else f"média {fmt_brl(ano_todo[chave] // meses)}/mês em {meses} meses"
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
        "<div class='quadro'><table class='resumo'><thead><tr>"
        "<th></th>"
        f"<th class='grupo'>{rotulo_mes}</th>"
        f"<th class='grupo'>Ano {ano} · acumulado</th>"
        "</tr></thead><tbody>" + "".join(linhas) + "</tbody></table></div>"
    )


def _tom_de_calor(valor: int, maximo: int) -> str:
    """Fundo da célula: um matiz só, do claro ao vinho, conforme o peso.

    Rampa de uma cor porque o que ela codifica é magnitude, não identidade —
    matizes diferentes fariam o olho procurar um significado que não existe. O
    passo é discreto, cinco degraus, para as faixas serem distinguíveis em vez
    de virarem um borrão contínuo.
    """
    if not maximo or valor <= 0:
        return ""
    faixa = min(int(valor / maximo * 5), 4)   # 0..4
    return ["#FBF6F1", "#F3E3E4", "#E7C9CD", "#D9A8AF", "#C6838D"][faixa]


def _matriz_mes_a_mes(matriz: dict, ano: int) -> str:
    """Categoria × mês, em HTML, inteira na tela.

    Era um dataframe do Streamlit, que rola por dentro: as últimas categorias
    ficavam escondidas justo quando são elas que se quer ver. Em HTML a tabela
    aparece inteira, e sobra o fundo das células para dizer onde o dinheiro
    pesou sem ninguém precisar comparar números um a um.
    """
    meses = matriz["meses"]
    linhas = matriz["linhas"]
    cabecalho = (
        "<tr><th class='cat'>Categoria</th>"
        + "".join(f"<th>{graficos.MESES_PT.get(m, m)}</th>" for m in meses)
        + f"<th class='fecha'>Acum. {ano}</th><th>Média/mês</th>"
        + f"<th>Total {ano - 1}</th></tr>"
    )

    corpo = []
    for linha in linhas:
        valores = [linha["meses"][m] for m in meses]
        maximo = max(valores) if valores else 0
        celulas = []
        for valor in valores:
            if not valor:
                celulas.append("<td class='zero'>—</td>")
                continue
            fundo = _tom_de_calor(valor, maximo)
            pico = " pico" if valor == maximo and maximo > 0 else ""
            celulas.append(
                f"<td class='calor{pico}' style='background:{fundo}'>{fmt_mil(valor)}</td>"
            )
        anterior = fmt_mil(linha["ano_anterior"]) if linha["ano_anterior"] else "—"
        corpo.append(
            f"<tr><td class='cat' title='{linha['categoria']}'>{linha['categoria']}</td>"
            + "".join(celulas)
            + f"<td class='fecha'>{fmt_mil(linha['acumulado'])}</td>"
            + f"<td>{fmt_mil(linha['media'])}</td><td>{anterior}</td></tr>"
        )

    total_mes = [sum(l["meses"][m] for l in linhas) for m in meses]
    acumulado = sum(l["acumulado"] for l in linhas)
    corpo.append(
        "<tr class='total'><td class='cat'>TOTAL</td>"
        + "".join(f"<td>{fmt_mil(v) if v else '—'}</td>" for v in total_mes)
        + f"<td class='fecha'>{fmt_mil(acumulado)}</td>"
        # pelos meses decorridos, como nas linhas de cima. Dividir pelo número
        # de colunas contava setembro e outubro, que só aparecem na tabela por
        # causa de um agendamento de R$ 200 — e a média do TOTAL saía menor que
        # a soma das médias das categorias, o que é impossível
        + f"<td>{fmt_mil(acumulado // max(analytics.meses_decorridos(ano), 1))}</td>"
        + f"<td>{fmt_mil(sum(l['ano_anterior'] for l in linhas)) or '—'}</td></tr>"
    )
    return (
        "<div class='quadro' style='max-width:none'><table class='matriz'>"
        f"<thead>{cabecalho}</thead><tbody>{''.join(corpo)}</tbody></table></div>"
    )


def _competencia_de_abertura(engine, competencias: list[str]) -> str:
    """Em que mês a tela abre.

    A planilha traz lançamento agendado até dezembro, então o mês mais recente
    da base é um mês que ainda não aconteceu: abrir nele mostrava despesa zero
    e um gráfico vazio, como se a casa não tivesse gastado nada. Abre no mês de
    hoje; se ele ainda não tiver gasto lançado, no último que teve.
    """
    hoje = date.today().strftime("%Y-%m")
    passados = [c for c in competencias if c <= hoje]
    # uma consulta para todos os meses. Perguntar mês a mês custava até doze
    # idas ao banco só para decidir onde a tela abre
    com_gasto = dados.meses_com_despesa(engine, dados.versao())
    if hoje in com_gasto:
        return hoje
    for competencia in passados:   # a lista vem do mais recente para o mais antigo
        if competencia in com_gasto:
            return competencia
    return passados[0] if passados else competencias[0]


def _reais(centavos: int) -> str:
    return fmt_brl(centavos).replace("$", chr(92) + "$")


def _auditoria_das_despesas(engine, usuario: dict, competencia: str) -> None:
    """Por onde a despesa deste mês pode estar dobrando — e o conserto ao lado.

    Extrato de conta corrente chegando em cima da planilha e das faturas dobra
    despesa de três jeitos, e nenhum é erro de digitação: o mesmo gasto na
    planilha e no extrato; o pagamento da fatura somado às compras que já
    estão nela; dinheiro andando entre as contas da casa. Cada um aparece
    aqui com o botão que resolve.
    """
    dados_auditoria = dados.auditoria_do_mes(engine, dados.versao(), competencia)
    previsto, realizado = dados_auditoria["previsto"], dados_auditoria["realizado"]
    transferencias = dados_auditoria["transferencias"]
    pagamentos = dados_auditoria["pagamentos_de_fatura"]
    duplicatas = dados_auditoria["duplicatas"]
    maiores = dados_auditoria.get("maiores") or []
    encerrada = dados_auditoria.get("critica_encerrada", False)
    tem_problema = bool((previsto and realizado) or transferencias or pagamentos or duplicatas)
    raio_x = dados_auditoria.get("raio_x") or {}
    if not tem_problema and not maiores and not raio_x.get("valendo"):
        return

    mes = f"{graficos.rotulo_mes(competencia).lower()}/{competencia[2:4]}"
    with st.expander(f"🔎 Por que a despesa de {mes} está assim — auditoria", expanded=True):
        if maiores:
            st.markdown("**Os maiores gastos do mês — de onde vieram, e com o que casam**")
            st.caption(
                "Ordenados por valor. **Casa com** diz se o mesmo valor existe em outro lugar: "
                "na planilha do mês, num “pagamento recebido” de cartão, num crédito de outra "
                "conta da casa, no total de uma fatura. Linha com “—” não casa com nada."
            )
            st.dataframe(
                pd.DataFrame([
                    {"Data": f"{l['data']:%d/%m}", "Conta": l["conta"],
                     "Descrição": l["descricao"][:44], "Categoria": l["categoria"] or "—",
                     "Valor": fmt_brl(-l["valor_centavos"]), "Casa com": l["casa_com"],
                     "Arquivo": l["arquivo"]}
                    for l in maiores
                ]),
                width="stretch", hide_index=True,
            )

        # no mes em curso a planilha e previsao, e previsao vale ate o extrato
        # chegar: nao ha "valendo juntos" para alarmar
        em_curso = competencia >= date.today().strftime("%Y-%m")
        if previsto and realizado and em_curso:
            pass
        elif previsto and realizado and encerrada:
            # o dono ja olhou e disse "fica como esta": informacao, nao alarme
            st.info(
                f"Em {mes} valem {_reais(previsto)} da **planilha** ao lado de "
                f"{_reais(realizado)} dos **extratos** — conferido por você na Crítica "
                "(gasto em dinheiro ou de conta fora do sistema). Para rever, reabra em "
                "**Upload → 🔍 Crítica planilha × extratos**.",
                icon="✅",
            )
        elif previsto and realizado:
            st.error(
                f"**Planilha e extratos valendo juntos.** Em {mes} há "
                f"{_reais(previsto)} de despesa vinda da **planilha** e {_reais(realizado)} "
                "vinda de **extratos**. O mesmo gasto nas duas conta duas vezes. O "
                "pente-fino, gasto a gasto, está em **Upload → 🔍 Crítica planilha × "
                "extratos** — ela mostra o que bateu, o que divergiu e o que só a planilha "
                "tem, com um botão para cada caso.",
                icon="📚",
            )
            # a conferencia pelo valor ja acontece na importacao; o que sobra
            # se decide na Critica, que tem os botoes certos e o "fica como esta"
            st.dataframe(
                pd.DataFrame([
                    {"Origem": i["origem"], "Conta": i["conta"],
                     "Lançamentos": i["quantos"], "Despesa": fmt_brl(i["total"])}
                    for i in dados_auditoria["por_origem"]
                ]),
                width="stretch", hide_index=True,
            )

        if pagamentos:
            total = -sum(l["valor_centavos"] for l in pagamentos)
            st.warning(
                f"**{len(pagamentos)} pagamento(s) de fatura contados como despesa** "
                f"({_reais(total)}). As compras já são despesa na fatura; o pagamento é só "
                "o dinheiro saindo para cobri-las.",
                icon="💳",
            )
            st.dataframe(
                pd.DataFrame([
                    {"Data": f"{l['data']:%d/%m}", "Conta": l["conta"],
                     "Descrição": l["descricao"], "Valor": fmt_brl(-l["valor_centavos"])}
                    for l in pagamentos
                ]),
                width="stretch", hide_index=True,
            )
            if st.button("Marcar como pagamento de fatura (transferência)",
                         key=f"aud_fatura_{competencia}"):
                repo.marcar_transferencia(
                    engine, [l["id"] for l in pagamentos], usuario["nome"],
                    subcategoria="Pagamento de Fatura",
                )
                st.rerun()

        if transferencias:
            total = sum(p["valor"] for p in transferencias)
            st.warning(
                f"**{len(transferencias)} transferência(s) entre contas da casa** "
                f"({_reais(total)}): saída numa conta, entrada noutra, mesmo valor, até três "
                "dias. Contadas como despesa de um lado e receita do outro.",
                icon="🔁",
            )
            st.dataframe(
                pd.DataFrame([
                    {"Saiu de": p["saida"]["conta"], "Em": f"{p['saida']['data']:%d/%m}",
                     "Entrou em": p["entrada"]["conta"],
                     "Descrição (saída)": p["saida"]["descricao"],
                     "Valor": fmt_brl(p["valor"])}
                    for p in transferencias
                ]),
                width="stretch", hide_index=True,
            )
            if st.button("Marcar os dois lados como transferência",
                         key=f"aud_transf_{competencia}"):
                ids = [p["saida"]["id"] for p in transferencias] + \
                      [p["entrada"]["id"] for p in transferencias]
                repo.marcar_transferencia(engine, ids, usuario["nome"])
                st.rerun()

        if duplicatas:
            total = sum(abs(d["copia"]["valor_centavos"]) for d in duplicatas)
            st.warning(
                f"**{len(duplicatas)} lançamento(s) em duplicidade exata** ({_reais(total)}): "
                "mesma conta, dia, valor e descrição, duas vezes.",
                icon="📄",
            )
            st.dataframe(
                pd.DataFrame([
                    {"Data": f"{d['copia']['data']:%d/%m}", "Conta": d["copia"]["conta"],
                     "Descrição": d["copia"]["descricao"],
                     "Valor": fmt_brl(d["copia"]["valor_centavos"])}
                    for d in duplicatas
                ]),
                width="stretch", hide_index=True,
            )
            if st.button("Desativar as cópias", key=f"aud_dup_{competencia}"):
                repo.desativar_transacoes(
                    engine, [d["copia"]["id"] for d in duplicatas],
                    f"duplicidade exata desativada na auditoria por {usuario['nome']}",
                )
                st.rerun()

        _raio_x(dados_auditoria.get("raio_x") or {}, mes)


def _raio_x(raio_x: dict, mes: str) -> None:
    """De onde vem cada real do mês — a resposta que antes só o SQL dava."""
    valendo, planilha = raio_x.get("valendo") or [], raio_x.get("planilha") or []
    if not valendo and not planilha:
        return
    st.markdown(f"**Raio-X de {mes} — o que está valendo, por origem, conta e lado**")
    st.caption(
        "Um print desta tabela diz o que compõe cada total do mês. *Entrada* soma na "
        "receita, *saída* na despesa, *transferência* fica fora dos dois."
    )
    if valendo:
        st.dataframe(
            pd.DataFrame([
                {"Origem": i["origem"], "Conta": i["conta"], "Lado": i["lado"],
                 "Categoria": i["categoria"], "Lançamentos": i["quantos"],
                 "Total": fmt_brl(i["total"])}
                for i in valendo
            ]),
            width="stretch", hide_index=True,
        )
    if planilha:
        ativas = sum(1 for l in planilha if l["ativo"])
        st.markdown(
            f"**A planilha de {mes}, linha a linha** — {ativas} valendo, "
            f"{len(planilha) - ativas} aposentada(s), cada uma com o motivo"
        )
        st.dataframe(
            pd.DataFrame([
                {"Vale": "sim" if l["ativo"] else "não",
                 "Data": f"{l['data']:%d/%m}", "Descrição": l["descricao"][:40],
                 "Valor": fmt_brl(l["valor_centavos"]), "Pessoa": l["pessoa"] or "—",
                 "Categoria": l["categoria"] or "—", "Motivo": l["observacao"] or "—"}
                for l in planilha
            ]),
            width="stretch", hide_index=True,
        )


def render(engine, usuario: dict) -> None:
    competencias = dados.competencias(engine, dados.versao())

    # a conexão fica aberta uma vez para a tela inteira: o resto da leitura vem
    # do cache, e só a categoria explodida ainda precisa perguntar ao banco
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

    painel = dados.painel_do_mes(engine, dados.versao(), competencia, pessoa)
    atual = painel["atual"]
    serie = painel["serie"]
    categorias = painel["categorias"]
    metas = painel["metas"]
    orcamento = painel["orcamento"]
    matriz = painel["matriz"]
    anual = painel["anual"]
    acumulado = painel["acumulado"]

    # O mês anterior é o do calendário, e ele é nomeado no rótulo.
    #
    # Antes isto era `serie[posicao - 1]` — o mês anterior *que tem lançamento*.
    # `serie_mensal` só devolve competências com movimento, então um buraco na
    # série fazia setembro ser comparado com julho sob o rótulo "mês anterior".
    # E há um buraco garantido neste ano: agosto tem só a previsão lançada à
    # mão, e os extratos reais só começaram a chegar em setembro. Um "+107,2%
    # vs mês anterior" contra um mês pela metade se lê como renda dobrada,
    # ainda que nada tenha dobrado — foi o que mandou a casa procurar um bug
    # onde talvez só houvesse uma comparação torta.
    passado = analytics.mes_anterior(competencia)
    anterior = next((m for m in serie if m["competencia"] == passado), None)

    def variacao(chave: str) -> str | None:
        if not anterior or not anterior[chave]:
            return None
        delta = (atual[chave] / anterior[chave] - 1) * 100
        return f"{delta:+.1f}% vs {graficos.rotulo_mes(passado).lower()}/{passado[2:4]}"

    meta_poupanca = next(
        (o["meta"] for o in orcamento if o["categoria"] == analytics.CATEGORIA_POUPANCA), 0
    )
    # meses que já aconteceram — a planilha traz lançamento agendado até
    # dezembro, e contar esses meses fazia a média mensal encolher
    meses_no_ano = analytics.meses_decorridos(ano)
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

    # A sentinela. Cartao nao gera receita; se alguma linha de cartao esta do
    # lado da renda, em qualquer mes, a tela diz de qual cartao e quanto — em
    # vez de deixar um numero estranho para alguem desconfiar
    suspeitas = painel.get("receita_em_cartao") or []
    if suspeitas:
        linhas = "\n".join(
            f"- **{item['conta']}** · {item['competencia']} · {item['quantos']} lançamento(s) · "
            f"{fmt_brl(item['total']).replace('$', chr(92) + '$')}"
            for item in suspeitas[:8]
        )
        st.error(
            "**Dinheiro de cartão contado como receita.** Cartão não gera renda: o que "
            "entra é compra, e o crédito que aparece é estorno ou pagamento da fatura. "
            "Ou o arquivo foi lido ao contrário (Upload → Histórico → *Corrigir o sinal*), "
            "ou um estorno foi classificado como receita (reclassifique em "
            "**Classificação**).\n\n" + linhas,
            icon="💳",
        )

    entrada = atual.get("sem_categoria_entrada", 0)
    saida = atual.get("sem_categoria_saida", 0)
    estorno = atual.get("sem_categoria_estorno", 0)
    if entrada or saida or estorno:
        # os dois lados, nunca o liquido: "19 mil sem categoria" escondia 39 mil
        # entrando e 58 mil saindo, cada um somado no seu total
        partes = []
        if saida:
            partes.append(f"{_reais(saida)} de saída (já somados na despesa)")
        if entrada:
            partes.append(f"{_reais(entrada)} de entrada (já somados na receita)")
        if estorno:
            partes.append(f"{_reais(estorno)} de crédito em cartão (já abatidos da despesa)")
        st.warning(
            "Ainda sem categoria neste mês: " + " e ".join(partes) + ". "
            "Resolva na tela **Classificação** para os números fecharem.",
            icon="🏷️",
        )

    _auditoria_das_despesas(engine, usuario, competencia)

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
        st.markdown(_matriz_mes_a_mes(matriz, ano), unsafe_allow_html=True)
        st.markdown(
            "<p class='nota'>Valores em R$ mil. O fundo escurece com o peso do mês "
            "<b>dentro da própria categoria</b> — a leitura é ao longo da linha, não "
            "entre linhas. O contorno marca o mês mais pesado de cada conta. A poupança "
            "aparece como categoria própria e não entra no total de despesas.</p>",
            unsafe_allow_html=True,
        )

    # ---- explodir uma categoria -------------------------------------------
    st.markdown("### Abrir uma categoria")
    st.caption(
        "A matriz acima para na categoria. Aqui ela se abre: onde dentro de "
        "**Alimentação** o dinheiro foi, mês a mês."
    )
    nomes = [linha["categoria"] for linha in matriz["linhas"]]
    if nomes:
        c1, c2 = st.columns([2, 1.2])
        escolhida = c1.selectbox("Categoria", nomes, key="explodir")
        escopo = c2.radio(
            "Período", [f"Ano {ano}", f"Só {graficos.rotulo_mes(competencia)}"],
            horizontal=True, key="escopo_explodir",
        )
        alvo = next(
            (c for c in categorias if c["categoria"] == escolhida),
            None,
        )
        categoria_id = alvo["categoria_id"] if alvo else None
        if categoria_id is None:
            plano = dados.plano_de_contas(engine, dados.versao(), natureza="despesa")
            categoria_id = next((c["id"] for c in plano if c["nome"] == escolhida), None)

        if categoria_id:
            do_ano = escopo.startswith("Ano")
            explodida = dados.categoria_explodida(
                engine, dados.versao(), categoria_id, ano,
                None if do_ano else competencia, pessoa,
            )
            aberto, fatias = explodida["aberto"], explodida["fatias"]
            if not fatias:
                st.caption("Nada lançado nesta categoria no período.")
            else:
                total = sum(f["total"] for f in fatias)
                esquerda, direita = st.columns([1.6, 1])
                with esquerda:
                    if do_ano and aberto["linhas"]:
                        st.markdown(_matriz_mes_a_mes(aberto, ano), unsafe_allow_html=True)
                    else:
                        st.markdown(
                            "".join(
                                _barra_categoria(
                                    {"categoria": f["subcategoria"], "realizado": f["total"],
                                     "meta": 0, "estourou": False, "meta_e_piso": False,
                                     "abaixo_do_piso": False},
                                    max(f["total"] for f in fatias),
                                )
                                for f in fatias
                            ),
                            unsafe_allow_html=True,
                        )
                with direita:
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "Subcategoria": f["subcategoria"],
                                "Total": fmt_brl(f["total"]),
                                "%": f"{f['total'] / total * 100:.0f}%" if total else "—",
                                "Lanç.": f["qtd"],
                            }
                            for f in fatias
                        ]),
                        width="stretch", hide_index=True,
                    )
                linhas = explodida.get("linhas") or []
                if not do_ano and linhas:
                    with st.expander(
                        f"Os {len(linhas)} lançamento(s) de {escolhida} em "
                        f"{graficos.rotulo_mes(competencia).lower()}/{competencia[2:4]}"
                    ):
                        st.dataframe(
                            pd.DataFrame([
                                {"Data": f"{l['data']:%d/%m}", "Conta": l["conta"],
                                 "Descrição": l["descricao"][:48],
                                 "Subcategoria": l["subcategoria"] or "—",
                                 "Valor": fmt_brl(abs(l["valor_centavos"])),
                                 "Arquivo": l["arquivo"] or l["origem"]}
                                for l in sorted(linhas, key=lambda l: l["valor_centavos"])
                            ]),
                            width="stretch", hide_index=True,
                        )
                sem_detalhe = next((f for f in fatias if not f["detalhada"]), None)
                if sem_detalhe:
                    aviso, botao = st.columns([3, 1.2])
                    aviso.caption(
                        f"⚠️ {fmt_brl(sem_detalhe['total'])} em "
                        f"{sem_detalhe['qtd']} lançamento(s) ainda **sem subcategoria** — "
                        "é o que falta detalhar para esta abertura ficar completa."
                    )
                    # apontar o problema sem dar o caminho é metade do serviço:
                    # o lançamento não está na fila (já tem categoria) e caçá-lo
                    # pela busca exige saber o nome, que é o que não se sabe
                    if botao.button("Classificar agora", key="ir_sem_sub",
                                    type="primary", width="stretch"):
                        st.session_state["tela"] = "Classificação"
                        st.session_state["secao_classificacao"] = "sem_sub"
                        st.session_state["foco_sem_sub"] = {
                            "competencia": None if do_ano else competencia,
                            "categoria_id": categoria_id,
                        }
                        st.rerun()

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
