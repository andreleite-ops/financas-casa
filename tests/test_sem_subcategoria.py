"""Achar o lançamento que parou na categoria — e chegar até ele.

O relatório dizia "R$ 231 em 1 lançamento ainda sem subcategoria" e não dizia
qual. Ele não está na fila de pendências (já tem categoria) e caçá-lo pela
busca exige saber o nome, que é justamente o que não se sabe.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import db, repo
from core.dedup import hash_lancamento
from core.texto import normalizar


def _cat(conn, nome: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _sub(conn, categoria_id: int, nome: str) -> int:
    return conn.execute(
        sa.select(db.subcategorias.c.id).where(
            db.subcategorias.c.categoria_id == categoria_id,
            db.subcategorias.c.nome == nome,
        )
    ).scalar_one()


def _lancar(conn, dia, descricao, valor, categoria_id, subcategoria_id=None):
    conta = conn.execute(sa.select(db.contas.c.id).limit(1)).scalar_one()
    norm = normalizar(descricao)
    return conn.execute(
        sa.insert(db.transacoes).values(
            data=dia, competencia=dia.strftime("%Y-%m"), descricao=descricao,
            descricao_norm=norm, valor_centavos=valor, conta_id=conta,
            categoria_id=categoria_id, subcategoria_id=subcategoria_id,
            pessoa="Casal", status="manual", confianca=1.0, origem="extrato",
            hash_dedup=hash_lancamento(conta, dia, valor, norm), ativo=True,
        )
    ).inserted_primary_key[0]


def test_conta_por_mes_o_que_falta_detalhar(engine, conn):
    """É o número do rótulo da seção e o que preenche o filtro de mês."""
    moradia = _cat(conn, "Moradia")
    _lancar(conn, date(2026, 8, 10), "COLEGIO", -23_171, moradia)
    _lancar(conn, date(2026, 7, 3), "CONDOMINIO", -90_000, moradia)
    _lancar(conn, date(2026, 8, 15), "ENEL", -31_200, moradia,
            _sub(conn, moradia, "Energia"))                     # este está detalhado

    assert repo.sem_subcategoria_por_mes(conn) == {"2026-08": 1, "2026-07": 1}


def test_filtra_pela_categoria_que_o_relatorio_apontou(engine, conn):
    """Quem chega pela Visão Geral vem de uma categoria só — e é nela que quer ficar."""
    moradia, saude = _cat(conn, "Moradia"), _cat(conn, "Saúde")
    _lancar(conn, date(2026, 8, 10), "COLEGIO", -23_171, moradia)
    _lancar(conn, date(2026, 8, 12), "DROGARIA", -12_000, saude)

    so_moradia = repo.sem_subcategoria(conn, competencia="2026-08", categoria_id=moradia)
    assert [linha["descricao"] for linha in so_moradia] == ["COLEGIO"]
    assert len(repo.sem_subcategoria(conn, competencia="2026-08")) == 2


def test_detalhar_tira_o_lancamento_da_lista(engine, conn):
    """O ciclo fecha: o que foi detalhado some da conta e não volta."""
    moradia = _cat(conn, "Moradia")
    condominio = _sub(conn, moradia, "Condomínio")
    linha_id = _lancar(conn, date(2026, 8, 10), "COLEGIO SANTA MARIA", -23_171, moradia)
    assert repo.sem_subcategoria_por_mes(conn) == {"2026-08": 1}
    conn.commit()

    repo.reclassificar(
        engine, linha_id, categoria_id=moradia, subcategoria_id=condominio,
        pessoa="Casal", usuario="André", criar_regra=True,
    )

    with engine.connect() as leitura:
        assert repo.sem_subcategoria_por_mes(leitura) == {}


def test_lancamento_inativo_nao_entra_na_conta(engine, conn):
    """Previsão substituída pelo extrato está fora dos relatórios — e daqui também."""
    moradia = _cat(conn, "Moradia")
    linha_id = _lancar(conn, date(2026, 8, 10), "COLEGIO", -23_171, moradia)
    conn.execute(
        sa.update(db.transacoes).where(db.transacoes.c.id == linha_id).values(ativo=False)
    )

    assert repo.sem_subcategoria_por_mes(conn) == {}
