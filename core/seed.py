"""Carga inicial: plano de contas, contas, regras e metas.

Roda a cada inicializacao do app e precisa ser barata: le o que ja existe em
poucas consultas e insere em lote so o que falta. A versao anterior consultava
o banco uma vez por categoria, subcategoria e regra — perto de 500 idas e
voltas, imperceptiveis no SQLite local e lentissimas contra um Postgres na
nuvem, onde cada ida custa uns 150 ms.
"""

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


def _inserir_em_lote(conn, tabela, linhas: list[dict]) -> None:
    """Um INSERT com varias linhas, em vez de um por registro."""
    if linhas:
        conn.execute(sa.insert(tabela), linhas)


def _semear_categorias(conn) -> None:
    existentes = {
        (linha.natureza, linha.nome): linha.id
        for linha in conn.execute(
            sa.select(db.categorias.c.id, db.categorias.c.nome, db.categorias.c.natureza)
        )
    }
    novas = [
        {"nome": cat, "natureza": natureza, "ordem": ordem}
        for natureza, grupos in (("receita", RECEITAS), ("despesa", DESPESAS))
        for ordem, (cat, _subs) in enumerate(grupos, start=1)
        if (natureza, cat) not in existentes
    ]
    _inserir_em_lote(conn, db.categorias, novas)

    # relê só se algo entrou agora
    if novas:
        existentes = {
            (linha.natureza, linha.nome): linha.id
            for linha in conn.execute(
                sa.select(db.categorias.c.id, db.categorias.c.nome, db.categorias.c.natureza)
            )
        }

    subs_existentes = {
        (linha.categoria_id, linha.nome)
        for linha in conn.execute(
            sa.select(db.subcategorias.c.categoria_id, db.subcategorias.c.nome)
        )
    }
    novas_subs = []
    for natureza, grupos in (("receita", RECEITAS), ("despesa", DESPESAS)):
        for cat, subs in grupos:
            categoria_id = existentes.get((natureza, cat))
            if categoria_id is None:
                continue
            for ordem, sub in enumerate(subs, start=1):
                if (categoria_id, sub) not in subs_existentes:
                    novas_subs.append(
                        {"categoria_id": categoria_id, "nome": sub, "ordem": ordem}
                    )
    _inserir_em_lote(conn, db.subcategorias, novas_subs)


def _semear_contas(conn) -> None:
    existentes = {linha.nome for linha in conn.execute(sa.select(db.contas.c.nome))}
    _inserir_em_lote(
        conn, db.contas,
        [
            {"nome": nome, "tipo": tipo, "titular": titular,
             "instituicao": inst, "parser": parser}
            for nome, tipo, titular, inst, parser in CONTAS_INICIAIS
            if nome not in existentes
        ],
    )


def _semear_regras(conn) -> int:
    """Insere o dicionario inicial. Padroes mais especificos vem primeiro."""
    categorias = {
        linha.nome: linha.id
        for linha in conn.execute(sa.select(db.categorias.c.id, db.categorias.c.nome))
    }
    subs = {
        (linha.categoria_id, linha.nome): linha.id
        for linha in conn.execute(
            sa.select(db.subcategorias.c.id, db.subcategorias.c.categoria_id,
                      db.subcategorias.c.nome)
        )
    }
    ja_existem = {
        (linha.padrao, linha.tipo_match)
        for linha in conn.execute(sa.select(db.regras.c.padrao, db.regras.c.tipo_match))
    }

    novas, vistos = [], set()
    # padrao mais longo = mais especifico = prioridade melhor (numero menor)
    for pos, (padrao, categoria, subcategoria) in enumerate(
        sorted(REGRAS_INICIAIS, key=lambda r: -len(r[0]))
    ):
        categoria_id = categorias.get(categoria)
        if categoria_id is None:
            continue
        padrao_norm = normalizar(padrao)
        chave = (padrao_norm, "contem")
        if chave in ja_existem or chave in vistos:
            continue
        vistos.add(chave)
        novas.append(
            {
                "padrao": padrao_norm,
                "tipo_match": "contem",
                "categoria_id": categoria_id,
                "subcategoria_id": subs.get((categoria_id, subcategoria)),
                "pessoa": None,
                "prioridade": 200 + pos,
                "origem": "sistema",
                "criada_por": None,
            }
        )
    _inserir_em_lote(conn, db.regras, novas)
    return len(novas)


def _semear_metas(conn, ano: int) -> None:
    por_nome = {
        linha.nome: linha.id
        for linha in conn.execute(
            sa.select(db.categorias.c.id, db.categorias.c.nome).where(
                db.categorias.c.natureza == "despesa"
            )
        )
    }
    ja_existem = {
        linha.categoria_id
        for linha in conn.execute(
            sa.select(db.metas.c.categoria_id).where(db.metas.c.ano == ano)
        )
    }
    _inserir_em_lote(
        conn, db.metas,
        [
            {"ano": ano, "categoria_id": por_nome[categoria], "percentual": pct}
            for categoria, pct in METAS_INICIAIS.items()
            if categoria in por_nome and por_nome[categoria] not in ja_existem
        ],
    )


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
        totais = conn.execute(
            sa.select(
                sa.select(sa.func.count()).select_from(db.categorias).scalar_subquery(),
                sa.select(sa.func.count()).select_from(db.subcategorias).scalar_subquery(),
                sa.select(sa.func.count()).select_from(db.contas).scalar_subquery(),
                sa.select(sa.func.count()).select_from(db.regras).scalar_subquery(),
            )
        ).one()
    return {
        "categorias": totais[0],
        "subcategorias": totais[1],
        "contas": totais[2],
        "regras": totais[3],
        "regras_novas": regras,
    }
