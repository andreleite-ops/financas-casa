"""Pipeline de importacao e consultas usadas pelas telas."""

from __future__ import annotations

import calendar
import re
from datetime import date

import sqlalchemy as sa

from . import ai, analytics, cartoes, classify, db, dedup
from parsers.base import endireitar, fatura_invertida
from .texto import normalizar, pessoa_na_descricao

PESSOA_PADRAO = "Casal"

# A carga inicial nao pertence a nenhum banco: a planilha da casa anota gastos
# de todas as contas e cartoes juntos, sem dizer de qual saiu cada um.
# Registra-la dentro de uma conta real faria aquela conta parecer dona de todo
# o historico. Por isso ela tem uma conta propria, que nao aparece na lista de
# extratos.
CONTA_PLANILHA = "Planilha (carga inicial)"


def conta_da_planilha(engine) -> int:
    """Devolve a conta reservada da carga inicial, criando-a se preciso."""
    with engine.begin() as conn:
        existente = conn.execute(
            sa.select(db.contas.c.id).where(db.contas.c.nome == CONTA_PLANILHA)
        ).scalar()
        if existente:
            return existente
        return conn.execute(
            sa.insert(db.contas).values(
                nome=CONTA_PLANILHA, tipo="corrente", titular="Casal",
                instituicao="—", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


# --------------------------------------------------------------------------
# leituras de apoio
# --------------------------------------------------------------------------
def listar_contas(conn, so_ativas: bool = False) -> list[dict]:
    consulta = sa.select(db.contas).order_by(db.contas.c.ativa.desc(), db.contas.c.nome)
    if so_ativas:
        consulta = consulta.where(db.contas.c.ativa == sa.true())
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def conta_por_id(conn, conta_id: int) -> dict | None:
    linha = conn.execute(sa.select(db.contas).where(db.contas.c.id == conta_id)).fetchone()
    return dict(linha._mapping) if linha else None


def plano_de_contas(conn, natureza: str | None = None) -> list[dict]:
    consulta = (
        sa.select(
            db.categorias.c.id,
            db.categorias.c.nome,
            db.categorias.c.natureza,
            db.categorias.c.ordem,
            db.categorias.c.ativa,
        )
        .order_by(db.categorias.c.natureza.desc(), db.categorias.c.ordem)
    )
    if natureza:
        consulta = consulta.where(db.categorias.c.natureza == natureza)
    categorias = conn.execute(consulta).fetchall()

    # todas as subcategorias numa consulta só. Uma por categoria custava
    # dezoito idas ao banco, e o banco está em São Paulo enquanto o app roda nos
    # Estados Unidos: cada ida são uns 150ms. Isso acontecia a cada toque de
    # campo na tela de classificação, e era ela a lentidão sentida ali.
    por_categoria: dict[int, list[dict]] = {}
    if categorias:
        subs = conn.execute(
            sa.select(
                db.subcategorias.c.id,
                db.subcategorias.c.nome,
                db.subcategorias.c.ativa,
                db.subcategorias.c.categoria_id,
            )
            .where(db.subcategorias.c.categoria_id.in_([c.id for c in categorias]))
            .order_by(db.subcategorias.c.ordem)
        )
        for sub in subs:
            linha = dict(sub._mapping)
            por_categoria.setdefault(linha.pop("categoria_id"), []).append(linha)

    return [
        {**dict(cat._mapping), "subcategorias": por_categoria.get(cat.id, [])}
        for cat in categorias
    ]


def plano_para_ia(conn) -> dict[str, list[str]]:
    return {
        cat["nome"]: [s["nome"] for s in cat["subcategorias"]]
        for cat in plano_de_contas(conn)
        if cat["ativa"]
    }


def _indice_categorias(conn) -> tuple[dict[str, int], dict[tuple[int, str], int]]:
    cats = {
        linha.nome.casefold(): linha.id
        for linha in conn.execute(sa.select(db.categorias.c.id, db.categorias.c.nome))
    }
    subs = {
        (linha.categoria_id, linha.nome.casefold()): linha.id
        for linha in conn.execute(
            sa.select(db.subcategorias.c.id, db.subcategorias.c.categoria_id, db.subcategorias.c.nome)
        )
    }
    return cats, subs


# Como cada pessoa pode aparecer escrita na coluna de portador da fatura.
# Comparação é por palavra inteira, nunca por começo: "RO" é a Rô, mas
# "ROBERTO" e "RODRIGO" são terceiros, e "ANDREA" não é o André. Prefixo
# parecia resolver e na verdade atribuía o gasto de estranhos à casa.
APELIDOS_PESSOA = {
    "andre": "André", "andré": "André", "a": "André",
    "ro": "Rô", "rô": "Rô", "r": "Rô",
    "casal": "Casal", "nos": "Casal", "nós": "Casal",
    "ambos": "Casal", "c": "Casal",
}


def _apelidos_configurados() -> dict[str, str]:
    """Nomes completos como o cartão os imprime, vindos do segredo do app.

    O repositório é público: nome completo de ninguém entra no código. A
    fatura, porém, imprime o portador por extenso, e sem essa tradução o gasto
    fica com a pessoa errada. Por isso o de-para mora no segredo
    APELIDOS_PESSOA ("nome como sai na fatura=Rô;outro nome=André"), que só
    existe na instalação da casa.
    """
    bruto = db._segredo("APELIDOS_PESSOA") or ""
    mapa: dict[str, str] = {}
    for par in bruto.split(";"):
        if "=" not in par:
            continue
        chave, _, pessoa = par.partition("=")
        pessoa = pessoa.strip()
        if pessoa in db.PESSOAS:
            mapa[chave.strip().casefold()] = pessoa
    return mapa


def _pessoa_valida(valor: str | None, padrao: str) -> str:
    if not valor:
        return padrao
    limpo = str(valor).strip().casefold()
    configurados = _apelidos_configurados()
    for pessoa in db.PESSOAS:
        if limpo == pessoa.casefold():
            return pessoa
    # o nome inteiro primeiro (o segredo pode traduzir "fulano de tal leite"),
    # depois só a primeira palavra ("ANDRE TITULAR" -> "andre")
    primeira = limpo.split()[0] if limpo.split() else limpo
    for candidato in (limpo, primeira):
        if candidato in configurados:
            return configurados[candidato]
        if candidato in APELIDOS_PESSOA:
            return APELIDOS_PESSOA[candidato]
    return padrao


# --------------------------------------------------------------------------
# importacao
# --------------------------------------------------------------------------
LOTE_INSERCAO = 500


def _mes_vizinho(competencia: str, passo: int) -> str:
    ano, mes = int(competencia[:4]), int(competencia[5:7]) + passo
    if mes == 0:
        ano, mes = ano - 1, 12
    elif mes == 13:
        ano, mes = ano + 1, 1
    return f"{ano:04d}-{mes:02d}"


# Quanto dois valores podem divergir e ainda merecer uma conferida de olho.
# Mais larga que a folga do pareamento automatico de proposito: aqui ninguem
# decide nada, so se pergunta.
FOLGA_PARA_PERGUNTAR = 0.5


def _previsoes_por_conferir(conn, sem_par: list[dict]) -> list[dict]:
    """Receita prevista a mao que este arquivo pode ter trazido de novo.

    So olha as entradas do arquivo que **nao** casaram com previsao nenhuma. Se
    a entrada ja realizou a previsao do proprio mes, nao ha o que perguntar — e
    perguntar assim mesmo seria o pior tipo de aviso: o que aparece todo mes,
    vira paisagem e deixa de ser lido justo quando importa.

    Sobra o que a regra automatica nao alcanca, que e onde a duplicacao passaria
    calada: o dinheiro caiu num mes sem previsao correspondente (o salario de
    dezembro creditado em 2 de janeiro) ou veio tao acima do previsto que saiu
    da folga. Nesses casos a janela abre para os meses vizinhos e a folga dobra
    — porque aqui ninguem decide nada, so se pergunta.
    """
    if not sem_par:
        return []

    meses = {linha["competencia"] for linha in sem_par}
    janela = sorted(
        meses
        | {_mes_vizinho(m, -1) for m in meses}
        | {_mes_vizinho(m, +1) for m in meses}
    )
    consulta = (
        sa.select(
            db.transacoes.c.competencia,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.origem,
        )
        .where(
            db.transacoes.c.origem.in_(dedup.ORIGENS_DE_PREVISAO),
            db.transacoes.c.valor_centavos > 0,
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.competencia.in_(janela),
        )
        .order_by(db.transacoes.c.competencia, db.transacoes.c.id)
    )
    saida = []
    for previsao in conn.execute(consulta):
        # A janela de meses vizinhos e para a previsao digitada a mao, que tem
        # mes aproximado. A linha da planilha tem data exata — e, nos meses ja
        # fechados, e historia, nao previsao: o salario de julho na planilha
        # nao pode ser posto em duvida por um credito de agosto. Sem este
        # limite, o extrato de agosto listava julho e setembro inteiros como
        # "confira se e o mesmo dinheiro", com o convite para apagar.
        if previsao.origem == "planilha" and previsao.competencia not in meses:
            continue
        candidatas = [
            linha for linha in sem_par
            if previsao.origem != "planilha" or linha["competencia"] == previsao.competencia
        ]
        parecidas = [
            linha for linha in candidatas
            if abs(linha["valor_centavos"] - previsao.valor_centavos)
            <= FOLGA_PARA_PERGUNTAR * max(linha["valor_centavos"], previsao.valor_centavos)
        ]
        if parecidas:
            saida.append({
                **dict(previsao._mapping),
                "parecida_com": parecidas[0]["descricao"],
                "valor_parecido": parecidas[0]["valor_centavos"],
            })
            continue

        # A Ro recebe dos pacientes picado, dezenas de vezes no mes, e lanca o
        # total a mao numa linha so. Quando o extrato dela entra, nenhum
        # pagamento isolado se parece com o total — R$ 300 contra R$ 4.800 —,
        # entao a comparacao um a um passa batido e a renda dela dobra calada.
        # Somados e que eles sao o mesmo dinheiro.
        do_mes = [
            linha for linha in sem_par
            if linha["competencia"] == previsao.competencia
        ]
        soma = sum(linha["valor_centavos"] for linha in do_mes)
        if len(do_mes) > 1 and abs(soma - previsao.valor_centavos) <= (
            FOLGA_PARA_PERGUNTAR * max(soma, previsao.valor_centavos)
        ):
            saida.append({
                **dict(previsao._mapping),
                "parecida_com": f"{len(do_mes)} lançamentos somados",
                "valor_parecido": soma,
            })
    return saida


def _inserir_transacoes(conn, linhas: list[dict], upload_id: int) -> list[int]:
    """Insere tudo em poucas idas ao banco e devolve os ids na mesma ordem.

    Nao usa RETURNING ordenado de proposito: o SQLAlchemy so garante a ordem em
    parte dos bancos e, onde nao garante, volta a inserir linha a linha — que e
    exatamente o custo que se quer evitar. Como todas as linhas do lote
    compartilham o mesmo upload_id e a chave e sequencial, reler os ids em
    ordem crescente devolve a mesma ordem da insercao, em qualquer banco.
    """
    if not linhas:
        return []
    for inicio in range(0, len(linhas), LOTE_INSERCAO):
        conn.execute(sa.insert(db.transacoes), linhas[inicio : inicio + LOTE_INSERCAO])

    ids = [
        linha.id
        for linha in conn.execute(
            sa.select(db.transacoes.c.id)
            .where(db.transacoes.c.upload_id == upload_id)
            .order_by(db.transacoes.c.id)
        )
    ]
    if len(ids) != len(linhas):
        raise RuntimeError(
            f"esperava {len(linhas)} lançamentos gravados, encontrei {len(ids)}"
        )
    return ids


def importar(
    engine,
    *,
    conta_id: int,
    lancamentos: list,
    arquivo: str,
    usuario: str,
    origem: str = "extrato",
    competencia: str | None = None,
    usar_ia: bool = True,
    pessoa_padrao: str | None = None,
) -> dict:
    """Grava um lote de lancamentos aplicando dedup e classificacao.

    Devolve o resumo que a tela de upload mostra.
    """
    resumo = {
        "lidos": len(lancamentos),
        "importados": 0,
        "auto": 0,
        "pendentes": 0,
        "duplicados_exatos": 0,
        "duplicados_provaveis": 0,
        "conferidos_planilha": 0,
        "previsoes_realizadas": 0,
        "previsoes_a_conferir": [],
        "sinal_corrigido": False,
        "upload_id": None,
    }
    if not lancamentos:
        return resumo

    with engine.begin() as conn:
        conta = conta_por_id(conn, conta_id)
        if conta is None:
            raise ValueError(f"conta {conta_id} não existe")
        # quem escolhe manda; senão vale o titular da conta. A planilha de
        # carga inicial fica numa conta do casal, mas pode ser de uma pessoa só
        pessoa_padrao = _pessoa_valida(pessoa_padrao, conta["titular"])

        # A trava que fecha o assunto, no unico lugar por onde tudo passa.
        # Num cartao, compra e negativa. Se o lote chegou quase todo positivo,
        # o arquivo veio com o sinal trocado — por qualquer caminho que seja:
        # leitor do banco, mapeamento manual, coluna "Tipo" com "a vista"
        # dentro, caixa desmarcada. Aqui ele e virado e a tela fica sabendo.
        # Antes disto a mesma fatura passou tres vezes, cada uma por um
        # caminho que a protecao anterior nao olhava.
        if conta["tipo"] == "cartao" and fatura_invertida(lancamentos):
            lancamentos = endireitar(lancamentos)
            resumo["sinal_corrigido"] = True

        upload_id = conn.execute(
            sa.insert(db.uploads).values(
                arquivo=arquivo,
                conta_id=conta_id,
                competencia=competencia,
                origem=origem,
                enviado_por=usuario,
                lidos=len(lancamentos),
            )
        ).inserted_primary_key[0]
        resumo["upload_id"] = upload_id

        regras = classify.carregar_regras(conn)
        naturezas = classify._natureza_por_categoria(conn)
        # o que o detector de pagamento de cartao precisa: os cartoes
        # cadastrados e o total de cada fatura ja importada
        emissores = cartoes.emissores(conn) if conta["tipo"] == "corrente" else []
        totais_fatura = cartoes.totais_de_fatura(conn) if emissores else {}
        recebidos = cartoes.pagamentos_recebidos(conn) if emissores else []
        transferencia_id = _id_da_categoria(conn, analytics.CATEGORIA_TRANSFERENCIA)
        pagamento_fatura_id = _id_da_subcategoria(conn, transferencia_id, "Pagamento de Fatura")
        donos_de_categoria = classify.donos_por_categoria(conn)
        bidirecionais = classify.categorias_bidirecionais(conn)
        traducoes = listar_de_para(conn)
        cats_idx, subs_idx = _indice_categorias(conn)

        # todo o histórico que pode conflitar vem numa consulta só; as decisões
        # saem da memória. Ir ao banco por lançamento custaria duas ou três
        # viagens de rede por linha — inviável num arquivo de milhares.
        indice = dedup.carregar_indice(conn, conta_id, [lan.data for lan in lancamentos])

        linhas: list[dict] = []          # o que será inserido, na ordem
        duplicatas: list[tuple[int, dedup.Decisao]] = []   # (posição na lista, decisão)
        substituir: list[int] = []       # linhas da planilha que o extrato aposenta
        entradas_sem_par: list[dict] = []  # receitas que nao casaram com previsao
        pendentes_pos: list[int] = []    # posições que ficaram sem categoria
        proximo_temp = -1

        for lan in lancamentos:
            descricao_norm = normalizar(lan.descricao)
            # o mes em que este lancamento vai contar, decidido antes de
            # qualquer pareamento: e por ele que a previsao casa, porque e por
            # ele que o relatorio soma
            competencia_da_linha = (
                lan.competencia or (competencia or lan.data.strftime("%Y-%m"))
            )
            decisao = indice.avaliar(
                conta_id=conta_id,
                dia=lan.data,
                competencia=competencia_da_linha,
                valor_centavos=lan.valor_centavos,
                descricao=lan.descricao,
                descricao_norm=descricao_norm,
                origem=lan.origem or origem,
                upload_id=upload_id,
                # o dono ainda nao foi decidido aqui; o que a origem declara ja
                # basta para desempatar entre duas receitas previstas no mes
                pessoa=lan.pessoa_hint or pessoa_padrao,
                # Num cartao nao existe receita, entao nada que venha dele pode
                # "realizar" uma receita prevista a mao. Sem esta porta, uma
                # fatura lida com o sinal trocado aposentava a previsao do mes:
                # a compra de R$ 19.000 casava por mes e por ordem de grandeza
                # com o pro-labore previsto e o desligava — e o salario de
                # verdade, chegando depois, ja nao encontrava com quem parear.
                pode_realizar_previsao=conta["tipo"] != "cartao",
            )
            if decisao.existente_id:
                indice.marcar_usado(decisao.existente_id)

            # Num cartão de crédito não existe receita: o que entra é compra, e
            # o crédito que aparece é estorno ou o pagamento da própria fatura.
            # A natureza é decidida AQUI, antes de qualquer camada classificar,
            # porque é ela que as três camadas usam como guarda. Decidida depois,
            # a guarda olhava o sinal — e a fatura que entrou positiva na
            # primeira vez teve compras classificadas como renda, viraram
            # memória, e a memória repetia a cada fatura seguinte. Foram os 14
            # lançamentos do cartão em "Outras Receitas" que a sentinela pegou.
            natureza_declarada = lan.natureza_hint
            if conta["tipo"] == "cartao" and not natureza_declarada:
                natureza_declarada = "despesa"

            # classificacao: dica da planilha primeiro, senao regras
            categoria_id = subcategoria_id = None
            status, confianca = "pendente", None
            if lan.categoria_hint:
                rotulo = str(lan.categoria_hint).strip()
                categoria_id = cats_idx.get(rotulo.casefold())
                # o rótulo da Rô não existe no plano de contas; o de-para é a
                # tradução que ele já recebeu uma vez, e que vale daqui pra frente
                if categoria_id is None and rotulo in traducoes:
                    traducao = traducoes[rotulo]
                    categoria_id = traducao["categoria_id"]
                    subcategoria_id = traducao["subcategoria_id"]
                # a mesma guarda de natureza que vale para as regras: dinheiro
                # que entrou não pode cair numa categoria de despesa só porque
                # a origem rotulou assim. Sem isso, um erro de sinal na leitura
                # vira um total de despesas negativo, sem nada apontar a causa.
                # Quando a origem declara a natureza numa coluna (DESP/REC), ela
                # manda: estorno de despesa entra positivo e continua despesa.
                natureza_esperada = natureza_declarada or (
                    "receita" if lan.valor_centavos > 0 else "despesa"
                )
                if (categoria_id and categoria_id not in bidirecionais
                        and naturezas.get(categoria_id) != natureza_esperada):
                    categoria_id = subcategoria_id = None
                if categoria_id and lan.subcategoria_hint:
                    subcategoria_id = subs_idx.get(
                        (categoria_id, str(lan.subcategoria_hint).strip().casefold())
                    )
                if categoria_id:
                    status, confianca = "manual", 1.0
            # as regras rodam sempre, mesmo com a categoria ja resolvida pela
            # dica: e delas que sai o dono da receita (TAG e do André, BIOS e da
            # Rô, NUN e dos dois). Sem isso, uma receita minha lançada na
            # planilha dela entraria como dela, e o "quem trouxe o quê" mentiria.
            achado = classify.classificar_local(
                lan.descricao, lan.valor_centavos, regras, naturezas,
                natureza_hint=natureza_declarada,
                rotulo_origem=lan.categoria_hint,
                bidirecionais=bidirecionais,
            )
            if categoria_id is None and achado.classificado:
                categoria_id = achado.categoria_id
                subcategoria_id = achado.subcategoria_id
                status, confianca = achado.status, achado.confianca

            # O pagamento da fatura do cartao, reconhecido pelo que o sistema
            # ja sabe — o cadastro dos cartoes e o total de cada fatura — e nao
            # por regra de texto. Vale por cima de qualquer classificacao que
            # nao seja transferencia: as compras ja sao despesa na fatura, e
            # este debito e so o dinheiro mudando de bolso.
            # a memoria aprendida (o dono ja ensinou este texto) vale acima
            # dos detectores, aqui como na varredura da subida
            ensinado = achado.status == "auto_memoria" and categoria_id is not None
            if (conta["tipo"] == "corrente" and (lan.origem or origem) == "extrato"
                    and categoria_id != transferencia_id and transferencia_id and not ensinado):
                propria = _transferencia_propria(lan.descricao, lan.valor_centavos)
                if propria:
                    categoria_id = transferencia_id
                    subcategoria_id = _id_da_subcategoria(conn, transferencia_id, propria[0])
                    status, confianca = "auto_regra", 0.95
                    achado.explicacao = propria[1]
            # So no extrato: a linha "CARTAO NUBANK 32.238,29" da planilha de
            # julho e o proprio gasto do mes (a fatura de julho nunca vai ser
            # importada), nao o pagamento dele. A conta da planilha e do tipo
            # corrente, e sem esta porta o detector a tratava como banco e
            # julho perdia a fatura inteira.
            if (emissores and lan.valor_centavos < 0 and categoria_id != transferencia_id
                    and (lan.origem or origem) == "extrato" and not ensinado):
                motivo = cartoes.reconhecer(
                    lan.descricao, lan.valor_centavos, competencia_da_linha,
                    emissores_cadastrados=emissores, totais=totais_fatura,
                    data=lan.data, recebidos=recebidos,
                )
                if motivo and transferencia_id:
                    categoria_id, subcategoria_id = transferencia_id, pagamento_fatura_id
                    status, confianca = "auto_regra", 0.95
                    achado.explicacao = motivo

            # quem diz de quem é o gasto, em ordem: a coluna de pessoa do
            # arquivo, a regra, a própria descrição ("ALMOÇO ANDRÉ") e a
            # categoria que é de uma pessoa por natureza (Filhos & Pensão).
            declarado = (
                lan.pessoa_hint
                or achado.pessoa
                or pessoa_na_descricao(lan.descricao)
                or donos_de_categoria.get(categoria_id)
            )
            # ninguém disse: despesa sem dono declarado é da casa. Herdar o
            # titular da conta ou a resposta de "de quem é este arquivo" fazia
            # o gasto comum inteiro virar dívida de uma pessoa só — foi assim
            # que R$ 713 mil da casa apareceram como despesa da Rô.
            padrao = PESSOA_PADRAO if lan.valor_centavos < 0 else pessoa_padrao
            pessoa = _pessoa_valida(declarado, padrao)
            observacao = decisao.motivo or None
            if (achado.explicacao or "").startswith(("pagamento d", "transferência entre", "resgate de")):
                observacao = achado.explicacao

            if decisao.situacao == "confere_planilha":
                # o do extrato prevalece e herda a classificacao da planilha
                antigo = indice.registro(decisao.existente_id)
                if antigo and antigo["categoria_id"] and categoria_id is None:
                    categoria_id = antigo["categoria_id"]
                    subcategoria_id = antigo["subcategoria_id"]
                    pessoa = antigo["pessoa"]
                    status = "manual" if antigo["status"] == "manual" else "auto_regra"
                    confianca = 1.0
                observacao = "conferido com a planilha"
                substituir.append(decisao.existente_id)
                resumo["conferidos_planilha"] += 1

            if decisao.situacao == "realiza_previsao":
                # a receita que ele lançou à mão para o mês acabou de chegar de
                # verdade. Vale o do extrato — é o que aconteceu, com o valor
                # que aconteceu — e a previsão sai de cena, herdando para cá a
                # categoria e a pessoa que ele já tinha escolhido nela.
                previsto = indice.registro(decisao.existente_id)
                if previsto and previsto["categoria_id"]:
                    categoria_id = previsto["categoria_id"]
                    subcategoria_id = previsto["subcategoria_id"]
                    pessoa = previsto["pessoa"]
                    status, confianca = "manual", 1.0
                observacao = decisao.motivo
                substituir.append(decisao.existente_id)
                resumo["previsoes_realizadas"] += 1

            registro = {
                "data": lan.data,
                "competencia": competencia_da_linha,
                "descricao": lan.descricao,
                "descricao_norm": descricao_norm,
                "valor_centavos": lan.valor_centavos,
                "conta_id": conta_id,
                "categoria_id": categoria_id,
                "subcategoria_id": subcategoria_id,
                "pessoa": pessoa,
                "status": status,
                "confianca": confianca,
                "origem": lan.origem or origem,
                "hash_dedup": dedup.hash_lancamento(
                    conta_id, lan.data, lan.valor_centavos, descricao_norm
                ),
                "upload_id": upload_id,
                "ativo": decisao.entra_ativo,
                "observacao": observacao,
                "classificado_por": None,
                "classificacao_origem": (str(lan.categoria_hint).strip()
                                         if lan.categoria_hint else None),
                "natureza": natureza_declarada,
            }
            posicao = len(linhas)
            linhas.append(registro)
            if (registro["valor_centavos"] > 0 and registro["ativo"]
                    and decisao.situacao != "realiza_previsao"):
                entradas_sem_par.append(registro)

            # entra no índice com id provisório, para pegar linha repetida
            # dentro do próprio arquivo
            indice.adicionar({**registro, "id": proximo_temp})
            proximo_temp -= 1

            if decisao.e_duplicata:
                duplicatas.append((posicao, decisao))
                chave = ("duplicados_exatos" if decisao.situacao == "duplicata_exata"
                         else "duplicados_provaveis")
                resumo[chave] += 1
                continue

            resumo["importados"] += 1
            if categoria_id is None:
                pendentes_pos.append(posicao)
            else:
                resumo["auto"] += 1

        ids = _inserir_transacoes(conn, linhas, upload_id)
        temp_para_real = {-(i + 1): ids[i] for i in range(len(ids))}

        if substituir:
            # o mesmo tradutor que as duplicidades usam: a decisao pode apontar
            # para uma linha do proprio lote, que ainda tinha id provisorio
            # negativo. Sem traduzir, o UPDATE ... WHERE id IN (-1) nao acerta
            # nada e as duas linhas ficam ativas, com o resumo dizendo que uma
            # foi aposentada
            substituir = [temp_para_real.get(alvo, alvo) for alvo in substituir]
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(substituir))
                .values(
                    ativo=False,
                    observacao="substituído pelo lançamento do extrato",
                    # de quem foi a substituição: é por aqui que desfazer o
                    # upload devolve estas linhas ao mês
                    substituido_por=upload_id,
                )
            )
        if origem == "extrato" and conta["tipo"] == "corrente":
            resumo["previsoes_do_mes_fechado"] = _aposentar_previsoes_do_mes_fechado(
                conn, conta=conta, upload_id=upload_id,
                competencias={l.get("competencia") for l in linhas},
            )
        if duplicatas:
            conn.execute(
                sa.insert(db.duplicidades),
                [
                    {
                        "transacao_nova_id": ids[posicao],
                        "transacao_existente_id": temp_para_real.get(
                            decisao.existente_id, decisao.existente_id
                        ),
                        "tipo": "exata" if decisao.situacao == "duplicata_exata" else "provavel",
                        "motivo": decisao.motivo,
                    }
                    for posicao, decisao in duplicatas
                ],
            )

        # A regra da previsão realizada casa por mês e por ordem de grandeza.
        # Ela cobre o caso comum e erra por omissão em dois: o dinheiro cai no
        # mês seguinte ao previsto, ou o valor real foge demais do previsto
        # (mês de bônus). Nesses, ninguém pareia e a renda dobra calada — que é
        # justamente o que não pode acontecer sem alguém ficar sabendo.
        #
        # Então, quando este arquivo trouxe entradas e ainda sobra receita
        # prevista à mão por perto, a tela pergunta. Não decide nada: só põe as
        # duas coisas lado a lado para quem sabe olhar.
        resumo["previsoes_a_conferir"] = _previsoes_por_conferir(conn, entradas_sem_par)

        pendentes = [
            (ids[posicao], linhas[posicao]["descricao"], linhas[posicao]["valor_centavos"],
             linhas[posicao]["natureza"])
            for posicao in pendentes_pos
        ]

        # camada 3: IA so no que sobrou
        if pendentes and usar_ia and ai.disponivel():
            resolvidos = _classificar_com_ia(conn, pendentes)
            resumo["auto"] += resolvidos
            resumo["ia"] = resolvidos

        resumo["pendentes"] = len(pendentes) - resumo.get("ia", 0)
        conn.execute(
            sa.update(db.uploads)
            .where(db.uploads.c.id == upload_id)
            .values(
                importados=resumo["importados"],
                auto=resumo["auto"],
                pendentes=resumo["pendentes"],
                duplicados=resumo["duplicados_exatos"] + resumo["duplicados_provaveis"],
            )
        )
    return resumo


