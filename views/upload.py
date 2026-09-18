"""Upload de extratos, conferência de duplicidades, crítica e cadastro de contas."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import dedup, db, reconcile, repo
from core.money import fmt_brl
from core.texto import sem_marcacao
from parsers import extrato_bradesco, extrato_itau, instituicoes, pdf, tabular
from parsers import pdf as leitor_pdf
from views import manual
from parsers.base import ErroDeLeitura, ajustar_ano_fatura, competencia_predominante
from ui import dados, graficos
from ui.graficos import MESES_PT as MESES_CURTOS
from ui.tema import selo_pessoa

def _reais(centavos: int) -> str:
    """Valor pronto para entrar em texto markdown.

    Um par de cifrões vira fórmula LaTeX no Streamlit, e o "R$" some junto com
    o pedaço da frase entre eles.
    """
    return fmt_brl(centavos).replace("$", r"\$")


PAPEIS = ["data", "competencia", "descricao", "valor", "entrada", "saida", "categoria", "subcategoria",
          "pessoa", "tipo"]
ROTULOS_PAPEL = {
    "data": "Data (obrigatória)", "competencia": "Mês de referência", "descricao": "Descrição", "valor": "Valor único",
    "entrada": "Entrada / crédito", "saida": "Saída / débito",
    "categoria": "Categoria (se a planilha já tiver)", "subcategoria": "Subcategoria",
    "pessoa": "Pessoa", "tipo": "Tipo (D/C)",
}


# A lista de competências vai para os dois lados, e larga.
#
# Para a frente porque fatura de cartão fecha num mês e vence no seguinte: a
# compra de agosto entra na fatura que vence em setembro, e é setembro a
# competência dela. A lista começava no mês de hoje, então esse upload
# simplesmente não tinha como ser feito.
#
# Para trás porque histórico não tem prazo: extrato antigo, ano fechado que se
# resolve recuperar, a fatura que ficou esquecida na gaveta.
#
# Dois anos à frente e cinco atrás dão oitenta e quatro opções. Parece muito
# para um menu, mas o campo aceita digitação: escrever "2027-03" filtra a
# lista na hora, e rolar até lá continua possível para quem preferir.
MESES_A_FRENTE = 24
MESES_PARA_TRAS = 60


def _passo_de_mes(ano: int, mes: int, passo: int) -> tuple[int, int]:
    total = (ano * 12 + mes - 1) + passo
    return total // 12, total % 12 + 1


def _competencias_sugeridas() -> list[str]:
    """Do mês mais adiantado para o mais antigo, com o mês de hoje no meio."""
    hoje = date.today()
    ano, mes = _passo_de_mes(hoje.year, hoje.month, MESES_A_FRENTE)
    saida = []
    for _ in range(MESES_A_FRENTE + MESES_PARA_TRAS):
        saida.append(f"{ano:04d}-{mes:02d}")
        ano, mes = _passo_de_mes(ano, mes, -1)
    return saida


def _aba_enviar(engine, usuario: dict) -> None:
    contas = dados.contas(engine, dados.versao(), so_ativas=True)
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
    elif conta["tipo"] == "corrente":
        # em conta corrente cada lançamento usa a própria data, e o menu não
        # mudava nada — exceto o registro do upload, que guardava o mês de hoje
        # e fazia o mapa dizer que setembro estava carregado com agosto. Sem
        # menu, sem armadilha: o mês sai do arquivo
        competencia = None
        c2.caption(
            "Sem competência: em conta corrente cada lançamento usa a própria data, e o "
            "mês do arquivo é o que os lançamentos disserem."
        )
    else:
        competencia = c2.selectbox(
            "Mês da fatura", _competencias_sugeridas(),
            # abre no mês de hoje, não no primeiro da lista: os meses à frente
            # existem para a fatura de cartão, e não são o caso comum
            index=MESES_A_FRENTE,
            help="O mês em que a fatura vence. Cada compra conta no mês da própria data "
                 "(a compra de julho é de julho, mesmo na fatura de agosto); o mês da "
                 "fatura completa o ano das datas e diz ao mapa qual fatura entrou. "
                 "Dá para digitar o mês (“2027-03”) em vez de rolar.",
        )
    # só serve para completar data sem ano, o caso da fatura de cartão
    ano_referencia = int(competencia[:4]) if competencia else date.today().year

    if e_planilha:
        # planilha de receitas costuma ser de uma pessoa só; a da casa, do casal.
        # Sem isso, tudo entraria como "Casal" e o relatório por pessoa não
        # separaria o que é de quem
        pessoa_arquivo = st.radio(
            "De quem são as receitas deste arquivo", db.PESSOAS, index=2, horizontal=True,
            help="Vale só para as receitas que não disserem de quem são. Despesa sem dono "
                 "declarado é sempre da casa.",
        )
        st.caption(
            "**Despesa sem dono declarado entra como Casal**, sempre. Só sai disso o que "
            "diz de quem é — o nome na descrição (“ALMOÇO ANDRÉ”), uma regra, ou uma "
            "categoria que é de uma pessoa (Filhos & Pensão)."
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
        # "Tipo" no cabeçalho não quer dizer D/C — na fatura de cartão a coluna
        # com esse nome traz "à vista"/"parcelado". Aceitá-la pelo nome fazia o
        # sistema achar que o arquivo declarava o lado de cada linha, e isso
        # desligava a inversão da fatura. Aqui vale o conteúdo, inclusive quando
        # a coluna foi escolhida à mão no campo acima.
        if mapa.get("tipo") and not tabular.coluna_diz_o_sinal(df[mapa["tipo"]]):
            st.caption(
                f"A coluna **{mapa['tipo']}** não traz despesa/receita (D/C) — só um rótulo "
                "como “à vista” ou “parcelado”. Ela não vai decidir o sinal."
            )
            mapa = {**mapa, "tipo": None}
        tem_coluna_de_sinal = bool(mapa.get("tipo") or mapa.get("entrada") or mapa.get("saida"))
        # Num cartão o sinal não é pergunta: a fatura exporta a compra positiva,
        # e quem vem negativo é estorno ou o pagamento da própria fatura. Os
        # leitores por banco (parsers/instituicoes) já aplicavam essa regra
        # sozinhos; este caminho — o mapeamento manual de colunas, onde cai a
        # conta de leitor genérico — era o único que ainda perguntava, e
        # perguntava com a resposta errada já marcada. Foi por aqui que a
        # fatura de setembro entrou inteira como receita.
        #
        # Por isso a regra virou o padrão e a exceção virou a caixa: esquecer
        # de marcar passou a dar no caso certo, não no errado.
        e_cartao = conta["tipo"] == "cartao"
        if tem_coluna_de_sinal:
            inverter = False
            st.caption(
                "O próprio arquivo diz o que é despesa e o que é receita, na coluna "
                "mapeada acima — é ela que manda no sinal."
            )
        elif e_cartao:
            positivo_e_gasto = tabular.positivo_e_gasto(df, mapa)
            if positivo_e_gasto:
                st.info(
                    "**Fatura de cartão: o valor positivo é compra.** Neste arquivo a maioria "
                    "das linhas vem positiva, então elas entram como **despesa**, e só o que "
                    "vem negativo (estorno, pagamento da fatura) entra como crédito.",
                    icon="💳",
                )
            excecao = st.checkbox(
                "Não é o caso deste arquivo: aqui a compra já vem negativa",
                value=False,
                key=f"{chave_estado}:excecao_sinal",
                help="Só marque se a prévia abaixo mostrar as compras como ENTRADA mesmo "
                     "depois do aviso acima. O normal numa fatura é não mexer aqui.",
            )
            inverter = positivo_e_gasto and not excecao
        elif tabular.positivo_e_gasto(df, mapa):
            # Conta corrente com um arquivo quase todo positivo. Prender a
            # proteção ao tipo da conta foi o erro que deixou a fatura passar
            # pela segunda vez: ela foi enviada na conta corrente, e ali nada
            # disparava — nem a inversão automática, nem a trava do gravador,
            # que também olha o tipo da conta.
            #
            # Aqui o arquivo é genuinamente ambíguo: pode ser a lista de
            # recebimentos da Rô (positivo é receita mesmo) ou uma fatura de
            # cartão enviada na conta errada. Adivinhar erra metade das vezes,
            # então esta é a única pergunta do sistema sem resposta pronta — e
            # ela tranca o botão até ser respondida.
            proporcao = tabular.proporcao_positiva(df, mapa) or 0
            st.warning(
                f"**{proporcao:.0%} das linhas deste arquivo vêm positivas**, e a conta "
                f"escolhida é **{conta['nome']}**, uma conta corrente. Num extrato de conta "
                "corrente isso não acontece: ele tem os dois lados. Ou este arquivo é uma "
                "lista de recebimentos, ou é uma fatura de cartão que veio parar na conta "
                "errada. Preciso que você diga qual.",
                icon="✋",
            )
            GASTOS = "São gastos — o positivo aqui é despesa (fatura de cartão, lista de compras)"
            RECEBIMENTOS = "São recebimentos — o positivo aqui é receita mesmo"
            resposta = st.radio(
                "O que é este arquivo?", [GASTOS, RECEBIMENTOS],
                index=None, key=f"{chave_estado}:natureza_do_arquivo",
            )
            if resposta is None:
                st.info(
                    "Responda acima para liberar a importação. Se for fatura de cartão, o "
                    "melhor é cancelar e enviá-la na conta do próprio cartão: ali o sistema "
                    "garante sozinho que nada de cartão entre como renda.",
                    icon="⬆️",
                )
                return
            inverter = resposta == GASTOS
        else:
            inverter = st.checkbox(
                "O valor vem positivo mesmo quando é gasto (comum em fatura de cartão)",
                value=False,
                key=f"{chave_estado}:inverter",
                help="Marque se, na prévia abaixo, as compras aparecerem como ENTRADA.",
            )

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
        # A conta é sobre o arquivo inteiro, não sobre as oito linhas da prévia:
        # a fatura do cartão traz o "Pagamento recebido" logo nas primeiras
        # linhas, e bastava ele para a amostra deixar de ser toda de entrada e o
        # aviso não aparecer — justamente no arquivo em que ele mais importa.
        # Uma varredura da coluna de valor, sem reler o arquivo lançamento a
        # lançamento, que é o que a tela faz a cada mexida no mapeamento.
        proporcao = tabular.proporcao_positiva(df, mapa)
        entram = None if proporcao is None else ((1 - proporcao) if inverter else proporcao)
        if entram is not None and entram >= tabular.PROPORCAO_DE_GASTO:
            st.warning(
                f"**{entram:.0%} das linhas deste arquivo vão entrar como ENTRADA**, "
                "somando nas receitas do mês. Isso está certo numa planilha de "
                "recebimentos e errado numa fatura de cartão ou num extrato de gastos — "
                "nesses, marque a caixa acima.",
                icon="⚠️",
            )
            st.caption(
                "Se passar errado dá para voltar atrás: **Histórico → Desfazer uma "
                "importação** apaga tudo o que entrou por este arquivo."
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
            # o mapeamento manual passa pela mesma regra do leitor de fatura:
            # a compra conta no mes da compra, a parcela no ciclo
            if conta["tipo"] == "cartao" and competencia:
                lancamentos = ajustar_ano_fatura(lancamentos, competencia)
            _importar(engine, conta, lancamentos, arquivo.name, usuario,
                      "planilha" if e_planilha else "extrato", competencia, avisos,
                      pessoa_padrao=pessoa_arquivo)
        return

    # PDF de banco costuma vir com senha (CPF, data de nascimento, os quatro
    # primeiros do CNPJ). Sem o campo, o arquivo simplesmente não abria e o
    # erro dizia "não consegui ler o PDF" sem dizer o porquê
    senha = None
    if arquivo.name.lower().endswith(".pdf"):
        senha = st.text_input(
            "Senha do PDF (se tiver)", type="password",
            help="Muitos bancos protegem o extrato. Costuma ser o CPF, a data de "
                 "nascimento ou os primeiros dígitos do documento.",
        ) or None
        _diagnostico_pdf(engine, conteudo, senha, competencia, conta)

    if st.button("Processar arquivo", type="primary"):
        try:
            lancamentos = instituicoes.ler_arquivo(
                parser, conteudo, arquivo.name,
                competencia=competencia, tipo_conta=conta["tipo"], senha=senha,
            )
        except ErroDeLeitura as exc:
            st.error(f"Não consegui ler o arquivo: {exc}")
            st.caption(
                "Se for fatura em PDF com layout diferente do esperado, envie a versão "
                "CSV/Excel — ela passa pela conferência de colunas."
            )
            return
        texto = _texto_se_pdf(arquivo.name, conteudo, senha)
        if not _pdf_e_desta_conta(engine, conta, texto, bloquear=True):
            return
        avisos = _conferencia_do_extrato(parser, texto, lancamentos)
        _importar(
            engine, conta, lancamentos, arquivo.name, usuario, "extrato", competencia, avisos
        )


def _texto_se_pdf(nome: str, conteudo: bytes, senha) -> str | None:
    """O texto do PDF, uma vez só, para a identificação e a conferência."""
    if not nome.lower().endswith(".pdf"):
        return None
    try:
        return leitor_pdf.texto_do_pdf(conteudo, senha=senha)
    except Exception:
        return None


def _pdf_e_desta_conta(engine, conta, texto: str | None, *, bloquear: bool) -> bool:
    """O extrato é mesmo da conta escolhida no menu?

    Duas contas no mesmo banco, com o mesmo leitor e o mesmo layout, são duas
    opções iguais num menu — e escolher a errada não dava erro nenhum: o mês
    inteiro entrava na conta vizinha. O cabeçalho do PDF diz a agência e a
    conta; o cadastro diz qual é de qual. Aqui os dois se encontram.

    Devolve False só quando há certeza de que é a conta errada. Sem
    identificador no cadastro não há como conferir — e aí a tela pede que se
    cadastre, em vez de deixar passar calada.
    """
    if not texto:
        return True
    ident = extrato_itau.identificacao(texto) or extrato_bradesco.identificacao(texto)
    if not ident:
        return True
    bate = extrato_itau.conta_bate(ident, conta.get("identificador"))
    if bate:
        return True
    with engine.connect() as conn:
        dona = repo.conta_pelo_identificador(conn, ident)
    if bate is False:
        de_quem = f" Pelo cadastro, ele é da conta **{dona['nome']}**." if dona else ""
        st.error(
            f"**Este PDF não é da conta {conta['nome']}.** O extrato diz agência "
            f"**{ident['agencia']}**, conta **{ident['conta']}**; a conta escolhida está "
            f"cadastrada como **{conta['identificador']}**.{de_quem} Escolha a conta certa "
            "no menu acima.",
            icon="🚫",
        )
        return not bloquear
    # sem identificador no cadastro: não dá para conferir
    if dona and dona["id"] != conta["id"]:
        st.error(
            f"**Este PDF parece ser da conta {dona['nome']}**, não de {conta['nome']}: o "
            f"extrato diz agência {ident['agencia']}, e é {dona['nome']} que está "
            "cadastrada com ela. Escolha a conta certa no menu acima.",
            icon="🚫",
        )
        return not bloquear
    st.info(
        f"Este extrato é da agência **{ident['agencia']}**, conta **{ident['conta']}** "
        f"({ident['competencia']}). A conta **{conta['nome']}** ainda não tem agência no "
        "cadastro, então não dá para saber se é ela mesma.",
        icon="🏦",
    )
    if bloquear:
        return True
    # A separação por agência nasce do próprio arquivo, sem formulário: quem
    # tem duas contas no mesmo banco vê uma opção só no menu até dizer qual é
    # qual — e o jeito de dizer é aqui, com o PDF na mão. Os números vão para
    # o banco de dados, nunca para o código.
    c_sim, c_nao = st.columns(2)
    if c_sim.button(
        f"É esta: {conta['nome']} é a agência {ident['agencia']}",
        key=f"ident_sim:{conta['id']}:{ident['agencia']}", width="stretch",
    ):
        repo.identificar_conta(engine, conta["id"], f"{ident['agencia']}/{ident['conta']}")
        st.rerun()
    if c_nao.button(
        f"Não é esta: criar conta nova para a agência {ident['agencia']}",
        key=f"ident_nova:{conta['id']}:{ident['agencia']}", width="stretch",
    ):
        nome_novo = nome_para_agencia(conta, ident["agencia"])
        try:
            repo.salvar_conta(
                engine, nome=nome_novo, tipo=conta["tipo"], titular=conta["titular"],
                instituicao=conta["instituicao"], parser=conta["parser"],
                identificador=f"{ident['agencia']}/{ident['conta']}",
            )
        except Exception:
            st.error(f"Já existe uma conta chamada **{nome_novo}**. Escolha-a no menu acima.")
            return True
        st.success(f"Conta **{nome_novo}** criada. Escolha-a no menu **Conta / cartão** "
                   "acima e envie o arquivo de novo.")
        return True
    return True


def nome_para_agencia(conta: dict, agencia: str) -> str:
    """O nome da conta irmã: o mesmo banco e titular, com a agência no nome.

    "Itaú C/C" vira "Itaú C/C ag. 0660". O menu já acrescenta o titular.
    """
    base = conta["nome"].strip()
    return f"{base} ag. {agencia}"


def _conferencia_do_extrato(parser, texto, lancamentos) -> list[str]:
    """Compara o lido com o total que o próprio extrato imprime.

    O extrato do Itaú declara, no cabeçalho, quanto entrou e quanto saiu no
    mês, e o saldo antes e depois. Nenhuma outra conferência é tão boa: ela
    pega linha perdida, linha contada duas vezes e sinal trocado de uma vez só
    — e antes de gravar. Foi ela que acusou os R$ 2.240,00 de pacientes que o
    leitor deixava cair.
    """
    leitores = {"itau": extrato_itau, "bradesco": extrato_bradesco}
    if parser not in leitores or not lancamentos or not texto:
        return []
    try:
        conferencia = leitores[parser].conferir(texto, lancamentos)
    except Exception:                      # a conferência é um extra, nunca o obstáculo
        return []

    if conferencia.get("confere") is None:
        return []
    if conferencia["confere"]:
        saldo = " O saldo também fecha." if conferencia.get("saldo_fecha") else ""
        st.success(
            "Confere com o total impresso no extrato: entradas "
            f"{fmt_brl(conferencia['entradas'])}, saídas {fmt_brl(conferencia['saidas'])}."
            + saldo,
            icon="✅",
        )
        return []
    if "entradas_declaradas" not in conferencia:
        return ["o saldo do extrato não fecha com o que foi lido — alguma linha ficou de "
                "fora ou entrou duas vezes. Confira antes de classificar."]
    return [
        "o lido não bate com o total impresso no extrato — entradas "
        f"{fmt_brl(conferencia['entradas'])} contra "
        f"{fmt_brl(conferencia['entradas_declaradas'])}, saídas "
        f"{fmt_brl(conferencia['saidas'])} contra "
        f"{fmt_brl(conferencia['saidas_declaradas'])}. Confira antes de classificar."
    ]


def _diagnostico_pdf(engine, conteudo: bytes, senha, competencia, conta) -> None:
    """Mostra o que o leitor entendeu do PDF antes de gravar qualquer coisa.

    Um leitor que devolve zero lançamentos e mais nada não deixa ninguém
    avançar: não dá para saber se o PDF é imagem, se a senha está errada, se o
    layout é novo ou se o banco escreve a data de outro jeito. Aqui aparece o
    texto cru e o que passou perto e ficou de fora — é o que permite ajustar o
    leitor para um banco novo sem precisar do arquivo original em mãos.
    """
    try:
        diag = pdf.diagnosticar(
            conteudo, senha=senha, competencia=competencia,
            tudo_despesa=conta["tipo"] == "cartao",
        )
    except ErroDeLeitura as exc:
        st.error(f"**Não abri o PDF:** {exc}", icon="🔒")
        st.caption(
            "Se ele pede senha, preencha o campo acima. Se for digitalizado (só imagem), "
            "não há texto para ler — baixe a versão CSV/Excel no site do banco."
        )
        return

    # a identificação aparece antes do botão: é o momento de trocar a conta
    # no menu, não depois de gravar
    _pdf_e_desta_conta(engine, conta, _texto_se_pdf("x.pdf", conteudo, senha), bloquear=False)

    lidos = len(diag["lancamentos"])
    if lidos:
        st.success(f"Reconheci **{lidos} lançamentos** em {diag['linhas_no_pdf']} linhas de texto.")
    else:
        st.error(
            f"**Nenhum lançamento reconhecido** em {diag['linhas_no_pdf']} linhas. "
            "O layout deste banco ainda não é conhecido.",
            icon="🔍",
        )

    with st.expander("O que eu li deste PDF", expanded=not lidos):
        if diag["quase"]:
            st.markdown(
                f"**{len(diag['quase'])} linhas pareciam lançamento e ficaram de fora.** "
                "São elas que dizem o que falta ensinar ao leitor:"
            )
            st.code("\n".join(diag["quase"][:15]), language="text")
        if diag["ignoradas"]:
            st.markdown(
                f"**{len(diag['ignoradas'])} linhas foram descartadas de propósito** "
                "(total, saldo, limite, vencimento):"
            )
            st.code("\n".join(diag["ignoradas"][:8]), language="text")
        st.markdown("**Texto cru do PDF, como o leitor recebe:**")
        st.code("\n".join(diag["amostra"]), language="text")
        st.caption(
            "Para eu ensinar o leitor a ler este banco, copie daqui umas 10 linhas de "
            "lançamento e o cabeçalho. **Troque os valores** antes de mandar — o "
            "repositório é público."
        )


def _importar(engine, conta, lancamentos, nome_arquivo, usuario, origem, competencia,
              avisos, pessoa_padrao=None):
    if not lancamentos:
        st.error(
            "Não encontrei nenhum lançamento no arquivo. Confira se escolheu a conta certa "
            "e se as colunas de data e valor foram mapeadas."
        )
        return

    if conta["tipo"] == "corrente":
        competencia = competencia_predominante(lancamentos)

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

    # o que a subida faria no proximo reboot, feito agora: a fatura que
    # acabou de entrar pode ter compras datadas num mes da planilha, e pode
    # ser a fatura que um debito ja gravado no banco estava pagando
    if conta["tipo"] == "cartao":
        repo.aplicar_meses_da_planilha(engine)
        repo.marcar_pagamentos_de_cartao(engine)

    st.success(f"Arquivo processado: {resumo['lidos']} lançamentos lidos.")

    # A última rede, depois de tudo gravado: quantas destas linhas somam na
    # renda do mês? Numa fatura de cartão a resposta certa é "quase nenhuma".
    # As perguntas antes de importar dependem de alguém responder direito; esta
    # olha o que de fato entrou, e vem com o desfazer do lado — é o que faltava
    # nas duas vezes em que a fatura passou.
    if resumo.get("sinal_corrigido"):
        st.info(
            "**Este arquivo veio com a compra positiva, e foi virado na gravação.** "
            "Numa conta de cartão, compra é despesa — o sistema não pergunta mais, "
            "corrige. Só o que veio negativo no arquivo (estorno, pagamento da fatura) "
            "entrou como crédito.",
            icon="💳",
        )
    entradas = sum(1 for lan in lancamentos if lan.valor_centavos > 0)
    if (not resumo.get("sinal_corrigido")
            and entradas and entradas / len(lancamentos) >= tabular.PROPORCAO_DE_GASTO):
        soma = sum(lan.valor_centavos for lan in lancamentos if lan.valor_centavos > 0)
        st.error(
            f"**{entradas} dos {len(lancamentos)} lançamentos entraram como ENTRADA**, "
            f"somando {_reais(soma)} nas receitas de **{conta['nome']}**. Se este arquivo "
            "era uma fatura de cartão ou uma lista de gastos, está invertido: desfaça agora "
            "e envie de novo com o sinal certo.",
            icon="🚨",
        )
        if st.button("Desfazer esta importação", type="primary", key="desfazer_recem"):
            apagadas, devolvidas, retidas = repo.apagar_upload(engine, resumo["upload_id"])
            st.success(f"{apagadas} lançamento(s) removido(s). Pode enviar de novo.")
            st.stop()

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
    if resumo.get("previsoes_realizadas"):
        st.info(
            f"**{resumo['previsoes_realizadas']} receita(s) que você lançou à mão chegaram de "
            "verdade neste extrato.** A previsão saiu de cena e vale o valor do extrato — sem "
            "isso, a renda do mês apareceria dobrada. Nada foi apagado: a previsão ficou "
            "inativa e continua visível na tela **Receitas**.",
            icon="💰",
        )
    # o que a regra automática não casou, mas continua parecido demais para
    # passar sem alguém olhar
    sobrando = resumo.get("previsoes_a_conferir") or []
    if sobrando:
        linhas = "\n".join(
            f"- **{item['competencia']}** · {item['pessoa']} · "
            f"{sem_marcacao(item['descricao'])} — {_reais(item['valor_centavos'])} "
            f"— parecida com *{sem_marcacao(item['parecida_com'][:34])}* "
            f"({_reais(item['valor_parecido'])}) deste arquivo"
            for item in sobrando[:10]
        )
        st.warning(
            "**Confira se alguma destas receitas previstas (à mão ou na planilha) é o mesmo dinheiro que "
            "acabou de entrar pelo extrato.** Elas continuam contando nos relatórios, e "
            "duas linhas para o mesmo salário dobram a renda do mês. O que cai no mesmo "
            "mês com valor parecido o sistema já casou sozinho; aqui sobrou o que não "
            "casou com nada — caiu num mês sem previsão correspondente, ou veio bem "
            "acima do previsto.\n\n" + linhas
            + "\n\nSe alguma já veio no extrato, apague-a na tela **Receitas**.",
            icon="💰",
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

    contas = [c for c in dados.contas(engine, dados.versao(), so_ativas=not incluir_inativas)
              if c['nome'] != repo.CONTA_PLANILHA]
    cobertura = dados.cobertura_de_uploads(engine, dados.versao(), tuple(competencias))
    mapa, mapa_planilha = cobertura["contas"], cobertura["planilha"]

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
        celula = mapa_planilha.get(competencia)
        classe = " class='futuro'" if competencia == atual else ""
        if celula:
            celulas_planilha.append(
                f"<td{classe}><span class='ok'>✓<small>{celula['total']}</small></span></td>"
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
            celula = mapa.get((conta["id"], competencia))
            if competencia == atual:
                # mês em curso: o extrato ainda nem fechou. O que já existe aqui
                # é parcial por definição — um "✓ carregado" no mês de hoje
                # dizia que setembro estava pronto com o mês pela metade
                celulas.append(
                    f"<td class='futuro'><span class='dup'>parcial<small>{celula['ativos']}"
                    "</small></span></td>" if celula else "<td class='futuro'>em curso</td>"
                )
            elif not celula:
                faltando += 1
                celulas.append("<td><span class='falta'>·</span></td>")
            elif celula["ativos"] == 0:
                celulas.append("<td><span class='dup'>!<small>duplicado</small></span></td>")
            else:
                celulas.append(
                    f"<td><span class='ok'>✓<small>{celula['ativos']}</small></span></td>"
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
    # o que exatamente há numa célula: em vez de discutir com o mapa, olha-se
    # as linhas e o arquivo de onde vieram
    with st.expander("Ver o que há numa conta num mês"):
        e1, e2 = st.columns(2)
        conta_alvo = e1.selectbox("Conta", contas, format_func=lambda c: c["nome"],
                                  key="mapa_conta")
        mes_alvo = e2.selectbox("Mês", list(reversed(competencias)), key="mapa_mes")
        itens = dados.lancamentos_da_conta_no_mes(
            engine, dados.versao(), conta_alvo["id"], mes_alvo
        )
        if not itens:
            st.caption("Nada nesta conta neste mês.")
        else:
            st.caption(
                f"{len(itens)} lançamento(s), {sum(1 for i in itens if i['ativo'])} valendo. "
                "A coluna **Arquivo** diz de onde cada um veio — para desfazer um upload "
                "errado, use **Histórico**."
            )
            st.dataframe(
                pd.DataFrame([
                    {"Data": f"{i['data']:%d/%m/%Y}", "Descrição": i["descricao"],
                     "Valor": fmt_brl(i["valor_centavos"]),
                     "Vale": "sim" if i["ativo"] else "não",
                     "Arquivo": i["arquivo"] or i["origem"]}
                    for i in itens
                ]),
                width="stretch", hide_index=True,
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


def _aba_duplicidades(engine, usuario: dict, fila: list[dict]) -> None:
    # a fila chega pronta: a `render` já precisa dela para escrever a contagem
    # no título da aba, e buscá-la de novo aqui era a mesma consulta de três
    # tabelas indo ao banco duas vezes no mesmo rerun

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
    critica = dados.critica(engine, dados.versao())

    st.caption(
        "A planilha da Rô é a carga inicial do histórico. Quando o extrato do mesmo período "
        "entra, o sistema confronta os dois — só compara meses em que as duas origens existem."
    )
    _criticas_encerradas(engine, critica.get("encerradas") or [])
    if critica["sem_conferencia"]:
        if not critica.get("encerradas"):
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
        exatas = sum(1 for item in critica["divergencias"] if item["diferenca"] == 0)
        if exatas:
            st.caption(
                f"{exatas} delas têm **o mesmo valor no centavo** — é o mesmo gasto escrito "
                "de outro jeito (o condomínio anotado no dia 5 e debitado no dia 10). Nenhuma "
                "pede decisão; as de valor diferente continuam abaixo, uma a uma."
            )
            if st.button(f"Vale o extrato nas {exatas} de valor igual", type="primary",
                         key="critica_exatas"):
                total = reconcile.aposentar_pares_exatos(engine, usuario["nome"])
                st.success(f"{total} linha(s) da planilha conferidas com o extrato.")
                st.rerun()
        for item in critica["divergencias"]:
            planilha, extrato = item["planilha"], item["extrato"]
            partes = item.get("extratos") or [extrato]
            if len(partes) > 1:
                lado_extrato = " + ".join(
                    f"{p['data']:%d/%m} {fmt_brl(p['valor_centavos'])}" for p in partes
                ) + f" (soma de {len(partes)} lançamentos do extrato)"
            else:
                lado_extrato = (f"{extrato['data']:%d/%m} · {fmt_brl(extrato['valor_centavos'])} "
                                f"(diferença de {fmt_brl(abs(item['diferenca']))})")
            with st.container(border=True):
                c1, c2 = st.columns([3, 1.1])
                c1.markdown(
                    f"**{planilha['descricao']}**<br>"
                    f"<span class='nota'>Planilha: {planilha['data']:%d/%m} · "
                    f"{fmt_brl(planilha['valor_centavos'])} &nbsp;|&nbsp; "
                    f"Extrato: {lado_extrato}</span>",
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
        st.caption(
            "Só despesas de **meses fechados** entram aqui. O mês em curso e as receitas "
            "previstas ficam de fora de propósito: o extrato parcial não diz que o resto "
            "do mês não vai acontecer."
        )
        if st.button(f"Descartar os {len(critica['so_planilha'])} que só estão na planilha"):
            total = reconcile.descartar_da_planilha(
                engine, [i["id"] for i in critica["so_planilha"]], usuario["nome"]
            )
            st.success(f"{total} lançamento(s) descartado(s).")
            st.rerun()

    _encerrar_critica(engine, usuario, critica)

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


def _encerrar_critica(engine, usuario: dict, critica: dict) -> None:
    """"Ok, entendi, não vou mudar nada": o mês sai da frente, tudo como está.

    Sem isto a crítica era uma lista que nunca acabava: o que só está na
    planilha (gasto em dinheiro, ou de conta fora do sistema) voltava a cada
    visita, e "Manter as duas" era esquecido na consulta seguinte.
    """
    meses = sorted({i["competencia"] for i in critica["so_planilha"]}
                   | {i["planilha"]["competencia"] for i in critica["divergencias"]}
                   | {i["competencia"] for i in critica["faltantes"]})
    if not meses:
        return
    st.markdown("#### Ok, entendi — não vou mudar nada")
    st.caption(
        "Deixa tudo como está: o que só está na planilha continua valendo, e o mês "
        "sai desta tela. Dá para reabrir depois, no alto da aba."
    )
    colunas = st.columns(min(len(meses), 3))
    for pos, competencia in enumerate(meses):
        rotulo = f"{graficos.rotulo_mes(competencia).lower()}/{competencia[2:4]}"
        if colunas[pos % 3].button(f"Fica como está em {rotulo}", key=f"crit_ok_{competencia}",
                                   width="stretch"):
            total = reconcile.encerrar_critica(engine, competencia, usuario["nome"])
            st.session_state["msg_critica"] = (
                f"{rotulo}: crítica encerrada, {total} linha(s) da planilha mantidas."
            )
            st.rerun()


def _criticas_encerradas(engine, encerradas: list[str]) -> None:
    if recado := st.session_state.pop("msg_critica", None):
        st.success(recado)
    if not encerradas:
        return
    rotulos = ", ".join(
        f"{graficos.rotulo_mes(c).lower()}/{c[2:4]}" for c in encerradas
    )
    # discreto: o mes encerrado nao volta a pedir atencao; reabrir fica
    # guardado, para quem for procurar
    with st.expander(f"Crítica encerrada em {rotulos} — tudo como está"):
        escolha = st.selectbox("Reabrir um mês", ["—", *encerradas], key="crit_reabrir")
        if escolha != "—" and st.button("Reabrir", key="crit_reabrir_btn"):
            reconcile.reabrir_critica(engine, escolha)
            st.rerun()


def _aba_manual(engine, usuario: dict) -> None:
    """Despesa e receita que não passam por extrato nenhum.

    Os euros comprados em espécie saem da conta como um saque e viram uma
    viagem — o extrato não sabe disso, só quem gastou sabe.
    """
    st.caption(
        "Nem todo dinheiro passa por extrato. Aqui entra o que só você sabe: gasto em "
        "espécie, a sua parte de uma conta dividida, o recebimento que chega picado."
    )
    ano = date.today().year
    manual.formulario(engine, usuario, ano, "despesa")
    manual.formulario(engine, usuario, ano, "receita")
    st.caption(
        "O lançamento vai para uma conta própria, **Lançamento manual**, separada dos "
        "bancos — assim a crítica planilha × extratos não cobra um comprovante que não "
        "existe."
    )


def _aba_contas(engine) -> None:
    contas = dados.contas(engine, dados.versao(), so_ativas=False)

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
            # a identificação fica num expander de propósito: é preenchida uma
            # vez por conta, e a lista de contas não precisa ficar carregada
            # de campos para sempre
            atual = conta.get("identificador") or ""
            with st.expander(f"Identificação: {atual or 'não cadastrada'}"):
                novo = st.text_input(
                    "Agência (e conta, se quiser)", value=atual,
                    key=f"ident{conta['id']}", placeholder="Ex.: 1234 ou 1234/56789-0",
                    help="Como o banco imprime no extrato. Serve para barrar o PDF de uma "
                         "conta enviado na outra.",
                )
                if st.button("Salvar identificação", key=f"salvar_ident{conta['id']}"):
                    repo.identificar_conta(engine, conta["id"], novo)
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
            identificador = st.text_input(
                "Agência (e conta, se quiser) — opcional",
                placeholder="Ex.: 1234 ou 1234/56789-0",
                help="Como o banco imprime no extrato. Com isso o sistema confere se o PDF "
                     "enviado é mesmo desta conta e barra o arquivo trocado — importa quando "
                     "há duas contas no mesmo banco.",
            )
            if st.form_submit_button("Incluir", type="primary"):
                if not nome.strip() or not instituicao.strip():
                    st.error("Preencha nome e instituição.")
                else:
                    repo.salvar_conta(
                        engine, nome=nome, tipo=tipo, titular=titular,
                        instituicao=instituicao, parser=parser, identificador=identificador,
                    )
                    st.success(f"Conta {nome} incluída.")
                    st.rerun()


def _aba_historico(engine) -> None:
    historico = dados.uploads(engine, dados.versao())
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
        c_apaga, c_vira = st.columns(2)
        # o reparo para a fatura que ja esta no banco com o sinal trocado: nao
        # perde classificacao, nao pede reimportacao, e e idempotente — clicar
        # de novo nao desfaz (a versao que desfazia foi acionada duas vezes e
        # devolveu a fatura ao erro)
        if c_vira.button("Corrigir o sinal desta importação", width="stretch",
                         help="Só age se for fatura de cartão gravada com a compra positiva. "
                              "Clicar de novo não desfaz nada."):
            corrigidos = repo.endireitar_upload(engine, escolha["id"])
            if corrigidos:
                st.success(f"{corrigidos} lançamento(s) corrigidos: compra agora é despesa.")
                st.rerun()
            else:
                st.info("Nada a corrigir: este arquivo já está com o sinal certo, ou não é "
                        "fatura de cartão.")
        if escolha.get("competencia") and escolha["origem"] == "extrato":
            # a fatura do XP paga em setembro e inteira de agosto: enviada como
            # setembro, as parcelas dela caem no mes errado. Aqui se corrige
            # sem reimportar
            m1, m2 = st.columns([2, 1])
            novo_mes = m1.selectbox(
                "Mês da fatura deste arquivo", _competencias_sugeridas(),
                index=(_competencias_sugeridas().index(escolha["competencia"])
                       if escolha["competencia"] in _competencias_sugeridas() else MESES_A_FRENTE),
                key=f"mes_fatura_{escolha['id']}",
                help="Para cartão: o mês em que a fatura fechou (XP: o mês das compras; "
                     "Nubank: o mês do vencimento). Trocar aqui recoloca as parcelas no "
                     "mês certo.",
            )
            if m2.button("Corrigir o mês da fatura", width="stretch",
                         key=f"btn_mes_fatura_{escolha['id']}"):
                movidas = repo.mudar_mes_da_fatura(engine, escolha["id"], novo_mes)
                st.success(f"Mês da fatura: {novo_mes}. {movidas} lançamento(s) mudaram de mês.")
                st.rerun()
        if c_apaga.button("Desfazer importação", type="secondary", width="stretch"):
            total, devolvidas, retidas = repo.apagar_upload(engine, escolha["id"])
            recado = f"{total} lançamento(s) removido(s)."
            if devolvidas:
                recado += (
                    f" {devolvidas} lançamento(s) que este arquivo tinha substituído "
                    "voltaram a valer."
                )
            st.success(recado)
            if retidas:
                st.warning(
                    f"{retidas} previsão(ões) que este arquivo tinha aposentado **continuam "
                    "desligadas**: o dinheiro delas já entrou por um extrato depois. Religar "
                    "aqui faria o mês contar duas vezes. Se você discordar, o botão **Voltar "
                    "a valer** está em *Lançar à mão*.",
                    icon="🔁",
                )
            st.rerun()


def render(engine, usuario: dict) -> None:
    fila_dup = dados.duplicidades(engine, dados.versao())
    pendentes_dup = len(fila_dup)

    abas = st.tabs([
        "📤 Enviar arquivo",
        "🗓️ O que falta carregar",
        f"🔁 Duplicidades ({pendentes_dup})" if pendentes_dup else "🔁 Duplicidades",
        "🔍 Crítica planilha × extratos",
        "✍️ Lançar à mão",
        "🏦 Contas e cartões",
        "🗂️ Histórico",
    ])
    with abas[0]:
        _aba_enviar(engine, usuario)
    with abas[1]:
        _aba_mapa(engine)
    with abas[2]:
        _aba_duplicidades(engine, usuario, fila_dup)
    with abas[3]:
        _aba_critica(engine, usuario)
    with abas[4]:
        _aba_manual(engine, usuario)
    with abas[5]:
        _aba_contas(engine)
    with abas[6]:
        _aba_historico(engine)
