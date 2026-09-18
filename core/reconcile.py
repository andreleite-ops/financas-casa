"""Critica de conferencia: planilha da Ro x extratos importados.

A planilha e a carga inicial do historico e pode nao ter 100% dos lancamentos.
Quando o extrato do mesmo periodo entra, esta tela mostra o que bateu, o que
faltava, o que so existe na planilha e o que divergiu.

So compara periodos onde as duas origens existem. Mes que ainda nao teve
extrato importado nao vira "faltante" - nao ha o que conferir ali.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from . import db
from .analytics import CATEGORIA_POUPANCA, CATEGORIA_TRANSFERENCIA
from .texto import chave_estabelecimento

JANELA_DIAS = 5


def _periodos_com_as_duas_origens(conn) -> set[str]:
    """Os meses em que existe planilha E extrato — em qualquer conta.

    Comparava por (conta, mês). Só que a planilha mora numa conta reservada
    só dela, e o extrato mora na conta do banco: a interseção por conta era
    vazia por construção, e a crítica respondia "ainda não há período com as
    duas origens" para sempre. A ferramenta feita para pegar despesa dobrada
    nunca chegou a rodar — e as despesas de agosto dobraram sem ninguém ver.
    """
    # so o que esta valendo: a compra de cartao desligada por "mes da
    # planilha" nao faz de julho um mes com extrato
    consulta = (
        sa.select(db.transacoes.c.competencia, db.transacoes.c.origem)
        .where(db.transacoes.c.ativo == sa.true())
        .distinct()
    )
    por_origem: dict[str, set[str]] = {"planilha": set(), "extrato": set()}
    for linha in conn.execute(consulta):
        if linha.origem in por_origem:
            por_origem[linha.origem].add(linha.competencia)
    # So mes fechado. No mes em curso o extrato e parcial por definicao, e "o
    # que so esta na planilha" e o resto do mes que ainda nao aconteceu —
    # inclusive as receitas previstas. Foi assim que um clique em "descartar
    # o que so esta na planilha" zerou a renda de setembro.
    em_curso = date.today().strftime("%Y-%m")
    return {c for c in por_origem["planilha"] & por_origem["extrato"] if c < em_curso}


def _filtro_periodos(periodos: set[str]):
    return db.transacoes.c.competencia.in_(sorted(periodos))


# A marca de "mantido": a linha da planilha fica valendo E sai da critica.
# Sem olhar a marca, "Manter as duas" gravava a decisao e a tela a esquecia na
# visita seguinte — a mesma linha voltava para sempre.
MARCA_MANTIDO = "mantido na conferência"
_CHAVE_ENCERRADA = "critica_encerrada_{}"


def criticas_encerradas(conn) -> set[str]:
    """Os meses em que o dono disse "entendi, fica como esta"."""
    prefixo = _CHAVE_ENCERRADA.format("")
    return {
        linha.chave[len(prefixo):]
        for linha in conn.execute(
            sa.select(db.config.c.chave).where(db.config.c.chave.like(prefixo + "%"))
        )
    }


def encerrar_critica(engine, competencia: str, usuario: str) -> int:
    """"Ok, entendi, nao vou mudar nada": o mes sai da critica, tudo como esta.

    O que so esta na planilha continua valendo (gasto em dinheiro, ou de uma
    conta que nao esta no sistema), com a marca de mantido; e o mes inteiro
    deixa de ser listado ate alguem reabrir. Devolve quantas linhas marcou.
    """
    with engine.begin() as conn:
        critica = criticar(conn)
        ids = [i["id"] for i in critica["so_planilha"] if i["competencia"] == competencia]
        # divergencia de valor diferente: as duas valem, como o dono pediu.
        # De valor igual, nao: e o mesmo gasto escrito de outro jeito, e
        # "fica como esta" nao pode ser o botao que o conta duas vezes
        exatos = []
        for item in critica["divergencias"]:
            if item["planilha"]["competencia"] != competencia:
                continue
            if item["diferenca"] == 0:
                exatos.append((item["planilha"]["id"], [e["id"] for e in item["extratos"]]))
            else:
                ids.append(item["planilha"]["id"])
        if ids:
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id.in_(ids))
                .values(observacao=f"{MARCA_MANTIDO} por {usuario}: fica como está")
            )
        if exatos:
            _aposentar_pares(conn, exatos, usuario)
        chave = _CHAVE_ENCERRADA.format(competencia)
        if conn.execute(sa.select(db.config.c.chave).where(db.config.c.chave == chave)).first():
            conn.execute(sa.update(db.config).where(db.config.c.chave == chave).values(valor=usuario))
        else:
            conn.execute(sa.insert(db.config).values(chave=chave, valor=usuario))
    return len(ids)


def reabrir_critica(engine, competencia: str) -> None:
    """Volta a listar o mes. As linhas ja marcadas como mantidas seguem
    mantidas — reabrir e para ver o que apareceu depois, nao para rediscutir."""
    with engine.begin() as conn:
        conn.execute(
            sa.delete(db.config).where(db.config.c.chave == _CHAVE_ENCERRADA.format(competencia))
        )


def criticar(conn) -> dict:
    """Devolve os quatro grupos da critica, com as listas para revisao."""
    encerradas = criticas_encerradas(conn)
    periodos = _periodos_com_as_duas_origens(conn) - encerradas
    vazio = {
        "periodos": 0, "conferidos": 0, "faltantes": [], "so_planilha": [],
        "divergencias": [], "sem_conferencia": True, "encerradas": sorted(encerradas),
    }
    if not periodos:
        return vazio

    escopo = _filtro_periodos(periodos)

    conferidos = conn.execute(
        sa.select(sa.func.count())
        .select_from(db.transacoes)
        .where(
            escopo,
            db.transacoes.c.origem == "extrato",
            db.transacoes.c.observacao == "conferido com a planilha",
        )
    ).scalar() or 0

    colunas = (
        db.transacoes.c.id,
        db.transacoes.c.data,
        db.transacoes.c.descricao,
        db.transacoes.c.valor_centavos,
        db.transacoes.c.competencia,
        db.contas.c.nome.label("conta"),
    )
    juncao = db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
    com_categoria = juncao.outerjoin(
        db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id
    )

    faltantes = [
        dict(linha._mapping)
        for linha in conn.execute(
            sa.select(*colunas)
            .select_from(com_categoria)
            .where(
                escopo,
                db.transacoes.c.origem == "extrato",
                db.transacoes.c.ativo == sa.true(),
                # a critica e de gastos: receita prevista tem o proprio
                # pareamento e nunca pode estar ao alcance do "descartar"
                db.transacoes.c.valor_centavos < 0,
                # pagamento de fatura, transferencia e aporte nao sao gasto:
                # parear a TED para a corretora com o "APORTE" da planilha
                # aposentava a poupanca do mes
                sa.or_(
                    db.categorias.c.nome.is_(None),
                    db.categorias.c.nome.not_in((CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA)),
                ),
                sa.or_(
                    db.transacoes.c.observacao.is_(None),
                    db.transacoes.c.observacao != "conferido com a planilha",
                ),
            )
            .order_by(db.transacoes.c.data)
        )
    ]

    restantes_planilha = [
        dict(linha._mapping)
        for linha in conn.execute(
            sa.select(*colunas, db.transacoes.c.conta_id)
            .select_from(juncao)
            .where(
                escopo,
                db.transacoes.c.origem == "planilha",
                db.transacoes.c.ativo == sa.true(),
                db.transacoes.c.valor_centavos < 0,
                sa.or_(
                    db.transacoes.c.observacao.is_(None),
                    sa.not_(db.transacoes.c.observacao.like(MARCA_MANTIDO + "%")),
                ),
            )
            .order_by(db.transacoes.c.data)
        )
    ]

    # o que sobrou na planilha pode ser divergencia de valor/data com o extrato
    # sem a conta na chave: a planilha anota o gasto sem dizer de qual conta
    # saiu, e exigir a mesma conta era o outro jeito de nunca casar nada
    por_chave: dict[str, list[dict]] = {}
    por_valor: dict[tuple[str, int], list[dict]] = {}
    for item in faltantes:
        chave = chave_estabelecimento(item["descricao"])
        if chave:
            por_chave.setdefault(chave, []).append(item)
        por_valor.setdefault((item["competencia"], item["valor_centavos"]), []).append(item)

    divergencias, so_planilha = [], []
    usados: set[int] = set()
    for item in restantes_planilha:
        chave = chave_estabelecimento(item["descricao"])
        candidatos = por_chave.get(chave, []) if chave else []
        par = None
        for candidato in candidatos:
            if candidato["id"] in usados:
                continue
            if abs((candidato["data"] - item["data"]).days) <= JANELA_DIAS:
                par = candidato
                break
        # a descrição digitada nunca é a do banco; o mesmo valor, no mesmo mês,
        # com qualquer dia, é o segundo jeito de reconhecer o mesmo gasto — o
        # condomínio anotado no dia 5 e debitado no dia 10
        if par is None:
            for candidato in por_valor.get((item["competencia"], item["valor_centavos"]), []):
                if candidato["id"] not in usados:
                    par = candidato
                    break
        if par:
            usados.add(par["id"])
            divergencias.append(
                {
                    "planilha": item,
                    "extrato": par,
                    "extratos": [par],
                    "diferenca": par["valor_centavos"] - item["valor_centavos"],
                    "dias": (par["data"] - item["data"]).days,
                }
            )
        else:
            so_planilha.append(item)

    # a planilha anota "PENSAO 15.800" e o banco paga em dois PIX de 7.900:
    # o mesmo dinheiro, que nenhum valor igual ao centavo reconhece. Soma de
    # dois ou tres debitos do mes, para linhas grandes, e o terceiro jeito
    ainda_so_planilha = []
    for item in so_planilha:
        partes = _soma_de_partes(item, faltantes, usados)
        if partes:
            usados.update(p["id"] for p in partes)
            divergencias.append(
                {
                    "planilha": item,
                    "extrato": partes[0],
                    "extratos": partes,
                    "diferenca": 0,
                    "dias": (partes[0]["data"] - item["data"]).days,
                }
            )
        else:
            ainda_so_planilha.append(item)
    so_planilha = ainda_so_planilha

    faltantes = [item for item in faltantes if item["id"] not in usados]

    return {
        "periodos": len(periodos),
        "conferidos": conferidos,
        "faltantes": faltantes,
        "so_planilha": so_planilha,
        "divergencias": divergencias,
        "sem_conferencia": False,
        "encerradas": sorted(encerradas),
    }


# a soma de partes so vale para linha grande, e cada parte tem de ser uma
# fatia relevante: 145,00 = 100,00 + 45,00 de duas linhas sem relacao e
# coincidencia facil; 15.800 = 7.900 + 7.900 no mesmo mes nao e
MINIMO_PARA_SOMA = 100_000
FRACAO_MINIMA_DA_PARTE = 0.10


def _soma_de_partes(item: dict, faltantes: list[dict], usados: set[int]) -> list[dict] | None:
    alvo = item["valor_centavos"]
    if -alvo < MINIMO_PARA_SOMA:
        return None
    candidatos = [
        f for f in faltantes
        if f["id"] not in usados and f["competencia"] == item["competencia"]
        and -f["valor_centavos"] >= -alvo * FRACAO_MINIMA_DA_PARTE
        and -f["valor_centavos"] < -alvo
    ]
    candidatos.sort(key=lambda f: (f["data"], f["id"]))
    por_valor: dict[int, list[dict]] = {}
    for f in candidatos:
        por_valor.setdefault(f["valor_centavos"], []).append(f)
    # pares
    for a in candidatos:
        for b in por_valor.get(alvo - a["valor_centavos"], []):
            if b["id"] != a["id"]:
                return [a, b]
    # trios
    for i, a in enumerate(candidatos):
        for b in candidatos[i + 1:]:
            for c in por_valor.get(alvo - a["valor_centavos"] - b["valor_centavos"], []):
                if c["id"] not in (a["id"], b["id"]):
                    return [a, b, c]
    return None


def resolver_divergencia(engine, *, planilha_id: int, manter: str, usuario: str) -> None:
    """manter: 'extrato' descarta a linha da planilha; 'planilha' mantem as duas."""
    with engine.begin() as conn:
        if manter == "extrato":
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id == planilha_id)
                .values(ativo=False, observacao=f"descartado na conferência por {usuario}")
            )
        else:
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id == planilha_id)
                .values(observacao=f"{MARCA_MANTIDO} por {usuario}")
            )


def descartar_da_planilha(engine, ids: list[int], usuario: str) -> int:
    if not ids:
        return 0
    with engine.begin() as conn:
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id.in_(ids))
            .values(ativo=False, observacao=f"descartado na conferência por {usuario}")
        )
    return len(ids)


def aposentar_pares_exatos(engine, usuario: str, competencia: str | None = None) -> int:
    """Vale o extrato em todas as divergencias de valor exato, de uma vez.

    Uma divergencia com diferenca zero e o mesmo gasto escrito de outro jeito
    — o condominio anotado no dia 5 e debitado no dia 10. Resolve-las uma a
    uma, num mes com cem delas, nao e razoavel; e nenhuma pede decisao, porque
    o valor bate no centavo. As de valor diferente continuam na tela, uma a
    uma: essas pedem alguem olhando.
    """
    with engine.connect() as conn:
        critica = criticar(conn)
    pares = [
        (item["planilha"]["id"], [e["id"] for e in item["extratos"]])
        for item in critica["divergencias"]
        if item["diferenca"] == 0
        and (competencia is None or item["planilha"]["competencia"] == competencia)
    ]
    if not pares:
        return 0
    with engine.begin() as conn:
        _aposentar_pares(conn, pares, usuario)
    return len(pares)


def _aposentar_pares(conn, pares: list[tuple[int, list[int]]], usuario: str) -> None:
    """Aposenta a linha da planilha de cada par (planilha_id, [extrato_ids])."""
    extratos = [e for _, es in pares for e in es]
    # a linha da planilha aponta para o upload do extrato que a substituiu: e
    # por esse ponteiro que desfazer o upload a devolve ao mes. Uma consulta
    # para os uploads e um update por upload — cem pares no Supabase nao
    # podem custar duzentas idas ao banco
    upload_por_extrato = {
        linha.id: linha.upload_id
        for linha in conn.execute(
            sa.select(db.transacoes.c.id, db.transacoes.c.upload_id)
            .where(db.transacoes.c.id.in_(extratos))
        )
    }
    por_upload: dict[int | None, list[int]] = {}
    for planilha_id, extrato_ids in pares:
        por_upload.setdefault(upload_por_extrato.get(extrato_ids[0]), []).append(planilha_id)
    for upload_id, ids in por_upload.items():
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id.in_(ids))
            .values(ativo=False, substituido_por=upload_id,
                    observacao=f"conferido em massa com o extrato por {usuario}")
        )
    # o lado do extrato ganha a mesma marca que a conferencia do upload
    # deixa: e por ela que a critica conta o par como conferido, em vez de
    # listar a linha do banco como "faltava na planilha"
    conn.execute(
        sa.update(db.transacoes)
        .where(db.transacoes.c.id.in_(extratos))
        .values(observacao="conferido com a planilha")
    )