def _classificar_com_ia(conn, pendentes: list[tuple[int, str, int, str | None]]) -> int:
    plano = plano_para_ia(conn)
    cats_idx, subs_idx = _indice_categorias(conn)
    naturezas = classify._natureza_por_categoria(conn)
    resolvidos = 0

    for inicio in range(0, len(pendentes), ai.LOTE):
        fatia = pendentes[inicio : inicio + ai.LOTE]
        entrada = [(i, desc, valor) for i, (_id, desc, valor, _nat) in enumerate(fatia)]
        for sugestao in ai.sugerir_categorias(entrada, plano):
            if sugestao.indice >= len(fatia):
                continue
            transacao_id, _desc, valor, natureza = fatia[sugestao.indice]
            categoria_id = cats_idx.get(sugestao.categoria.casefold())
            if not categoria_id:
                continue
            # a natureza gravada manda sobre o sinal: e ela que impede a IA de
            # por uma compra de cartao numa categoria de receita
            natureza_esperada = natureza or ("receita" if valor > 0 else "despesa")
            if naturezas.get(categoria_id) != natureza_esperada:
                continue
            if sugestao.confianca < classify.LIMITE_CONFIANCA_IA:
                conn.execute(
                    sa.update(db.transacoes)
                    .where(db.transacoes.c.id == transacao_id)
                    .values(confianca=sugestao.confianca,
                            observacao=f"IA sugeriu {sugestao.categoria} (confiança baixa)")
                )
                continue
            subcategoria_id = (
                subs_idx.get((categoria_id, sugestao.subcategoria.casefold()))
                if sugestao.subcategoria
                else None
            )
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id == transacao_id)
                .values(
                    categoria_id=categoria_id,
                    subcategoria_id=subcategoria_id,
                    status="auto_ia",
                    confianca=sugestao.confianca,
                )
            )
            resolvidos += 1
    return resolvidos


