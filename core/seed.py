"""Carga inicial: plano de contas, contas, regras e metas."""

from __future__ import annotations

import sqlalchemy as sa

from . import db
from .plano_contas import (
    CONTAS_INICIAIS,
    DESPESAS,
    METAS_INICIAIS,
    RECEITAS,
    REGRAS_INICIAIS,
)
from .texto import normalizar


def _semear_categorias(conn) -> dict[tuple[str, str], int]:
    ids: dict[tuple[str, str], int] = {}
    for natureza, grupos in (("receita", RECEITAS), ("despesa", DESPESAS)):
        for ordem, (cat, subs) in enumerate(grupos, start=1):
            existente = conn.execute(
                sa.select(db.categorias.c.id).where(
                    db.categorias.c.nome == cat, db.categorias.c.natureza == natureza
                )
            ).scalar()
            if existente is None:
                existente = conn.execute(
                    sa.insert(db.categorias).values(nome=cat, natureza=natureza, ordem=ordem)
                ).inserted_primary_key[0]
            ids[(natureza, cat)] = existente
            for sub_ordem, sub in enumerate(subs, start=1):
                ja = conn.execute(
                    sa.select(db.subcategorias.c.id).where(
                        db.subcategorias.c.categoria_id == existente,
                        db.subcategorias.c.nome == sub,
                    )
                ).scalar()
                if ja is None:
                    conn.execute(
                        sa.insert(db.subcategorias).values(
                            categoria_id=existente, nome=sub, ordem=sub_ordem
                        )
                    )
    return ids


def _semear_contas(conn) -> None:
    for nome, tipo, titular, inst, parser in CONTAS_INICIAIS:
        ja = conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar()
        if ja is None:
            conn.execute(
                sa.insert(db.contas).values(
                    nome=nome, tipo=tipo, titular=titular, instituicao=inst, parser=parser
                )
            )


def _semear_regras(conn) -> int:
    """Insere o dicionario inicial. Padroes mais especificos vem primeiro."""
    cats = {
        (r.natureza, r.nome): r.id
        for r in conn.execute(
            sa.select(db.categorias.c.id, db.categorias.c.nome, db.categorias.c.natureza)
        )
    }
    subs: dict[tuple[int, str], int] = {
        (r.categoria_id, r.nome): r.id
        for r in conn.execute(
            sa.select(db.subcategorias.c.id, db.subcategorias.c.categoria_id, db.subcategorias.c.nome)
        )
    }
    por_nome = {nome: cid for (_nat, nome), cid in cats.items()}

    inseridas = 0
    # padrao mais longo = mais especifico = prioridade melhor (numero menor)
    ordenadas = sorted(REGRAS_INICIAIS, key=lambda r: -len(r[0]))
    for pos, (padrao, categoria, subcategoria) in enumerate(ordenadas):
        cid = por_nome.get(categoria)
        if cid is None:
            continue
        sid = subs.get((cid, subcategoria))
        padrao_norm = normalizar(padrao)
        ja = conn.execute(
            sa.select(db.regras.c.id).where(
                db.regras.c.padrao == padrao_norm, db.regras.c.tipo_match == "contem"
            )
        ).scalar()
        if ja is not None:
            continue
        conn.execute(
            sa.insert(db.regras).values(
                padrao=padrao_norm,
                tipo_match="contem",
                categoria_id=cid,
                subcategoria_id=sid,
                prioridade=200 + pos,
                origem="sistema",
            )
        )
        inseridas += 1
    return inseridas


def _semear_metas(conn, ano: int) -> None:
    por_nome = {
        r.nome: r.id
        for r in conn.execute(
            sa.select(db.categorias.c.id, db.categorias.c.nome).where(
                db.categorias.c.natureza == "despesa"
            )
        )
    }
    for categoria, pct in METAS_INICIAIS.items():
        cid = por_nome.get(categoria)
        if cid is None:
            continue
        ja = conn.execute(
            sa.select(db.metas.c.id).where(db.metas.c.ano == ano, db.metas.c.categoria_id == cid)
        ).scalar()
        if ja is None:
            conn.execute(sa.insert(db.metas).values(ano=ano, categoria_id=cid, percentual=pct))


def semear(engine=None, ano_metas: int | None = None) -> dict:
    """Idempotente: pode rodar a cada inicializacao do app sem duplicar nada."""
    from datetime import date

    engine = engine or db.get_engine()
    db.criar_schema(engine)
    ano = ano_metas or date.today().year
    with engine.begin() as conn:
        _semear_categorias(conn)
        _semear_contas(conn)
        regras = _semear_regras(conn)
        _semear_metas(conn, ano)
        resumo = {
            "categorias": conn.execute(sa.select(sa.func.count()).select_from(db.categorias)).scalar(),
            "subcategorias": conn.execute(
                sa.select(sa.func.count()).select_from(db.subcategorias)
            ).scalar(),
            "contas": conn.execute(sa.select(sa.func.count()).select_from(db.contas)).scalar(),
            "regras": conn.execute(sa.select(sa.func.count()).select_from(db.regras)).scalar(),
            "regras_novas": regras,
        }
    return resumo
