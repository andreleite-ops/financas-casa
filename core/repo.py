"""Pipeline de importacao e consultas usadas pelas telas."""

from __future__ import annotations

import calendar
from datetime import date

import sqlalchemy as sa

from . import ai, analytics, classify, db, dedup
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
        pendentes_pos: list[int] = []    # posições que ficaram sem categoria
        proximo_temp = -1

        for lan in lancamentos:
            descricao_norm = normalizar(lan.descricao)
            decisao = indice.avaliar(
                conta_id=conta_id,
                dia=lan.data,
                valor_centavos=lan.valor_centavos,
                descricao=lan.descricao,
                descricao_norm=descricao_norm,
                origem=lan.origem or origem,
                upload_id=upload_id,
                # o dono ainda nao foi decidido aqui; o que a origem declara ja
                # basta para desempatar entre duas receitas previstas no mes
                pessoa=lan.pessoa_hint or pessoa_padrao,
            )
            if decisao.existente_id:
                indice.marcar_usado(decisao.existente_id)

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
                natureza_esperada = lan.natureza_hint or (
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
                natureza_hint=lan.natureza_hint,
                rotulo_origem=lan.categoria_hint,
                bidirecionais=bidirecionais,
            )
            if categoria_id is None and achado.classificado:
                categoria_id = achado.categoria_id
                subcategoria_id = achado.subcategoria_id
                status, confianca = achado.status, achado.confianca

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
                "competencia": lan.competencia or (competencia or lan.data.strftime("%Y-%m")),
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
                "natureza": lan.natureza_hint,
            }
            posicao = len(linhas)
            linhas.append(registro)

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
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(substituir))
                .values(ativo=False, observacao="substituído pelo lançamento do extrato")
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

        pendentes = [
            (ids[posicao], linhas[posicao]["descricao"], linhas[posicao]["valor_centavos"])
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


def _classificar_com_ia(conn, pendentes: list[tuple[int, str, int]]) -> int:
    plano = plano_para_ia(conn)
    cats_idx, subs_idx = _indice_categorias(conn)
    naturezas = classify._natureza_por_categoria(conn)
    resolvidos = 0

    for inicio in range(0, len(pendentes), ai.LOTE):
        fatia = pendentes[inicio : inicio + ai.LOTE]
        entrada = [(i, desc, valor) for i, (_id, desc, valor) in enumerate(fatia)]
        for sugestao in ai.sugerir_categorias(entrada, plano):
            if sugestao.indice >= len(fatia):
                continue
            transacao_id, _desc, valor = fatia[sugestao.indice]
            categoria_id = cats_idx.get(sugestao.categoria.casefold())
            if not categoria_id:
                continue
            natureza_esperada = "receita" if valor > 0 else "despesa"
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
            sa.select(db.transacoes.c.descricao).where(db.transacoes.c.id == transacao_id)
        ).fetchone()
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
        alvo = f"%{normalizar(termo)}%"
        consulta = consulta.where(db.transacoes.c.descricao_norm.like(alvo))
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


# --------------------------------------------------------------------------
# contas e plano de contas
# --------------------------------------------------------------------------
def salvar_conta(engine, *, conta_id=None, nome, tipo, titular, instituicao, parser, ativa=True):
    with engine.begin() as conn:
        valores = dict(
            nome=nome.strip(), tipo=tipo, titular=titular,
            instituicao=instituicao.strip(), parser=parser, ativa=ativa,
        )
        if conta_id:
            conn.execute(sa.update(db.contas).where(db.contas.c.id == conta_id).values(**valores))
            return conta_id
        return conn.execute(sa.insert(db.contas).values(**valores)).inserted_primary_key[0]


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


def apagar_upload(engine, upload_id: int) -> int:
    """Desfaz uma importacao inteira - o 'undo' de um arquivo errado."""
    with engine.begin() as conn:
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
    return len(ids)


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


def sem_subcategoria(conn, competencia: str | None = None, limite: int = 200) -> list[dict]:
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