# --------------------------------------------------------------------------
# classificacao manual
# --------------------------------------------------------------------------
def reclassificar(
    engine,
    transacao_id: int,
    *,
    categoria_id: int,
    subcategoria_id: int | None,
    pessoa: str | None,
    usuario: str,
    criar_regra: bool = True,
) -> bool:
    """Classifica um lancamento. Devolve se a correcao virou memoria.

    Nem toda correcao pode virar regra: "PIX QR CODE DINAMICO" nao identifica
    estabelecimento nenhum, e guardar essa chave faria todo Pix por QR herdar
    esta classificacao. Quem chama usa o retorno para nao prometer na tela um
    aprendizado que nao aconteceu.
    """
    with engine.begin() as conn:
        linha = conn.execute(
            sa.select(db.transacoes.c.descricao, db.transacoes.c.valor_centavos,
                      db.contas.c.tipo.label("tipo_conta"))
            .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
            .where(db.transacoes.c.id == transacao_id)
        ).fetchone()
        # a compra no cartao nao gera receita, e isso vale tambem para a mao: a
        # tela ja nao oferece categoria de receita para compra, e aqui e a
        # garantia de que nenhum outro caminho grava o que a tela nao oferece.
        # O credito no cartao (ajuste, cashback) e dinheiro entrando: o dono
        # pode chama-lo de renda
        if linha and linha.tipo_conta == "cartao" and linha.valor_centavos < 0:
            natureza = classify._natureza_por_categoria(conn).get(categoria_id)
            if natureza == "receita" and categoria_id not in classify.categorias_bidirecionais(conn):
                raise ValueError(
                    "cartão de crédito não gera receita: o que entra é compra, e o crédito "
                    "que aparece é estorno ou pagamento da fatura. Escolha uma categoria "
                    "de despesa — o estorno vai para a categoria do gasto que ele devolve."
                )
        valores = dict(
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            status="manual",
            confianca=1.0,
            classificado_por=usuario,
        )
        if pessoa:
            valores["pessoa"] = pessoa
        conn.execute(
            sa.update(db.transacoes).where(db.transacoes.c.id == transacao_id).values(**valores)
        )
        if not (criar_regra and linha):
            return False
        return classify.aprender(
            conn, linha.descricao, categoria_id, subcategoria_id, usuario, pessoa
        )


CONTA_MANUAL = "Lançamento manual"


def conta_manual(engine) -> int:
    """Conta propria do que e digitado a mao, criada na primeira vez.

    Fica separada das contas de banco de proposito: o que foi digitado nao veio
    de extrato nenhum, e misturar as duas coisas faria a crítica planilha ×
    extratos cobrar um comprovante que nunca vai existir.
    """
    with engine.begin() as conn:
        existente = conn.execute(
            sa.select(db.contas.c.id).where(db.contas.c.nome == CONTA_MANUAL)
        ).scalar()
        if existente:
            return existente
        return conn.execute(
            sa.insert(db.contas).values(
                nome=CONTA_MANUAL, tipo="corrente", titular="Casal",
                instituicao="—", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def lancar_manual(
    engine, *, competencia: str, valor_centavos: int, pessoa: str,
    categoria_id: int, subcategoria_id: int | None, descricao: str, usuario: str,
    natureza: str = "receita", conta_id: int | None = None, dia: int = 28,
) -> int:
    """Grava um lançamento digitado à mão e devolve o id.

    Duas necessidades, o mesmo caminho. A Rô recebe dos pacientes em dezenas de
    valores pequenos, e um total por mês responde a mesma pergunta com uma
    linha. E há despesa que não passa por extrato nenhum: os euros comprados em
    espécie saem da conta como um saque e viram uma viagem, e só quem gastou
    sabe disso.

    O valor entra sempre positivo e o sinal sai da natureza — quem digita pensa
    em "gastei 500", não em "menos quinhentos". O dia 28 é só um lugar no
    calendário: quem manda no relatório é a competência.
    """
    if natureza not in ("receita", "despesa"):
        raise ValueError("natureza precisa ser receita ou despesa")
    if valor_centavos <= 0:
        raise ValueError("informe um valor maior que zero")
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    ultimo = calendar.monthrange(ano, mes)[1]
    data = date(ano, mes, min(dia, ultimo))
    descricao = " ".join(str(descricao).split()) or (
        "RECEITA MANUAL" if natureza == "receita" else "DESPESA MANUAL"
    )
    descricao_norm = normalizar(descricao)
    conta = conta_id or conta_manual(engine)
    assinado = valor_centavos if natureza == "receita" else -valor_centavos

    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.transacoes).values(
                data=data,
                competencia=competencia,
                descricao=descricao,
                descricao_norm=descricao_norm,
                valor_centavos=assinado,
                conta_id=conta,
                categoria_id=categoria_id,
                subcategoria_id=subcategoria_id,
                pessoa=_pessoa_valida(pessoa, "Casal"),
                status="manual",
                confianca=1.0,
                origem="manual",
                natureza=natureza,
                hash_dedup=dedup.hash_lancamento(conta, data, assinado, descricao_norm),
                ativo=True,
                classificado_por=usuario,
            )
        ).inserted_primary_key[0]


def lancar_receita_manual(engine, **kw) -> int:
    """Atalho histórico; o caminho é o mesmo de lancar_manual."""
    return lancar_manual(engine, natureza="receita", **kw)


def lancamentos_manuais(conn, ano: int, natureza: str | None = None) -> list[dict]:
    """O que já foi digitado à mão no ano, para a tela poder revisar e apagar.

    O que já foi realizado pelo extrato vem junto, marcado. Ele lança à mão o
    que ainda vai entrar até o fim do ano; sumir com a linha quando o dinheiro
    chega de verdade tiraria da tela justamente a resposta que ela dá — o que
    já veio e o que falta vir.
    """
    condicoes = [
        db.transacoes.c.origem == "manual",
        db.transacoes.c.competencia.like(f"{ano}-%"),
    ]
    if natureza:
        condicoes.append(
            db.transacoes.c.valor_centavos > 0 if natureza == "receita"
            else db.transacoes.c.valor_centavos < 0
        )
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.competencia,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.ativo,
            db.categorias.c.nome.label("categoria"),
            db.subcategorias.c.nome.label("subcategoria"),
        )
        .select_from(
            db.transacoes.outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*condicoes)
        .order_by(db.transacoes.c.competencia.desc(), db.transacoes.c.id.desc())
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def receitas_manuais(conn, ano: int) -> list[dict]:
    """Atalho histórico: só as receitas digitadas à mão."""
    return lancamentos_manuais(conn, ano, natureza="receita")


def _dono_declarado(conn, donos_categoria: dict[int, str] | None = None) -> dict[str, list[int]]:
    """pessoa -> ids que deveriam ser dela e estão com outra.

    Duas fontes dizem de quem é o gasto sem margem para dúvida: a descrição,
    quando a Rô escreve o nome no fim ("ALMOÇO ANDRÉ", "CONSULTA RO"), e a
    categoria, quando ela é de uma pessoa por natureza — pensão e gasto com os
    filhos são do André, não despesa da casa a ser rateada.
    """
    if donos_categoria is None:
        donos_categoria = classify.donos_por_categoria(conn)
    consulta = sa.select(
        db.transacoes.c.id,
        db.transacoes.c.descricao,
        db.transacoes.c.pessoa,
        db.transacoes.c.categoria_id,
    ).where(db.transacoes.c.ativo == sa.true())

    alvos: dict[str, list[int]] = {}
    for linha in conn.execute(consulta):
        dono = pessoa_na_descricao(linha.descricao) or donos_categoria.get(linha.categoria_id)
        if dono and dono != linha.pessoa:
            alvos.setdefault(dono, []).append(linha.id)
    return alvos


def dono_pela_descricao(conn, donos_categoria: dict[int, str] | None = None) -> dict[str, int]:
    """Quantos lançamentos têm dono declarado e estão com outra pessoa."""
    return {
        pessoa: len(ids)
        for pessoa, ids in _dono_declarado(conn, donos_categoria).items()
    }


def alertas_de_dono(conn) -> tuple[dict[str, int], dict]:
    """Os dois avisos de dono da tela de classificação, numa varredura só.

    As duas perguntas precisam saber quais categorias têm dono fixo, e cada uma
    ia buscar essa lista por conta própria. São os dois avisos do topo da mesma
    tela: perguntar duas vezes a mesma coisa, no mesmo rerun, custa uma ida ao
    banco que ninguém vê.
    """
    donos = classify.donos_por_categoria(conn)
    return dono_pela_descricao(conn, donos), sem_dono_declarado(conn, donos)


def corrigir_dono_pela_descricao(engine) -> int:
    """Aplica o dono que a descrição ou a categoria declara."""
    with engine.begin() as conn:
        alvos = _dono_declarado(conn)

        # uma atualização por pessoa, e não uma por lançamento: são centenas de
        # linhas, e cada ida ao banco custa uns 150ms daqui até São Paulo
        total = 0
        for dono, ids in alvos.items():
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(ids))
                .values(pessoa=dono)
            )
            total += len(ids)
    return total


def sem_dono_declarado(conn, donos_categoria: dict[int, str] | None = None) -> dict:
    """O que está com uma pessoa só porque o upload perguntou de quem era.

    A planilha da casa mistura as contas do casal, e a maior parte das linhas
    não diz de quem é o gasto. Atribuir todas elas a uma pessoa só faz o
    relatório por pessoa mentir por um fator de vinte — o que é da casa vira
    dívida de quem enviou o arquivo. Devolve contagem e total, sem mudar nada.
    """
    # uma varredura só para as duas respostas: pedir a lista duas vezes eram
    # quatro idas ao banco (a consulta e os donos por categoria, cada uma
    # repetida) para responder a mesma pergunta com números diferentes dela
    achados = _ids_sem_dono(conn, com_valor=True, donos_categoria=donos_categoria)
    return {
        "quantidade": len(achados),
        "despesas": sum(-v for _i, v in achados if v < 0),
    }


def _ids_sem_dono(conn, com_valor: bool = False, donos_categoria: dict[int, str] | None = None):
    """Despesas da planilha que estão com uma pessoa sem a descrição dizer isso.

    Receita fica de fora: o dono dela vem da fonte (TAG é do André, BIOS é da
    Rô, NUN é dos dois), não da descrição — "SALARIO" não diz de quem é, e
    passar isso para o casal apagaria justamente a separação que existe para
    impedir dupla contagem.
    """
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.categoria_id,
        )
        .select_from(
            db.transacoes.outerjoin(
                db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id
            )
        )
        .where(
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.origem == "planilha",
            db.transacoes.c.pessoa != "Casal",
            sa.or_(db.categorias.c.natureza.is_(None), db.categorias.c.natureza != "receita"),
            sa.or_(db.transacoes.c.natureza.is_(None), db.transacoes.c.natureza != "receita"),
        )
    )
    if donos_categoria is None:
        donos_categoria = classify.donos_por_categoria(conn)
    achados = [
        (linha.id, linha.valor_centavos)
        for linha in conn.execute(consulta)
        if pessoa_na_descricao(linha.descricao) is None
        and linha.categoria_id not in donos_categoria
    ]
    return achados if com_valor else [i for i, _v in achados]


def atribuir_ao_casal(engine) -> int:
    """Passa para o casal o que veio da planilha sem dono declarado."""
    with engine.begin() as conn:
        ids = _ids_sem_dono(conn)
        for inicio in range(0, len(ids), 500):
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(ids[inicio : inicio + 500]))
                .values(pessoa="Casal")
            )
    return len(ids)


