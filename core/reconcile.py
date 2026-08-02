"""Critica de conferencia: planilha da Ro x extratos importados.

A planilha e a carga inicial do historico e pode nao ter 100% dos lancamentos.
Quando o extrato do mesmo periodo entra, esta tela mostra o que bateu, o que
faltava, o que so existe na planilha e o que divergiu.

So compara periodos onde as duas origens existem. Mes que ainda nao teve
extrato importado nao vira "faltante" - nao ha o que conferir ali.
"""

from __future__ import annotations

import sqlalchemy as sa

from . import db
from .texto import chave_estabelecimento

JANELA_DIAS = 5


def _periodos_com_as_duas_origens(conn) -> set[tuple[int, str]]:
    consulta = (
        sa.select(db.transacoes.c.conta_id, db.transacoes.c.competencia, db.transacoes.c.origem)
        .distinct()
    )
    por_origem: dict[str, set[tuple[int, str]]] = {"planilha": set(), "extrato": set()}
    for linha in conn.execute(consulta):
        if linha.origem in por_origem:
            por_origem[linha.origem].add((linha.conta_id, linha.competencia))
    return por_origem["planilha"] & por_origem["extrato"]


def _filtro_periodos(periodos: set[tuple[int, str]]):
    return sa.or_(
        *[
            sa.and_(
                db.transacoes.c.conta_id == conta_id,
                db.transacoes.c.competencia == competencia,
            )
            for conta_id, competencia in periodos
        ]
    )


def criticar(conn) -> dict:
    """Devolve os quatro grupos da critica, com as listas para revisao."""
    periodos = _periodos_com_as_duas_origens(conn)
    vazio = {
        "periodos": 0, "conferidos": 0, "faltantes": [], "so_planilha": [],
        "divergencias": [], "sem_conferencia": True,
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

    faltantes = [
        dict(linha._mapping)
        for linha in conn.execute(
            sa.select(*colunas)
            .select_from(juncao)
            .where(
                escopo,
                db.transacoes.c.origem == "extrato",
                db.transacoes.c.ativo == sa.true(),
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
            )
            .order_by(db.transacoes.c.data)
        )
    ]

    # o que sobrou na planilha pode ser divergencia de valor/data com o extrato
    por_chave: dict[tuple[int, str], list[dict]] = {}
    for item in faltantes:
        chave = chave_estabelecimento(item["descricao"])
        if chave:
            por_chave.setdefault((item["conta"], chave), []).append(item)

    divergencias, so_planilha = [], []
    usados: set[int] = set()
    for item in restantes_planilha:
        chave = chave_estabelecimento(item["descricao"])
        candidatos = por_chave.get((item["conta"], chave), []) if chave else []
        par = None
        for candidato in candidatos:
            if candidato["id"] in usados:
                continue
            if abs((candidato["data"] - item["data"]).days) <= JANELA_DIAS:
                par = candidato
                break
        if par:
            usados.add(par["id"])
            divergencias.append(
                {
                    "planilha": item,
                    "extrato": par,
                    "diferenca": par["valor_centavos"] - item["valor_centavos"],
                    "dias": (par["data"] - item["data"]).days,
                }
            )
        else:
            so_planilha.append(item)

    faltantes = [item for item in faltantes if item["id"] not in usados]

    return {
        "periodos": len(periodos),
        "conferidos": conferidos,
        "faltantes": faltantes,
        "so_planilha": so_planilha,
        "divergencias": divergencias,
        "sem_conferencia": False,
    }


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
                .values(observacao=f"mantido na conferência por {usuario}")
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