# --------------------------------------------------------------------------
# de-para: o vocabulario da Ro traduzido para o plano de contas
# --------------------------------------------------------------------------
def rotulos_pendentes(conn) -> list[dict]:
    """Os rotulos da origem que ainda tem lancamento sem categoria.

    Treze rotulos cobrem os 441 pendentes da carga inicial. Decidir treze vezes
    e um trabalho de minutos; decidir 441 vezes e um trabalho que nao acontece.
    """
    # rótulo já traduzido sai da lista mesmo que os lançamentos sigam na fila:
    # eles continuam lá só para escolher a subcategoria, e a decisão de
    # categoria já foi tomada — repetir a pergunta seria pedir de novo o que já
    # foi respondido
    ja_traduzidos = sa.select(db.de_para.c.rotulo).scalar_subquery()
    consulta = (
        sa.select(
            db.transacoes.c.classificacao_origem.label("rotulo"),
            sa.func.count().label("quantidade"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .where(
            db.transacoes.c.status == "pendente",
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.classificacao_origem.isnot(None),
            db.transacoes.c.classificacao_origem.notin_(ja_traduzidos),
        )
        .group_by(db.transacoes.c.classificacao_origem)
        .order_by(sa.func.count().desc())
    )
    return [
        {"rotulo": linha.rotulo, "quantidade": int(linha.quantidade),
         "total": int(linha.total or 0)}
        for linha in conn.execute(consulta)
        if (linha.rotulo or "").strip()
    ]


def listar_de_para(conn) -> dict[str, dict]:
    """rotulo -> tradução já decidida."""
    consulta = (
        sa.select(
            db.de_para.c.rotulo,
            db.de_para.c.categoria_id,
            db.de_para.c.subcategoria_id,
            db.categorias.c.nome.label("categoria"),
            db.subcategorias.c.nome.label("subcategoria"),
        )
        .select_from(
            db.de_para.join(db.categorias, db.de_para.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.de_para.c.subcategoria_id == db.subcategorias.c.id)
        )
    )
    return {linha.rotulo: dict(linha._mapping) for linha in conn.execute(consulta)}


def salvar_de_para(
    engine, *, rotulo: str, categoria_id: int, subcategoria_id: int | None, usuario: str,
) -> int:
    """Guarda a tradução e aplica nos pendentes daquele rótulo.

    Guardar sem aplicar deixaria o trabalho pela metade, e aplicar sem guardar
    faria a próxima importação perguntar tudo de novo. Devolve quantos
    lançamentos saíram da fila.
    """
    with engine.begin() as conn:
        existente = conn.execute(
            sa.select(db.de_para.c.id).where(db.de_para.c.rotulo == rotulo)
        ).scalar()
        valores = dict(
            categoria_id=categoria_id, subcategoria_id=subcategoria_id, criado_por=usuario
        )
        if existente:
            conn.execute(sa.update(db.de_para).where(db.de_para.c.id == existente).values(**valores))
        else:
            conn.execute(sa.insert(db.de_para).values(rotulo=rotulo, **valores))

        # a guarda de natureza vale aqui como em todo o resto: um rótulo de
        # despesa não pode levar para lá o dinheiro que entrou
        natureza = conn.execute(
            sa.select(db.categorias.c.natureza).where(db.categorias.c.id == categoria_id)
        ).scalar()
        dono = classify.donos_por_categoria(conn).get(categoria_id)

        condicoes = [
            db.transacoes.c.status == "pendente",
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.classificacao_origem == rotulo,
            db.transacoes.c.valor_centavos > 0 if natureza == "receita"
            else db.transacoes.c.valor_centavos < 0,
        ]
        # traduzir só até a categoria resolve o relatório (é a categoria que
        # soma) mas não encerra o assunto: a subcategoria ainda é escolha de
        # quem olha o lançamento. Nesse caso a linha continua na fila, agora
        # com a categoria preenchida — falta só o detalhe.
        tem_subcategoria = conn.execute(
            sa.select(sa.func.count())
            .select_from(db.subcategorias)
            .where(
                db.subcategorias.c.categoria_id == categoria_id,
                db.subcategorias.c.ativa == sa.true(),
            )
        ).scalar()
        so_categoria = subcategoria_id is None and bool(tem_subcategoria)
        atualizacao = dict(
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            status="pendente" if so_categoria else "manual",
            confianca=None if so_categoria else 1.0,
            classificado_por=None if so_categoria else usuario,
        )
        if dono:
            atualizacao["pessoa"] = dono
        resultado = conn.execute(
            sa.update(db.transacoes).where(*condicoes).values(**atualizacao)
        )
    return resultado.rowcount


def apagar_de_para(engine, rotulo: str) -> int:
    """Desfaz a tradução e devolve à fila o que ela classificou.

    Devolve quantos lançamentos voltaram a ficar pendentes. Só volta o que
    está exatamente onde a tradução colocou: se depois disso alguém corrigiu um
    lançamento para outra categoria, essa correção fica de pé — desfazer uma
    decisão em massa não pode desfazer o trabalho fino feito em cima dela.
    """
    with engine.begin() as conn:
        traducao = conn.execute(
            sa.select(db.de_para.c.categoria_id, db.de_para.c.subcategoria_id)
            .where(db.de_para.c.rotulo == rotulo)
        ).first()
        devolvidos = 0
        if traducao:
            devolvidos = conn.execute(
                sa.update(db.transacoes)
                .where(
                    db.transacoes.c.classificacao_origem == rotulo,
                    db.transacoes.c.categoria_id == traducao.categoria_id,
                    db.transacoes.c.status == "manual",
                    db.transacoes.c.ativo == sa.true(),
                )
                .values(categoria_id=None, subcategoria_id=None,
                        status="pendente", confianca=None, classificado_por=None)
            ).rowcount
        conn.execute(sa.delete(db.de_para).where(db.de_para.c.rotulo == rotulo))
    return devolvidos


def _id_da_categoria(conn, nome: str) -> int | None:
    return conn.execute(sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)).scalar()


def _id_da_subcategoria(conn, categoria_id: int | None, nome: str) -> int | None:
    if categoria_id is None:
        return None
    return conn.execute(
        sa.select(db.subcategorias.c.id).where(
            db.subcategorias.c.categoria_id == categoria_id, db.subcategorias.c.nome == nome,
        )
    ).scalar()


def _config(conn, chave: str) -> str | None:
    return conn.execute(sa.select(db.config.c.valor).where(db.config.c.chave == chave)).scalar()


def _gravar_config(conn, chave: str, valor: str) -> None:
    if _config(conn, chave) is None:
        conn.execute(sa.insert(db.config).values(chave=chave, valor=valor))
    else:
        conn.execute(sa.update(db.config).where(db.config.c.chave == chave).values(valor=valor))


# Muda quando uma regra de varredura muda. A subida so roda a cadeia de
# varreduras quando esta versao ou o conjunto de uploads mudou: a cadeia e
# idempotente, mas custava uma centena de idas ao banco a cada start, e cada
# publicacao e um start
VERSAO_DAS_VARREDURAS = "2026-09-18.1"
_CHAVE_VARREDURAS = "varreduras"


def _assinatura_das_varreduras(conn) -> str:
    maior, quantos = conn.execute(
        sa.select(sa.func.coalesce(sa.func.max(db.uploads.c.id), 0), sa.func.count())
        .select_from(db.uploads)
    ).one()
    return f"{VERSAO_DAS_VARREDURAS}|{maior}|{quantos}"


def varreduras_pendentes(engine) -> bool:
    with engine.connect() as conn:
        return _config(conn, _CHAVE_VARREDURAS) != _assinatura_das_varreduras(conn)


def registrar_varreduras(engine) -> None:
    with engine.begin() as conn:
        _gravar_config(conn, _CHAVE_VARREDURAS, _assinatura_das_varreduras(conn))


DEVOLUCAO_DA_CONFERENCIA = "conferencia_devolvida_2026_09"


def devolver_descartes_da_conferencia(engine) -> int:
    """Devolve ao mes o que a critica descartou enquanto o alcance dela estava
    errado. Roda uma vez so; a marca fica em `config`.

    A critica passou a olhar todo mes com planilha e extrato — e o mes em curso
    entrou, com o extrato parcial. "O que so esta na planilha" ali era o resto
    do mes que ainda nao tinha acontecido, receitas previstas incluidas, e o
    botao de descartar apagou tudo isso. Como a critica nunca tinha rodado
    antes, todo descarte que existe veio desse alcance errado: volta tudo, e
    quem quiser descartar de novo faz isso com o alcance certo.
    """
    with engine.begin() as conn:
        if _config(conn, DEVOLUCAO_DA_CONFERENCIA):
            return 0
        resultado = conn.execute(
            sa.update(db.transacoes)
            .where(
                db.transacoes.c.ativo == sa.false(),
                db.transacoes.c.observacao.like("descartado na conferência por %"),
            )
            .values(ativo=True,
                    observacao="devolvido: a conferência alcançava o mês em curso")
        )
        _gravar_config(conn, DEVOLUCAO_DA_CONFERENCIA, "1")
    return resultado.rowcount or 0


# quem, no texto de um PIX ou TED, esta na outra ponta
_PREFIXO_CONTRAPARTE = re.compile(
    r"\b(?:REMET|REM|DEST|DES|PIX TRANSF|TED TRANSF|TED)\b\s*"
)
_NOMES_DA_CASA = {"ANDRE": "André", "RO": "Rô", "ROSANA": "Rô"}
# a outra ponta e uma instituicao financeira: o dinheiro e do proprio dono,
# voltando de uma aplicacao — resgate, nao renda
_INSTITUICAO = re.compile(
    r"\b(BANCO|BCO|INVESTIMENTOS?|CORRETORA|DTVM|CCTVM|PACTUAL|ASSET|TESOURO|"
    r"NU PAGAMENTOS|INTER S ?A|XP|BTG)\b"
)


def _contraparte(descricao: str) -> list[str]:
    """As palavras de quem esta na outra ponta, ou nada."""
    from core.texto import sem_acento

    texto = re.sub(r"[.:;,/\-]", " ", sem_acento(descricao).upper())
    texto = " ".join(texto.split())
    # o marcador da outra ponta vem DEPOIS do historico: em "TED TRANSF ELET
    # DISPON REMET ANDRE", o "TED TRANSF" do comeco e o historico, e o
    # "REMET" do fim e quem manda. Vale a ultima ocorrencia
    achados = list(_PREFIXO_CONTRAPARTE.finditer(texto))
    if not achados:
        return []
    resto = re.sub(r"^[\d ]+", "", texto[achados[-1].end():]).strip()   # "102 0001 FULANA" -> "FULANA"
    return resto.split()


def contraparte_da_casa(descricao: str) -> str | None:
    """A pessoa da casa que e a outra ponta deste PIX/TED — ou None.

    "PIX RECEBIDO REM: Andre Luiz Rodrigues" e o Andre mandando para si mesmo
    de outra conta; "TED-TRANSF ELET DISPON REMET.ANDRE LUIZ" idem. Isso nao e
    renda, e o PIX dele para a Ro nao e gasto: e dinheiro mudando de bolso
    dentro da casa.

    A comparacao e por palavra inteira, nunca por comeco — a mesma regra do
    portador do cartao. A primeira versao comparava por prefixo, e os apelidos
    de uma letra ("R", "C") fizeram RAFAEL virar Ro e CAIXA LOTERIAS virar
    Casal. ANDREA, paciente da Ro, nao e ANDRE. Os apelidos do segredo (nome
    completo como sai no extrato) entram tambem, por palavras inteiras.
    """
    from core.texto import sem_acento

    palavras = _contraparte(descricao)
    if not palavras:
        return None
    apelidos = {**APELIDOS_PESSOA, **_apelidos_configurados()}
    for apelido, pessoa in apelidos.items():
        chave = " ".join(sem_acento(apelido).upper().split())
        if len(chave) < 3:
            continue
        n = len(chave.split())
        if " ".join(palavras[:n]) == chave:
            return pessoa
    return _NOMES_DA_CASA.get(palavras[0])


def resgate_de_investimento(descricao: str, valor_centavos: int) -> bool:
    """Credito cuja outra ponta e uma instituicao financeira: resgate, nao renda.

    "PIX RECEBIDO REM: BANCO INTER SA" e o dinheiro do proprio dono voltando de
    uma aplicacao. O salario vem de uma empresa ("TAG PARTNERS LTDA."), o
    paciente vem com nome de gente; o banco como remetente e o proprio dono.
    Entrava como pro-labore — R$ 11.944,57 de resgate virando renda de agosto.
    """
    if valor_centavos <= 0:
        return False
    palavras = _contraparte(descricao)
    return bool(palavras) and bool(_INSTITUICAO.search(" ".join(palavras)))


_RESGATE_NO_TEXTO = re.compile(r"\b(RESGATE|RES APLIC|RESG APLIC)\b")
# resgate DE QUE: so de aplicacao. Resgate de pontos, de seguro, do FGTS e
# dinheiro novo, e o texto tem de dizer que era aplicacao
_CONTEXTO_DE_APLICACAO = re.compile(
    r"\b(CDB|LCI|LCA|LC|RDB|CRI|CRA|FUNDO|FUNDOS|FIC|FI|APLIC|APLICACAO|INVEST|INVESTIMENTO|"
    r"INVESTIMENTOS|TESOURO|POUP|POUPANCA|CDI|DEBENTURE|RENDA FIXA|RF|AUTOMATICO|AUT)\b"
)
_NAO_E_APLICACAO = re.compile(r"\b(PONTOS|SEGURO|FGTS|PREMIO|CASHBACK|MILHAS|CONSORCIO)\b")


def aplicacao_resgatada(descricao: str, valor_centavos: int) -> bool:
    """O banco diz "RESGATE" de uma aplicacao num credito: e o principal
    voltando, nao renda.

    A regra de texto mandava "RESGATE CDB" para Rendimentos, e o relatorio
    somava como receita os 15.000 que so tinham saido da aplicacao. O
    rendimento de verdade vem em linha propria ("REND PAGO", "RENDIMENTO").
    "RESGATE DE PONTOS" e "RESGATE SEGURO" nao sao aplicacao — ficam como estao.
    """
    from core.texto import sem_acento

    if valor_centavos <= 0:
        return False
    texto = sem_acento(descricao).upper()
    return (bool(_RESGATE_NO_TEXTO.search(texto))
            and bool(_CONTEXTO_DE_APLICACAO.search(texto))
            and not _NAO_E_APLICACAO.search(texto))


def _nao_foi_a_mao():
    """Filtro SQL: fora o que alguem classificou na tela ou ensinou por regra."""
    return sa.or_(db.transacoes.c.status.is_(None),
                  db.transacoes.c.status.not_in(("manual", "auto_memoria")))


def _transferencia_propria(descricao: str, valor_centavos: int) -> tuple[str, str] | None:
    """(subcategoria, motivo) quando o lancamento e dinheiro da propria casa."""
    pessoa = contraparte_da_casa(descricao)
    if pessoa:
        return "Entre Contas Próprias", f"transferência entre contas da casa ({pessoa})"
    if resgate_de_investimento(descricao, valor_centavos):
        return "Aplicação / Resgate", "resgate de aplicação: a outra ponta é uma instituição financeira"
    if aplicacao_resgatada(descricao, valor_centavos):
        return "Aplicação / Resgate", "resgate de aplicação: é o principal voltando, não renda"
    return None


def marcar_transferencias_proprias(engine) -> int:
    """Passa pelos PIX/TED ja gravados em conta corrente e marca os que sao
    entre contas da casa. Idempotente; roda na subida."""
    with engine.begin() as conn:
        transferencia_id = _id_da_categoria(conn, analytics.CATEGORIA_TRANSFERENCIA)
        if transferencia_id is None:
            return 0
        candidatos = conn.execute(
            sa.select(db.transacoes.c.id, db.transacoes.c.descricao, db.transacoes.c.valor_centavos)
            .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
            .where(
                db.contas.c.tipo == "corrente",
                db.transacoes.c.origem == "extrato",
                db.transacoes.c.ativo == sa.true(),
                sa.or_(db.transacoes.c.categoria_id.is_(None),
                       db.transacoes.c.categoria_id != transferencia_id),
                # o que foi classificado a mao e decisao de gente; a varredura
                # da subida nao passa por cima — passava, e toda correcao
                # feita na tela voltava atras no reboot seguinte
                _nao_foi_a_mao(),
            )
        ).all()
        subcategorias: dict[str, int | None] = {}
        por_destino: dict[tuple[str, str], list[int]] = {}
        for linha in candidatos:
            achado = _transferencia_propria(linha.descricao, linha.valor_centavos)
            if not achado:
                continue
            por_destino.setdefault(achado, []).append(linha.id)
        # um UPDATE por (subcategoria, motivo), nao um por linha
        for (subcategoria, motivo), ids in por_destino.items():
            if subcategoria not in subcategorias:
                subcategorias[subcategoria] = _id_da_subcategoria(conn, transferencia_id, subcategoria)
            conn.execute(
                sa.update(db.transacoes).where(db.transacoes.c.id.in_(ids))
                .values(categoria_id=transferencia_id,
                        subcategoria_id=subcategorias[subcategoria],
                        status="auto_regra", confianca=0.95,
                        # a marca da conferencia com a planilha e o que a
                        # critica conta; o motivo nao pode apaga-la
                        observacao=sa.case(
                            (db.transacoes.c.observacao == "conferido com a planilha",
                             db.transacoes.c.observacao),
                            else_=motivo))
            )
    return sum(len(ids) for ids in por_destino.values())


def _aposentar_previsoes_do_mes_fechado(conn, *, conta: dict, upload_id: int | None,
                                        competencias) -> int:
    """Mes fechado + extrato da pessoa no sistema = a receita prevista dela ja
    aconteceu, e esta no extrato.

    A previsao ("ATENDIMENTOS 15.000", "PRO LABORE 20.000") e a resposta de
    quem ainda nao tinha o extrato. Quando o mes fecha e o extrato da conta
    daquela pessoa entra, o dinheiro de verdade esta ali — em dezenas de PIX
    de pacientes, ou num salario pago em duas partes que nenhum pareamento
    por valor casa. Manter a previsao e somar a renda duas vezes. Vale so
    para mes fechado (o extrato parcial de um mes em curso nao diz que o
    resto nao vai acontecer) e so para a pessoa titular da conta. Aponta
    para o upload: desfaze-lo devolve a previsao.
    """
    em_curso = date.today().strftime("%Y-%m")
    fechadas = sorted(c for c in competencias if c and c < em_curso)
    # so a pessoa titular, nunca "Casal": a renda do casal (aluguel) pode cair
    # numa conta que nao esta no sistema, e um debito qualquer na conjunta
    # nao prova que ela chegou
    if not fechadas or conta.get("titular") not in ("André", "Rô"):
        return 0
    transferencia_id = _id_da_categoria(conn, analytics.CATEGORIA_TRANSFERENCIA)
    total = 0
    for mes in fechadas:
        # o que de fato entrou nesta conta no mes, fora transferencia
        entrou = conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(db.transacoes.c.valor_centavos), 0))
            .where(db.transacoes.c.conta_id == conta["id"],
                   db.transacoes.c.origem == "extrato",
                   db.transacoes.c.ativo == sa.true(),
                   db.transacoes.c.competencia == mes,
                   db.transacoes.c.valor_centavos > 0,
                   sa.or_(db.transacoes.c.categoria_id.is_(None),
                          db.transacoes.c.categoria_id != transferencia_id))
        ).scalar() or 0
        if entrou <= 0:
            continue
        previstas = conn.execute(
            sa.select(db.transacoes.c.id, db.transacoes.c.valor_centavos)
            .where(
                # so a planilha: o que foi lancado a mao e decisao de gente
                db.transacoes.c.origem == "planilha",
                db.transacoes.c.ativo == sa.true(),
                db.transacoes.c.valor_centavos > 0,
                # estorno de despesa e positivo e nao e previsao de renda
                sa.or_(db.transacoes.c.natureza.is_(None),
                       db.transacoes.c.natureza != "despesa"),
                db.transacoes.c.pessoa == conta["titular"],
                db.transacoes.c.competencia == mes,
            )
        ).all()
        # a previsao so morre se o extrato trouxe ao menos metade dela: um
        # extrato com um PIX de R$ 840 nao prova R$ 15.000 de atendimentos
        ids = [p.id for p in previstas if entrou * 2 >= p.valor_centavos]
        if not ids:
            continue
        resultado = conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id.in_(ids))
            .values(
                ativo=False, substituido_por=upload_id,
                observacao=f"previsão realizada: o extrato de {conta['nome']} do mês "
                           "está no sistema",
            )
        )
        total += resultado.rowcount or 0
    return total


# v4: a parcela conta no ciclo da fatura que a cobra, em qualquer cartao; a
# marca nova faz a passagem rodar de novo sobre o que ja esta gravado
CARTAO_PELA_COMPRA = "cartao_pela_compra_2026_09_v5"


def contar_cartao_pela_compra(engine) -> dict:
    """Muda, uma vez so, as compras de cartao ja gravadas para o mes da compra.

    Ate aqui toda linha da fatura levava o mes do menu — o da fatura. A casa
    conta pela data da compra, e a planilha de julho ja tinha, item a item,
    as compras de 17 a 31 de julho que a fatura de agosto trouxe. Cada linha
    vai para o mes da propria data (com a folga de `competencia_da_compra`),
    e em cada mes fechado que recebeu compras a planilha e conferida pelo
    valor, como o botao da critica faria. Roda uma vez; a marca fica em config.
    """
    from . import reconcile

    with engine.begin() as conn:
        if _config(conn, CARTAO_PELA_COMPRA):
            return {"movidas": 0, "conferidas": 0}
        movidas, meses = _recompetenciar_cartao(conn)
        _gravar_config(conn, CARTAO_PELA_COMPRA, "1")
    em_curso = date.today().strftime("%Y-%m")
    conferidas = 0
    for mes in sorted(m for m in meses if m < em_curso):
        conferidas += reconcile.aposentar_pares_exatos(engine, "sistema", mes)
    return {"movidas": movidas, "conferidas": conferidas}


MARCA_MES_DA_PLANILHA = analytics.MARCA_MES_DA_PLANILHA


def meses_so_da_planilha(conn) -> set[str]:
    """Os meses de planilha anteriores ao primeiro extrato de conta corrente.

    Ate julho a casa vive da planilha, anotada dia a dia; o primeiro extrato
    de banco e o de agosto, e nenhum de julho vai entrar. Nesses meses a
    planilha e a verdade inteira. Depois do primeiro extrato, nao: o mes em
    curso ainda sem extrato (setembro) e o mes seguinte antes do upload sao
    meses dos uploads, so que atrasados.
    """
    por_origem: dict[str, set[str]] = {"planilha": set(), "banco": set()}
    linhas = conn.execute(
        sa.select(db.transacoes.c.competencia, db.transacoes.c.origem, db.contas.c.tipo)
        .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
        .distinct()
    )
    for linha in linhas:
        if linha.origem == "planilha":
            por_origem["planilha"].add(linha.competencia)
        elif linha.origem == "extrato" and linha.tipo == "corrente":
            por_origem["banco"].add(linha.competencia)
    if not por_origem["banco"]:
        return set()
    primeiro_extrato = min(por_origem["banco"])
    return {c for c in por_origem["planilha"] if c < primeiro_extrato}


def _recompetenciar_cartao(conn, upload_id: int | None = None) -> tuple[int, set[str]]:
    """Da a cada compra de cartao gravada o mes que a regra de hoje da.

    Devolve (quantas mudaram, meses que receberam linha). Com `upload_id`,
    so as linhas daquele arquivo.
    """
    from parsers.base import DURACAO_MAXIMA_DO_CICLO, competencia_da_compra

    consulta = (
        sa.select(db.transacoes.c.id, db.transacoes.c.data, db.transacoes.c.competencia,
                  db.transacoes.c.descricao, db.transacoes.c.valor_centavos,
                  db.transacoes.c.upload_id,
                  db.uploads.c.competencia.label("mes_da_fatura"))
        .select_from(
            db.transacoes
            .join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.uploads, db.transacoes.c.upload_id == db.uploads.c.id)
        )
        .where(db.contas.c.tipo == "cartao", db.transacoes.c.origem == "extrato")
    )
    if upload_id is not None:
        consulta = consulta.where(db.transacoes.c.upload_id == upload_id)
    linhas = conn.execute(consulta).all()
    # o ciclo de cada fatura sai das linhas dela: a janela de um mes que
    # termina na ultima compra (a mesma regra de parsers.base.inicio_do_ciclo)
    datas_por_upload: dict[int | None, list[date]] = {}
    for linha in linhas:
        if linha.valor_centavos < 0:
            datas_por_upload.setdefault(linha.upload_id, []).append(linha.data)
    inicio_por_upload: dict[int | None, date] = {}
    for chave, datas in datas_por_upload.items():
        ultimo = max(datas)
        inicio_por_upload[chave] = min(d for d in datas if d >= ultimo - DURACAO_MAXIMA_DO_CICLO)
    por_mes: dict[str, list[int]] = {}
    for linha in linhas:
        fatura = linha.mes_da_fatura or linha.competencia
        nova = competencia_da_compra(
            linha.data, fatura, linha.descricao or "",
            inicio_do_ciclo=inicio_por_upload.get(linha.upload_id),
        )
        if nova != linha.competencia:
            por_mes.setdefault(nova, []).append(linha.id)
    # um UPDATE por mes de destino, nao um por linha
    for nova, ids in por_mes.items():
        conn.execute(
            sa.update(db.transacoes).where(db.transacoes.c.id.in_(ids)).values(competencia=nova)
        )
    return sum(len(ids) for ids in por_mes.values()), set(por_mes)


def mudar_mes_da_fatura(engine, upload_id: int, competencia: str) -> int:
    """Corrige o "mes da fatura" de um upload de cartao ja gravado e da as
    linhas dele o mes que a regra manda. E o conserto para a fatura do XP
    paga em setembro que e inteira de agosto e foi enviada como setembro."""
    with engine.begin() as conn:
        conn.execute(
            sa.update(db.uploads).where(db.uploads.c.id == upload_id)
            .values(competencia=competencia)
        )
        movidas, _ = _recompetenciar_cartao(conn, upload_id)
    return movidas


def aplicar_meses_da_planilha(engine) -> dict:
    """Num mes que e so da planilha, a compra de cartao datada nele nao conta.

    A fatura de agosto traz compras de 17 a 31 de julho, e julho e da
    planilha: o dono anotou o mes inteiro a mao, do jeito dele, e nao vai
    importar extrato nenhum de julho. Contar a compra da fatura por cima da
    anotacao dobrava o que ele anotou; tentar parear pelo valor aposentava
    metade e deixava a outra metade dobrada. A regra fecha o assunto: mes
    sem extrato de banco, vale a planilha; a compra de cartao datada nele
    fica de fora, marcada, e volta sozinha se um dia o extrato daquele mes
    entrar. Idempotente; roda na subida.
    """
    with engine.begin() as conn:
        meses = meses_so_da_planilha(conn)
        cartao = (
            db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
        )
        ids_cartao = [
            l.id for l in conn.execute(
                sa.select(db.transacoes.c.id).select_from(cartao)
                .where(db.contas.c.tipo == "cartao", db.transacoes.c.origem == "extrato",
                       db.transacoes.c.ativo == sa.true(),
                       db.transacoes.c.competencia.in_(sorted(meses)) if meses else sa.false())
            )
        ]
        retiradas = 0
        if ids_cartao:
            retiradas = conn.execute(
                sa.update(db.transacoes).where(db.transacoes.c.id.in_(ids_cartao))
                .values(ativo=False, observacao=MARCA_MES_DA_PLANILHA)
            ).rowcount or 0
        # o caminho de volta: o mes ganhou extrato, a compra volta a contar
        devolvidas = conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.ativo == sa.false(),
                   db.transacoes.c.observacao == MARCA_MES_DA_PLANILHA,
                   db.transacoes.c.competencia.not_in(sorted(meses)) if meses else sa.true())
            .values(ativo=True, observacao=None)
        ).rowcount or 0
        # e a planilha desses meses volta inteira: o que a conferencia pelo
        # valor tinha aposentado contra uma compra que agora nao vale
        planilha_de_volta = 0
        if meses:
            uploads_de_cartao = (
                sa.select(db.uploads.c.id)
                .select_from(db.uploads.join(db.contas, db.uploads.c.conta_id == db.contas.c.id))
                .where(db.contas.c.tipo == "cartao")
            )
            planilha_de_volta = conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.origem == "planilha",
                       db.transacoes.c.ativo == sa.false(),
                       db.transacoes.c.competencia.in_(sorted(meses)),
                       # so o que uma fatura de cartao aposentou: a compra que
                       # a aposentou e a que deixa de contar
                       db.transacoes.c.substituido_por.in_(uploads_de_cartao))
                .values(ativo=True, substituido_por=None,
                        observacao="devolvida: o mês é da planilha")
            ).rowcount or 0
    return {"retiradas": retiradas, "devolvidas": devolvidas, "planilha_de_volta": planilha_de_volta}


def aposentar_previsoes_de_meses_fechados(engine) -> int:
    """Passa pelos meses fechados que ja tem extrato e aposenta a receita
    prevista da pessoa titular. Idempotente; roda na subida — e o que
    conserta o que entrou antes desta regra existir."""
    em_curso = date.today().strftime("%Y-%m")
    with engine.begin() as conn:
        transferencia_id = _id_da_categoria(conn, analytics.CATEGORIA_TRANSFERENCIA)
        # uma consulta: por (conta, mes fechado) o upload mais recente e o que
        # entrou de credito fora transferencia. Era conta_por_id + tres
        # consultas por par, em todo start
        entrou = sa.func.sum(sa.case(
            (sa.and_(db.transacoes.c.valor_centavos > 0,
                     sa.or_(db.transacoes.c.categoria_id.is_(None),
                            db.transacoes.c.categoria_id != transferencia_id)),
             db.transacoes.c.valor_centavos), else_=0))
        cobertos = conn.execute(
            sa.select(db.transacoes.c.conta_id, db.contas.c.nome, db.contas.c.titular,
                      db.transacoes.c.competencia,
                      sa.func.max(db.transacoes.c.upload_id).label("upload_id"),
                      entrou.label("entrou"))
            .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
            .where(db.contas.c.tipo == "corrente",
                   db.contas.c.titular.in_(("André", "Rô")),
                   db.transacoes.c.origem == "extrato",
                   db.transacoes.c.ativo == sa.true(),
                   db.transacoes.c.competencia < em_curso)
            .group_by(db.transacoes.c.conta_id, db.contas.c.nome, db.contas.c.titular,
                      db.transacoes.c.competencia)
        ).all()
        cobertos = [c for c in cobertos if (c.entrou or 0) > 0]
        if not cobertos:
            return 0
        meses = sorted({c.competencia for c in cobertos})
        previstas = conn.execute(
            sa.select(db.transacoes.c.id, db.transacoes.c.valor_centavos,
                      db.transacoes.c.pessoa, db.transacoes.c.competencia)
            .where(db.transacoes.c.origem == "planilha",
                   db.transacoes.c.ativo == sa.true(),
                   db.transacoes.c.valor_centavos > 0,
                   sa.or_(db.transacoes.c.natureza.is_(None),
                          db.transacoes.c.natureza != "despesa"),
                   db.transacoes.c.pessoa.in_(("André", "Rô")),
                   db.transacoes.c.competencia.in_(meses))
        ).all()
        if not previstas:
            return 0
        total = 0
        for c in cobertos:
            ids = [p.id for p in previstas
                   if p.pessoa == c.titular and p.competencia == c.competencia
                   and c.entrou * 2 >= p.valor_centavos]
            if not ids:
                continue
            total += conn.execute(
                sa.update(db.transacoes).where(db.transacoes.c.id.in_(ids))
                .values(ativo=False, substituido_por=c.upload_id,
                        observacao=f"previsão realizada: o extrato de {c.nome} do mês "
                                   "está no sistema")
            ).rowcount or 0
            previstas = [p for p in previstas if p.id not in ids]
    return total


def marcar_pagamentos_de_cartao(engine) -> int:
    """Passa por todo debito de conta corrente ja gravado e marca os pagamentos
    de cartao que ainda estao contados como despesa. Idempotente; roda na subida.

    E o que conserta o que entrou antes do detector existir: o pagamento do
    Nubank e do XP no extrato do Bradesco de agosto, somados as compras que ja
    estavam nas faturas.
    """
    with engine.begin() as conn:
        emissores = cartoes.emissores(conn)
        if not emissores:
            return 0
        totais = cartoes.totais_de_fatura(conn)
        recebidos = cartoes.pagamentos_recebidos(conn)
        transferencia_id = _id_da_categoria(conn, analytics.CATEGORIA_TRANSFERENCIA)
        if transferencia_id is None:
            return 0
        pagamento_id = _id_da_subcategoria(conn, transferencia_id, "Pagamento de Fatura")
        candidatos = conn.execute(
            sa.select(db.transacoes.c.id, db.transacoes.c.descricao,
                      db.transacoes.c.valor_centavos, db.transacoes.c.competencia,
                      db.transacoes.c.data)
            .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
            .where(
                db.contas.c.tipo == "corrente",
                db.transacoes.c.origem == "extrato",
                db.transacoes.c.ativo == sa.true(),
                db.transacoes.c.valor_centavos < 0,
                sa.or_(db.transacoes.c.categoria_id.is_(None),
                       db.transacoes.c.categoria_id != transferencia_id),
                _nao_foi_a_mao(),
            )
        ).all()
        por_motivo: dict[str, list[int]] = {}
        for linha in candidatos:
            motivo = cartoes.reconhecer(
                linha.descricao, linha.valor_centavos, linha.competencia,
                emissores_cadastrados=emissores, totais=totais,
                data=linha.data, recebidos=recebidos,
            )
            if motivo:
                por_motivo.setdefault(motivo, []).append(linha.id)
        for motivo, ids in por_motivo.items():
            conn.execute(
                sa.update(db.transacoes).where(db.transacoes.c.id.in_(ids))
                .values(categoria_id=transferencia_id, subcategoria_id=pagamento_id,
                        status="auto_regra", confianca=0.95,
                        observacao=sa.case(
                            (db.transacoes.c.observacao == "conferido com a planilha",
                             db.transacoes.c.observacao),
                            else_=motivo))
            )
    return sum(len(ids) for ids in por_motivo.values())


def marcar_transferencia(engine, ids: list[int], usuario: str,
                         subcategoria: str = "Entre Contas Próprias") -> int:
    """Poe as linhas em Transferencias entre Contas: nem gasto nem ganho."""
    if not ids:
        return 0
    with engine.begin() as conn:
        categoria_id = conn.execute(
            sa.select(db.categorias.c.id)
            .where(db.categorias.c.nome == analytics.CATEGORIA_TRANSFERENCIA)
        ).scalar()
        if categoria_id is None:
            return 0
        subcategoria_id = conn.execute(
            sa.select(db.subcategorias.c.id).where(
                db.subcategorias.c.categoria_id == categoria_id,
                db.subcategorias.c.nome == subcategoria,
            )
        ).scalar()
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id.in_(ids))
            .values(categoria_id=categoria_id, subcategoria_id=subcategoria_id,
                    status="manual", confianca=1.0, classificado_por=usuario)
        )
    return len(ids)


def desativar_transacoes(engine, ids: list[int], motivo: str) -> int:
    """Tira as linhas dos relatorios sem apaga-las: da para voltar atras."""
    if not ids:
        return 0
    with engine.begin() as conn:
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id.in_(ids))
            .values(ativo=False, observacao=motivo)
        )
    return len(ids)


def reativar_transacao(engine, transacao_id: int) -> bool:
    """Devolve ao mes um lancamento que um upload tinha desligado.

    O pareamento "esta previsao acabou de chegar no extrato" casa por mes e por
    ordem de grandeza, nao por descricao — e um arquivo lido com o sinal
    trocado pode fazer uma compra de cartao "realizar" a receita prevista do
    mes. Quando isso acontece, a previsao fica riscada por engano e a renda do
    mes some sem que nada a tenha apagado. Aqui ela volta.
    """
    with engine.begin() as conn:
        resultado = conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id == transacao_id)
            .values(ativo=True, substituido_por=None, observacao=None)
        )
    return resultado.rowcount > 0


def excluir_transacao(engine, transacao_id: int) -> bool:
    """Apaga um lancamento avulso.

    Existe para o caso em que duas fontes descrevem o mesmo dinheiro com
    valores diferentes — a anotacao da casa ficou defasada em relacao ao
    contracheque, por exemplo. Nenhuma regra automatica deveria escolher entre
    as duas; quem decide e quem conhece o caso.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.delete(db.duplicidades).where(
                sa.or_(
                    db.duplicidades.c.transacao_nova_id == transacao_id,
                    db.duplicidades.c.transacao_existente_id == transacao_id,
                )
            )
        )
        resultado = conn.execute(
            sa.delete(db.transacoes).where(db.transacoes.c.id == transacao_id)
        )
    return resultado.rowcount > 0


def fila_pendentes(
    conn, limite: int = 200, competencia: str | None = None, termo: str = "",
) -> list[dict]:
    """A fila de classificação, opcionalmente de um mês só ou filtrada por texto.

    O André classifica mês a mês, e o gasto esporádico com os filhos está
    espalhado entre centenas de linhas. Sem cortar por mês, achar as três de
    agosto significa rolar a lista inteira.
    """
    condicoes = [
        db.transacoes.c.status == "pendente",
        db.transacoes.c.ativo == sa.true(),
    ]
    if competencia:
        condicoes.append(db.transacoes.c.competencia == competencia)
    if termo.strip():
        alvo = f"%{termo.strip()}%"
        condicoes.append(
            sa.or_(
                db.transacoes.c.descricao.ilike(alvo),
                db.transacoes.c.classificacao_origem.ilike(alvo),
            )
        )
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.data,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.confianca,
            db.transacoes.c.observacao,
            db.transacoes.c.classificacao_origem,
            db.contas.c.nome.label("conta"),
            # e o tipo da conta que decide quais categorias a tela oferece:
            # linha de cartao so vai para despesa
            db.contas.c.tipo.label("tipo_conta"),
        )
        .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
        .where(*condicoes)
        .order_by(db.transacoes.c.data.desc())
        .limit(limite)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def contar_pendentes(conn) -> int:
    """Quantos estao na fila — o numero do cracha, sem trazer as linhas.

    A barra lateral so quer um inteiro, e todo rerun de toda tela passa por
    aqui. Trazer a fila inteira para contar em Python custava 25 KB de rede por
    clique, e o teto de 500 fazia o cracha mentir a partir dali: a carga
    inicial deixou 441 pendentes, entao o numero estava a poucos lancamentos
    de congelar em "500" para sempre.
    """
    return conn.execute(
        sa.select(sa.func.count())
        .select_from(db.transacoes)
        .where(db.transacoes.c.status == "pendente", db.transacoes.c.ativo == sa.true())
    ).scalar_one()


def pendentes_por_competencia(conn) -> dict[str, int]:
    """Quantos pendentes em cada mês — para a tela dizer onde está o trabalho."""
    consulta = (
        sa.select(db.transacoes.c.competencia, sa.func.count().label("n"))
        .where(
            db.transacoes.c.status == "pendente",
            db.transacoes.c.ativo == sa.true(),
        )
        .group_by(db.transacoes.c.competencia)
        .order_by(db.transacoes.c.competencia.desc())
    )
    return {linha.competencia: int(linha.n) for linha in conn.execute(consulta)}


def buscar_transacoes(conn, termo: str = "", limite: int = 100) -> list[dict]:
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.data,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.status,
            db.transacoes.c.categoria_id,
            db.transacoes.c.subcategoria_id,
            db.transacoes.c.classificacao_origem,
            db.categorias.c.nome.label("categoria"),
            db.subcategorias.c.nome.label("subcategoria"),
            db.contas.c.nome.label("conta"),
            db.contas.c.tipo.label("tipo_conta"),
        )
        .select_from(
            db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(db.transacoes.c.ativo == sa.true())
        .order_by(db.transacoes.c.data.desc())
        .limit(limite)
    )
    if termo:
        centavos = _termo_em_centavos(termo)
        if centavos is not None:
            # "3351,65" ou "3.351,65": toda linha com esse valor, de qualquer
            # origem — e o jeito de saber se a planilha tem o mesmo gasto que
            # a fatura, quando a descricao digitada nao lembra a do banco
            consulta = consulta.where(sa.func.abs(db.transacoes.c.valor_centavos) == centavos)
        else:
            alvo = f"%{normalizar(termo)}%"
            consulta = consulta.where(db.transacoes.c.descricao_norm.like(alvo))
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def _termo_em_centavos(termo: str) -> int | None:
    """"3.351,65" -> 335165; "3351,65" -> 335165; "1500" -> 150000; texto -> None."""
    limpo = termo.strip().replace("R$", "").replace(" ", "")
    if not re.fullmatch(r"-?\d{1,3}(\.\d{3})*(,\d{1,2})?|-?\d+(,\d{1,2})?", limpo):
        return None
    limpo = limpo.replace(".", "").replace(",", ".").lstrip("-")
    return round(float(limpo) * 100)


# --------------------------------------------------------------------------
# contas e plano de contas
# --------------------------------------------------------------------------
def salvar_conta(engine, *, conta_id=None, nome, tipo, titular, instituicao, parser,
                 ativa=True, identificador=None):
    with engine.begin() as conn:
        valores = dict(
            nome=nome.strip(), tipo=tipo, titular=titular,
            instituicao=instituicao.strip(), parser=parser, ativa=ativa,
            identificador=(identificador or "").strip() or None,
        )
        if conta_id:
            conn.execute(sa.update(db.contas).where(db.contas.c.id == conta_id).values(**valores))
            return conta_id
        return conn.execute(sa.insert(db.contas).values(**valores)).inserted_primary_key[0]


def identificar_conta(engine, conta_id: int, identificador: str | None) -> None:
    """Grava a agência/conta como o extrato imprime, para a tela conferir."""
    with engine.begin() as conn:
        conn.execute(
            sa.update(db.contas).where(db.contas.c.id == conta_id)
            .values(identificador=(identificador or "").strip() or None)
        )


def conta_pelo_identificador(conn, ident: dict | None) -> dict | None:
    """A conta cadastrada de que este extrato é, se alguma bater."""
    from parsers.extrato_itau import conta_bate

    if not ident:
        return None
    for conta in listar_contas(conn, so_ativas=False):
        if conta_bate(ident, conta.get("identificador")):
            return conta
    return None


def alternar_conta(engine, conta_id: int, ativa: bool) -> None:
    with engine.begin() as conn:
        conn.execute(sa.update(db.contas).where(db.contas.c.id == conta_id).values(ativa=ativa))


def salvar_categoria(engine, *, categoria_id=None, nome, natureza, ativa=True):
    with engine.begin() as conn:
        if categoria_id:
            conn.execute(
                sa.update(db.categorias)
                .where(db.categorias.c.id == categoria_id)
                .values(nome=nome.strip(), ativa=ativa)
            )
            return categoria_id
        ordem = (conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(db.categorias.c.ordem), 0)).where(
                db.categorias.c.natureza == natureza
            )
        ).scalar() or 0) + 1
        return conn.execute(
            sa.insert(db.categorias).values(
                nome=nome.strip(), natureza=natureza, ordem=ordem, ativa=ativa
            )
        ).inserted_primary_key[0]


def metas_pela_media(conn, ano: int, renda_base: int) -> dict[int, float]:
    """Percentual que cada categoria vem gastando de fato, como ponto de partida.

    A pergunta do André: por que digitar percentual no chute se a casa já
    mostrou, mês a mês, quanto cada conta come? O histórico não é a meta — é o
    retrato de onde se está, e é dele que se decide o que mudar. Um orçamento
    que começa em zero e pede treze palpites não é preenchido.
    """
    if not renda_base:
        return {}
    meses = analytics.meses_decorridos(ano)
    gastos = analytics.por_categoria(conn, ano=ano)
    return {
        linha["categoria_id"]: round(linha["total"] / meses / renda_base * 100, 1)
        for linha in gastos
        if linha["total"]
    }


def uso_da_categoria(conn, categoria_id: int) -> dict:
    """O que depende desta categoria, antes de deixar alguém apagá-la.

    Apagar categoria em uso deixaria lançamentos órfãos e o total do mês
    mudaria sem ninguém pedir. Melhor dizer quantos são e oferecer desativar,
    que tira do caminho sem tocar em nada.
    """
    def quantos(tabela, coluna):
        return int(conn.execute(
            sa.select(sa.func.count()).select_from(tabela).where(coluna == categoria_id)
        ).scalar() or 0)

    lancamentos = quantos(db.transacoes, db.transacoes.c.categoria_id)
    regras_ = quantos(db.regras, db.regras.c.categoria_id)
    traducoes = quantos(db.de_para, db.de_para.c.categoria_id)
    return {
        "lancamentos": lancamentos,
        "regras": regras_,
        "traducoes": traducoes,
        "subcategorias": quantos(db.subcategorias, db.subcategorias.c.categoria_id),
        "pode_apagar": not (lancamentos or regras_ or traducoes),
    }


def usos_das_categorias(conn) -> dict[int, dict]:
    """O mesmo de uso_da_categoria, para todas de uma vez.

    A tela do plano de contas precisa disto para cada bloco na tela. Uma
    chamada por categoria eram quatro contagens vezes dezoito categorias:
    setenta e duas idas ao banco para desenhar uma tela que nao mudou nada.
    Aqui sao quatro, agrupadas por categoria_id.

    Categoria que nao aparece em nenhuma contagem volta com zero — some do
    GROUP BY, nao do resultado.
    """
    def por_categoria(tabela, coluna) -> dict[int, int]:
        return {
            linha[0]: int(linha[1])
            for linha in conn.execute(
                sa.select(coluna, sa.func.count()).select_from(tabela).group_by(coluna)
            )
            if linha[0] is not None
        }

    lancamentos = por_categoria(db.transacoes, db.transacoes.c.categoria_id)
    regras_ = por_categoria(db.regras, db.regras.c.categoria_id)
    traducoes = por_categoria(db.de_para, db.de_para.c.categoria_id)
    subs = por_categoria(db.subcategorias, db.subcategorias.c.categoria_id)

    ids = conn.execute(sa.select(db.categorias.c.id)).scalars().all()
    saida = {}
    for categoria_id in ids:
        usados = (
            lancamentos.get(categoria_id, 0),
            regras_.get(categoria_id, 0),
            traducoes.get(categoria_id, 0),
        )
        saida[categoria_id] = {
            "lancamentos": usados[0],
            "regras": usados[1],
            "traducoes": usados[2],
            # subcategoria de proposito fora da conta: apagar a categoria apaga
            # as subcategorias dela junto, e sempre foi assim
            "subcategorias": subs.get(categoria_id, 0),
            "pode_apagar": not any(usados),
        }
    return saida


def excluir_categoria(engine, categoria_id: int) -> bool:
    """Apaga a categoria e suas subcategorias. Recusa se algo depender dela.

    A recusa não é burocracia: sem ela, um clique apagaria a gaveta de dezenas
    de lançamentos e o relatório mudaria sozinho. Categoria com uso se desativa,
    não se apaga.
    """
    with engine.begin() as conn:
        if not uso_da_categoria(conn, categoria_id)["pode_apagar"]:
            return False
        conn.execute(
            sa.delete(db.subcategorias).where(db.subcategorias.c.categoria_id == categoria_id)
        )
        conn.execute(sa.delete(db.metas).where(db.metas.c.categoria_id == categoria_id))
        conn.execute(sa.delete(db.categorias).where(db.categorias.c.id == categoria_id))
    return True


def excluir_subcategoria(engine, subcategoria_id: int) -> bool:
    """Mesma regra, um nível abaixo: recusa se houver lançamento nela."""
    with engine.begin() as conn:
        em_uso = conn.execute(
            sa.select(sa.func.count()).select_from(db.transacoes)
            .where(db.transacoes.c.subcategoria_id == subcategoria_id)
        ).scalar()
        if em_uso:
            return False
        for tabela, coluna in ((db.regras, db.regras.c.subcategoria_id),
                               (db.de_para, db.de_para.c.subcategoria_id)):
            conn.execute(sa.update(tabela).where(coluna == subcategoria_id)
                         .values(subcategoria_id=None))
        conn.execute(sa.delete(db.subcategorias).where(db.subcategorias.c.id == subcategoria_id))
    return True


def cobertura(conn, competencias: list[str]) -> dict[tuple[int, str], dict]:
    """Quantos lançamentos cada conta tem em cada competência.

    É o que alimenta o mapa de carregamento: responde, para cada conta e cada
    mês, se o extrato já entrou. Conta lançamentos inativos também — se o mês
    foi importado e caiu tudo em duplicidade, ele já foi carregado.

    Ignora o que veio da planilha. A planilha é importada dentro de alguma
    conta, e sem esse filtro os lançamentos dela apareceriam como se o extrato
    daquela conta tivesse sido carregado — bem no painel que existe justamente
    para dizer o contrário.
    """
    if not competencias:
        return {}
    consulta = (
        sa.select(
            db.transacoes.c.conta_id,
            db.transacoes.c.competencia,
            sa.func.count(db.transacoes.c.id).label("total"),
            sa.func.sum(
                sa.case((db.transacoes.c.ativo == sa.true(), 1), else_=0)
            ).label("ativos"),
        )
        .where(
            db.transacoes.c.competencia.in_(competencias),
            db.transacoes.c.origem != "planilha",
        )
        .group_by(db.transacoes.c.conta_id, db.transacoes.c.competencia)
    )
    return {
        (linha.conta_id, linha.competencia): {
            "total": int(linha.total or 0),
            "ativos": int(linha.ativos or 0),
        }
        for linha in conn.execute(consulta)
    }


def lancamentos_da_conta_no_mes(conn, conta_id: int, competencia: str) -> list[dict]:
    """O que exatamente esta numa conta num mes, com o arquivo que trouxe.

    E a resposta para "o mapa diz que setembro foi carregado, e nao foi": em vez
    de discutir com o mapa, olha-se as linhas — e o arquivo de onde vieram.
    """
    consulta = (
        sa.select(
            db.transacoes.c.id, db.transacoes.c.data, db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos, db.transacoes.c.ativo, db.transacoes.c.origem,
            db.uploads.c.arquivo,
        )
        .select_from(db.transacoes.outerjoin(db.uploads, db.transacoes.c.upload_id == db.uploads.c.id))
        .where(db.transacoes.c.conta_id == conta_id, db.transacoes.c.competencia == competencia)
        .order_by(db.transacoes.c.data, db.transacoes.c.id)
    )
    return [dict(l._mapping) for l in conn.execute(consulta)]


def cobertura_planilha(conn, competencias: list[str]) -> dict[str, dict]:
    """O mesmo mapa, para a planilha de carga inicial.

    Não separa por conta: a planilha cobre um mês inteiro, de todas as contas.
    Conta os lançamentos independentemente de estarem ativos, porque a linha da
    planilha é desativada quando o extrato do mesmo período a substitui — e
    isso não significa que aquele mês deixou de ter sido carregado.
    """
    if not competencias:
        return {}
    consulta = (
        sa.select(
            db.transacoes.c.competencia,
            sa.func.count(db.transacoes.c.id).label("total"),
            sa.func.sum(
                sa.case((db.transacoes.c.ativo == sa.true(), 1), else_=0)
            ).label("ativos"),
        )
        .where(
            db.transacoes.c.competencia.in_(competencias),
            db.transacoes.c.origem == "planilha",
        )
        .group_by(db.transacoes.c.competencia)
    )
    return {
        linha.competencia: {"total": int(linha.total or 0), "ativos": int(linha.ativos or 0)}
        for linha in conn.execute(consulta)
    }


def salvar_subcategoria(engine, *, categoria_id: int, nome: str):
    with engine.begin() as conn:
        ordem = (conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(db.subcategorias.c.ordem), 0)).where(
                db.subcategorias.c.categoria_id == categoria_id
            )
        ).scalar() or 0) + 1
        return conn.execute(
            sa.insert(db.subcategorias).values(
                categoria_id=categoria_id, nome=nome.strip(), ordem=ordem
            )
        ).inserted_primary_key[0]


def salvar_metas(engine, ano: int, percentuais: dict[int, float]) -> None:
    """Grava as metas do ano de uma vez.

    Era um SELECT e um UPDATE ou INSERT por categoria: com treze categorias de
    despesa, vinte e seis idas ao banco num clique de "salvar" — perto de
    quatro segundos de espera para gravar treze numeros.
    """
    if not percentuais:
        return
    with engine.begin() as conn:
        existentes = {
            linha.categoria_id: linha.id
            for linha in conn.execute(
                sa.select(db.metas.c.id, db.metas.c.categoria_id).where(
                    db.metas.c.ano == ano,
                    db.metas.c.categoria_id.in_(list(percentuais)),
                )
            )
        }
        novas = [
            {"ano": ano, "categoria_id": categoria_id, "percentual": pct}
            for categoria_id, pct in percentuais.items()
            if categoria_id not in existentes
        ]
        if novas:
            conn.execute(sa.insert(db.metas), novas)

        # um UPDATE por percentual distinto, e nao por categoria: quem preenche
        # um orcamento repete o mesmo numero em varias linhas
        por_percentual: dict[float, list[int]] = {}
        for categoria_id, pct in percentuais.items():
            if categoria_id in existentes:
                por_percentual.setdefault(pct, []).append(existentes[categoria_id])
        for pct, ids in por_percentual.items():
            conn.execute(
                sa.update(db.metas).where(db.metas.c.id.in_(ids)).values(percentual=pct)
            )


def listar_metas(conn, ano: int) -> dict[int, float]:
    return {
        linha.categoria_id: linha.percentual
        for linha in conn.execute(
            sa.select(db.metas.c.categoria_id, db.metas.c.percentual).where(db.metas.c.ano == ano)
        )
    }


def listar_uploads(conn, limite: int = 20) -> list[dict]:
    consulta = (
        sa.select(
            db.uploads.c.id,
            db.uploads.c.arquivo,
            db.uploads.c.competencia,
            db.uploads.c.origem,
            db.uploads.c.enviado_por,
            db.uploads.c.lidos,
            db.uploads.c.importados,
            db.uploads.c.auto,
            db.uploads.c.pendentes,
            db.uploads.c.duplicados,
            db.uploads.c.criado_em,
            db.contas.c.nome.label("conta"),
        )
        .select_from(db.uploads.outerjoin(db.contas, db.uploads.c.conta_id == db.contas.c.id))
        .order_by(db.uploads.c.id.desc())
        .limit(limite)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def competencias_disponiveis(conn) -> list[str]:
    return [
        linha.competencia
        for linha in conn.execute(
            sa.select(db.transacoes.c.competencia)
            .where(db.transacoes.c.ativo == sa.true())
            .distinct()
            .order_by(db.transacoes.c.competencia.desc())
        )
    ]


def _pode_voltar(conn, desligada, upload_id: int) -> bool:
    """Devolver esta linha ao mes recriaria a duplicidade que ela evitava?

    Desfazer um upload religa o que aquele upload desligou. Mas entre desligar
    e desfazer pode ter passado outro arquivo trazendo o mesmo dinheiro — e o
    segundo arquivo nao viu a previsao, porque o indice de duplicidade so le
    linhas ativas. Religar por cima soma as duas.

    Foi exatamente essa a sequencia: a fatura lida com o sinal trocado aposentou
    a receita prevista de setembro; o extrato do salario chegou depois e entrou
    como nova, sem parear e sem nem virar pergunta; desfazer a fatura devolveu a
    previsao para o lado do salario. O upload errado saiu e a renda continuou
    dobrada, sem nada na tela explicando.
    """
    if desligada.origem != "manual" or desligada.valor_centavos <= 0:
        return True
    equivalente = conn.execute(
        sa.select(sa.func.count())
        .select_from(db.transacoes)
        .where(
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.origem != "manual",
            db.transacoes.c.valor_centavos > 0,
            db.transacoes.c.competencia == desligada.competencia,
            db.transacoes.c.id != desligada.id,
            # o que este upload trouxe esta prestes a ser apagado: contá-lo
            # aqui faria o desfazer normal reter a previsao que ele mesmo veio
            # devolver — o caso comum, e justamente o que tem de funcionar
            sa.or_(
                db.transacoes.c.upload_id.is_(None),
                db.transacoes.c.upload_id != upload_id,
            ),
            sa.func.abs(db.transacoes.c.valor_centavos - desligada.valor_centavos)
            <= dedup.FOLGA_DA_PREVISAO * desligada.valor_centavos,
        )
    ).scalar()
    return not equivalente


def endireitar_upload(engine, upload_id: int) -> int:
    """Corrige o sinal de uma fatura que foi gravada invertida. Idempotente.

    A primeira versao disto era "inverter": virava tudo, e virar de novo
    desfazia. Um botao que alterna um estado destrutivo e uma armadilha — dois
    cliques, e a fatura que tinha acabado de ser consertada voltou ao erro, ao
    centavo. Foi exatamente o que aconteceu.

    Aqui a pergunta e a mesma do gravador: este lote, numa conta de cartao,
    esta quase todo positivo? So entao vira. Ja esta certo, ou nao e cartao, ou
    e pequeno demais para decidir: nao faz nada. Clicar cem vezes da no mesmo
    que clicar uma, e rodar na inicializacao e seguro.
    """
    with engine.begin() as conn:
        linhas = conn.execute(
            sa.select(
                db.transacoes.c.id, db.transacoes.c.conta_id, db.transacoes.c.data,
                db.transacoes.c.valor_centavos, db.transacoes.c.descricao_norm,
                db.contas.c.tipo,
            )
            .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
            .where(db.transacoes.c.upload_id == upload_id)
        ).all()
        if not linhas or linhas[0].tipo != "cartao" or not fatura_invertida(linhas):
            return 0
        for linha in linhas:
            valor = -linha.valor_centavos
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id == linha.id)
                .values(
                    valor_centavos=valor,
                    # o valor faz parte do hash de duplicidade; sem acompanhar,
                    # a proxima importacao nao reconheceria estas linhas
                    hash_dedup=dedup.hash_lancamento(
                        linha.conta_id, linha.data, valor, linha.descricao_norm
                    ),
                )
            )
    return len(linhas)


def desclassificar_receita_em_cartao(engine) -> int:
    """Tudo o que a sentinela apontaria, consertado na subida. Idempotente.

    Usa a MESMA lista da sentinela, linha a linha — nao uma pergunta parecida.
    Para cada uma: a natureza vira "despesa", que e o que a trava do gravador
    teria feito se existisse quando a linha entrou. E se ela esta numa
    categoria de receita, perde a categoria e volta para a fila com a
    explicacao, onde sera classificada como qualquer outra pendencia.

    Depois disto a sentinela e vazia por construcao; se nao for, o bug esta
    aqui, e nao em quem classificou.
    """
    with engine.begin() as conn:
        ids = analytics.ids_receita_em_cartao(conn)
        if not ids:
            return 0
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id.in_(ids))
            .values(natureza="despesa")
        )
        em_categoria_de_receita = [
            linha.id for linha in conn.execute(
                sa.select(db.transacoes.c.id)
                .select_from(db.transacoes.join(
                    db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id))
                .where(db.transacoes.c.id.in_(ids), db.categorias.c.natureza == "receita")
            )
        ]
        if em_categoria_de_receita:
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(em_categoria_de_receita))
                .values(
                    categoria_id=None, subcategoria_id=None, status="pendente",
                    confianca=None, classificado_por=None,
                    observacao="estava numa categoria de receita; cartão não gera receita",
                )
            )
    return len(ids)


def endireitar_faturas_gravadas(engine) -> list[dict]:
    """Passa por toda fatura ja gravada e corrige a que estiver invertida.

    Roda na inicializacao. E o que faz a correcao nao depender de ninguem
    clicar em nada: a fatura que entrou errada antes da trava existir, ou que
    voltou ao erro por um clique a mais, e endireitada na proxima subida do
    app. Como `endireitar_upload` so age no que esta invertido, rodar a cada
    inicializacao nao custa nada quando esta tudo certo.
    """
    from parsers.base import MINIMO_PARA_DECIDIR, PROPORCAO_DE_GASTO

    # uma consulta decide quais faturas estao invertidas; so essas sao
    # reabertas. Antes era uma leitura por fatura em todo start
    # linha de valor zero nao entra na proporcao, como em fatura_invertida
    positivos = sa.func.sum(sa.case((db.transacoes.c.valor_centavos > 0, 1), else_=0))
    com_valor = sa.func.sum(sa.case((db.transacoes.c.valor_centavos != 0, 1), else_=0))
    with engine.connect() as conn:
        candidatos = conn.execute(
            sa.select(db.uploads.c.id, db.uploads.c.arquivo,
                      positivos.label("positivos"), com_valor.label("total"))
            .select_from(
                db.uploads
                .join(db.contas, db.uploads.c.conta_id == db.contas.c.id)
                .join(db.transacoes, db.transacoes.c.upload_id == db.uploads.c.id)
            )
            .where(db.contas.c.tipo == "cartao")
            .group_by(db.uploads.c.id, db.uploads.c.arquivo)
            .order_by(db.uploads.c.id)
        ).all()
    corrigidos = []
    for upload in candidatos:
        if not upload.total or upload.total < MINIMO_PARA_DECIDIR:
            continue
        if upload.positivos / upload.total < PROPORCAO_DE_GASTO:
            continue
        linhas = endireitar_upload(engine, upload.id)
        if linhas:
            corrigidos.append({"upload_id": upload.id, "arquivo": upload.arquivo, "linhas": linhas})
    return corrigidos


def apagar_upload(engine, upload_id: int) -> tuple[int, int, int]:
    """Desfaz uma importacao inteira - o 'undo' de um arquivo errado.

    Apagar o que entrou nao basta: o upload tambem *desliga* linhas antigas —
    a da planilha que ele conferiu, a receita prevista a mao que ele veio
    realizar. Desfazendo so um lado, essas linhas ficavam desligadas para
    sempre e o mes perdia dinheiro que ninguem apagou.

    Mas religar tudo as cegas tem o defeito oposto: se o dinheiro ja voltou por
    outro arquivo, a linha devolvida passa a contar duas vezes. Por isso cada
    uma passa por `_pode_voltar`. Devolve (apagadas, devolvidas, retidas).
    """
    with engine.begin() as conn:
        desligadas = conn.execute(
            sa.select(
                db.transacoes.c.id, db.transacoes.c.origem,
                db.transacoes.c.competencia, db.transacoes.c.valor_centavos,
            ).where(db.transacoes.c.substituido_por == upload_id)
        ).all()
        voltam = [linha.id for linha in desligadas if _pode_voltar(conn, linha, upload_id)]
        retidas = len(desligadas) - len(voltam)
        devolvidas = 0
        if voltam:
            devolvidas = conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(voltam))
                .values(ativo=True, substituido_por=None, observacao=None)
            ).rowcount or 0
        if retidas:
            # sai de baixo deste upload, mas continua desligada: o dinheiro dela
            # ja esta no mes por outro arquivo, e a tela precisa dizer isso
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.substituido_por == upload_id)
                .values(
                    substituido_por=None,
                    observacao="o dinheiro previsto aqui já entrou por um extrato",
                )
            )
        ids = [
            linha.id
            for linha in conn.execute(
                sa.select(db.transacoes.c.id).where(db.transacoes.c.upload_id == upload_id)
            )
        ]
        if ids:
            conn.execute(
                sa.delete(db.duplicidades).where(
                    sa.or_(
                        db.duplicidades.c.transacao_nova_id.in_(ids),
                        db.duplicidades.c.transacao_existente_id.in_(ids),
                    )
                )
            )
            conn.execute(sa.delete(db.transacoes).where(db.transacoes.c.id.in_(ids)))
        conn.execute(sa.delete(db.uploads).where(db.uploads.c.id == upload_id))
    return len(ids), devolvidas, retidas


# --------------------------------------------------------------------------
# analise por IA: o texto do mes e a subcategoria que faltou
# --------------------------------------------------------------------------
def _hash_do_contexto(contexto: str) -> str:
    import hashlib

    return hashlib.sha256(contexto.encode("utf-8")).hexdigest()


def salvar_analise(
    engine, *, competencia: str, texto: str, modelo: str, contexto: str,
    usuario: str, pergunta: str | None = None, tipo: str = "mes",
) -> int:
    """Guarda a analise no banco, e nao so na sessao.

    Duas razoes. A primeira: o Andre gera, a Ro abre depois e ve o mesmo texto,
    em vez de gerar de novo. A segunda: a analise custa uma chamada paga por
    vez, e reboot do Streamlit apaga a sessao inteira — sem gravar, o mesmo mes
    seria pago varias vezes por dia.
    """
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.analises).values(
                competencia=competencia,
                texto=texto,
                modelo=modelo,
                contexto_hash=_hash_do_contexto(contexto),
                pergunta=pergunta,
                tipo=tipo,
                gerada_por=usuario,
            )
        ).inserted_primary_key[0]


def ultima_analise(
    conn, competencia: str, contexto: str | None = None, tipo: str = "mes",
) -> dict | None:
    """A analise mais recente do periodo, com aviso quando os numeros mudaram."""
    linha = conn.execute(
        sa.select(db.analises)
        .where(
            db.analises.c.competencia == competencia,
            db.analises.c.pergunta.is_(None),
            # base antiga nao tem a coluna preenchida: o que veio antes do ano
            # existir e analise de mes
            sa.or_(db.analises.c.tipo == tipo, db.analises.c.tipo.is_(None))
            if tipo == "mes" else db.analises.c.tipo == tipo,
        )
        .order_by(db.analises.c.id.desc())
        .limit(1)
    ).fetchone()
    if linha is None:
        return None
    registro = dict(linha._mapping)
    registro["desatualizada"] = bool(
        contexto is not None
        and registro.get("contexto_hash")
        and registro["contexto_hash"] != _hash_do_contexto(contexto)
    )
    return registro


def perguntas_anteriores(conn, competencia: str, limite: int = 10) -> list[dict]:
    consulta = (
        sa.select(db.analises)
        .where(db.analises.c.competencia == competencia, db.analises.c.pergunta.isnot(None))
        .order_by(db.analises.c.id.desc())
        .limit(limite)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def sem_subcategoria(
    conn, competencia: str | None = None, limite: int = 200,
    categoria_id: int | None = None,
) -> list[dict]:
    """Classificado ate a categoria, faltando a subcategoria.

    E o que sobra quando alguem classifica em volume: a categoria resolve o
    relatorio (e ela que soma), e a subcategoria fica para depois. Este e o
    "depois".
    """
    condicoes = [
        db.transacoes.c.ativo == sa.true(),
        db.transacoes.c.categoria_id.isnot(None),
        db.transacoes.c.subcategoria_id.is_(None),
    ]
    if categoria_id:
        condicoes.append(db.transacoes.c.categoria_id == categoria_id)
    if competencia:
        condicoes.append(db.transacoes.c.competencia == competencia)
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.data,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.categoria_id,
            db.categorias.c.nome.label("categoria"),
            # a natureza vem da categoria, nao do sinal: um estorno dentro de
            # uma despesa entra com valor positivo e continua sendo despesa.
            # Pelo sinal, a tela ofereceria a lista de receitas para trocar o
            # grupo dele.
            db.categorias.c.natureza,
        )
        .select_from(
            db.transacoes.join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*condicoes)
        .order_by(db.transacoes.c.valor_centavos)
        .limit(limite)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def sem_subcategoria_por_mes(conn) -> dict[str, int]:
    """Quantos faltam detalhar em cada mes, do mais recente para o mais antigo.

    E o que a tela precisa para oferecer o mes certo: achar um lancamento sem
    subcategoria num relatorio e ter de sair procurando em que mes ele estava e
    dificuldade a mais para um trabalho que ja e chato.
    """
    consulta = (
        sa.select(db.transacoes.c.competencia, sa.func.count().label("quantos"))
        .where(
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.categoria_id.isnot(None),
            db.transacoes.c.subcategoria_id.is_(None),
        )
        .group_by(db.transacoes.c.competencia)
        .order_by(db.transacoes.c.competencia.desc())
    )
    return {linha.competencia: linha.quantos for linha in conn.execute(consulta)}


def sugerir_subcategorias(conn, competencia: str | None = None, limite: int = 60) -> list[dict]:
    """Pergunta a IA so a subcategoria, com a categoria ja escolhida por gente.

    Nada e gravado aqui: devolve as sugestoes para a tela mostrar lado a lado
    com o lancamento. Aplicar e um segundo passo, com o dedo de quem confere.
    """
    itens = sem_subcategoria(conn, competencia=competencia, limite=limite)
    if not itens:
        return []

    opcoes = {
        cat["id"]: [sub["nome"] for sub in cat["subcategorias"] if sub["ativa"]]
        for cat in plano_de_contas(conn)
    }

    entrada = []
    posicoes = {}
    for i, item in enumerate(itens):
        possiveis = opcoes.get(item["categoria_id"]) or []
        if not possiveis:
            continue                       # categoria sem subcategoria cadastrada
        posicoes[i] = item
        entrada.append((i, item["descricao"], item["valor_centavos"], item["categoria"], possiveis))
    if not entrada:
        return []

    validas = {
        (cat_id, nome.casefold()): nome
        for cat_id, nomes in opcoes.items()
        for nome in nomes
    }

    # a IA pode inventar um nome parecido; só entra o que existe mesmo dentro
    # daquela categoria
    por_indice: dict[int, tuple[str, float]] = {}
    for sugestao in ai.sugerir_subcategorias(entrada):
        item = posicoes.get(sugestao.indice)
        if item is None or not sugestao.subcategoria:
            continue
        nome = validas.get((item["categoria_id"], sugestao.subcategoria.casefold()))
        if nome:
            por_indice[sugestao.indice] = (nome, sugestao.confianca)

    # Devolve TODOS os candidatos, com e sem sugestão. O que a IA não soube
    # dizer é justamente o que precisa de gente — e some da tela era o pior
    # lugar para ele ir parar: viraria caça ao lançamento numa outra tela,
    # depois. Aqui ele aparece com a lista de subcategorias ao lado, pronto
    # para ser escolhido à mão no mesmo gesto.
    saida = []
    for i, item in posicoes.items():
        nome, confianca = por_indice.get(i, (None, 0.0))
        saida.append({
            **item,
            "subcategoria": nome,
            "confianca": confianca,
            "opcoes": opcoes.get(item["categoria_id"]) or [],
        })
    saida.sort(key=lambda linha: (linha["subcategoria"] is not None, linha["valor_centavos"]))
    return saida


def aplicar_destinos(
    engine, escolhas: dict[int, tuple[int, int | None]], usuario: str
) -> dict[str, int]:
    """Grava o destino conferido de cada lancamento da revisao de subcategorias.

    A escolha e o par (categoria, subcategoria) porque o grupo tambem sai
    errado: o lancamento foi classificado em volume, caiu em Casa e era Lazer.
    Obrigar a corrigir isso noutra tela seria caçar o lançamento de novo,
    depois — e o "depois" e justamente o que esta tela existe para evitar.

    As duas correcoes nao tem o mesmo peso, e por isso nao terminam igual:

    - so preencher a subcategoria e aceitar uma sugestao dentro da categoria
      que gente ja escolheu; nao vira memoria, senao a IA estaria ensinando o
      sistema com o proprio palpite.
    - trocar a categoria e correcao de gente contra o que estava gravado. Vale
      como memoria, do mesmo jeito que a tela de classificacao: a proxima
      fatura ja reconhece o estabelecimento no grupo certo.

    Devolve quantos foram gravados, quantos mudaram de categoria e quantos
    viraram memoria — a tela conta isso de volta para quem conferiu.
    """
    if not escolhas:
        return {"gravadas": 0, "categorias": 0, "memorias": 0}
    contas = {"gravadas": 0, "categorias": 0, "memorias": 0}
    with engine.begin() as conn:
        atuais = {
            linha.id: linha
            for linha in conn.execute(
                sa.select(
                    db.transacoes.c.id,
                    db.transacoes.c.categoria_id,
                    db.transacoes.c.descricao,
                    db.transacoes.c.pessoa,
                ).where(
                    db.transacoes.c.id.in_(list(escolhas)),
                    db.transacoes.c.subcategoria_id.is_(None),
                )
            )
        }
        for transacao_id, destino in escolhas.items():
            atual = atuais.get(transacao_id)
            if atual is None:
                continue
            categoria_id, subcategoria_id = destino
            if not categoria_id:
                continue
            trocou = categoria_id != atual.categoria_id
            if not trocou and subcategoria_id is None:
                continue                      # nao mudou nada: nao gasta escrita
            valores = dict(
                categoria_id=categoria_id,
                subcategoria_id=subcategoria_id,
                classificado_por=usuario,
                observacao=(
                    "categoria corrigida na revisão de subcategorias" if trocou
                    else "subcategoria sugerida pela IA e conferida"
                ),
            )
            if trocou:
                valores["status"] = "manual"
                valores["confianca"] = 1.0
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id == transacao_id)
                .values(**valores)
            )
            contas["gravadas"] += 1
            if trocou:
                contas["categorias"] += 1
                if classify.aprender(
                    conn, atual.descricao, categoria_id, subcategoria_id,
                    usuario, atual.pessoa,
                ):
                    contas["memorias"] += 1
    return contas
