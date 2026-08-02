"""Testes de core.dedup: duplicidade exata, provavel e fila de conferencia."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import dedup, db, repo
from parsers.base import Lancamento


def _conta_id(engine, nome: str = "Nubank Mastercard") -> int:
    with engine.begin() as conn:
        return conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar_one()


def _lote_basico() -> list[Lancamento]:
    return [
        Lancamento(data=date(2026, 7, 1), descricao="ALUGUEL APTO", valor_centavos=-250000),
        Lancamento(data=date(2026, 7, 3), descricao="PADARIA CENTRAL", valor_centavos=-1580),
        Lancamento(data=date(2026, 7, 5), descricao="IFOOD *RESTAURANTE X", valor_centavos=-4590),
    ]


def _ativos(engine, conta_id: int) -> list:
    with engine.begin() as conn:
        return conn.execute(
            sa.select(db.transacoes).where(db.transacoes.c.conta_id == conta_id)
        ).fetchall()


# --------------------------------------------------------------------------
# duplicata exata: mesmo lote reimportado
# --------------------------------------------------------------------------
def test_reimportar_mesmo_lote_gera_duplicatas_exatas_inativas(engine):
    conta_id = _conta_id(engine)

    resumo_1 = repo.importar(
        engine, conta_id=conta_id, lancamentos=_lote_basico(),
        arquivo="extrato_1.csv", usuario="andre", usar_ia=False,
    )
    assert resumo_1["importados"] == 3
    assert resumo_1["duplicados_exatos"] == 0

    resumo_2 = repo.importar(
        engine, conta_id=conta_id, lancamentos=_lote_basico(),
        arquivo="extrato_1_de_novo.csv", usuario="andre", usar_ia=False,
    )
    assert resumo_2["importados"] == 0
    assert resumo_2["duplicados_exatos"] == 3

    todas = _ativos(engine, conta_id)
    assert len(todas) == 6
    ativas = [t for t in todas if t.ativo]
    inativas = [t for t in todas if not t.ativo]
    assert len(ativas) == 3          # so o primeiro lote fica visivel
    assert len(inativas) == 3        # as duplicatas do segundo lote ficam de fora


def test_reconciliacao_dedup_fora_dos_relatorios(engine):
    from core import analytics

    conta_id = _conta_id(engine)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="a.csv", usuario="andre", usar_ia=False)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="b.csv", usuario="andre", usar_ia=False)

    with engine.begin() as conn:
        lista = analytics.lancamentos(conn, competencia="2026-07")
    # os relatorios so devem contar as 3 transacoes ativas, nao as 6 gravadas
    assert len(lista) == 3


# --------------------------------------------------------------------------
# fila de pendentes e resolucao
# --------------------------------------------------------------------------
def test_pendentes_lista_os_pares(engine):
    conta_id = _conta_id(engine)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="a.csv", usuario="andre", usar_ia=False)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="b.csv", usuario="andre", usar_ia=False)

    with engine.begin() as conn:
        fila = dedup.pendentes(conn)
    assert len(fila) == 3
    for par in fila:
        assert par["tipo"] == "exata"
        assert par["nova_descricao"] == par["velha_descricao"]


def test_resolver_excluir_apaga_o_novo(engine):
    conta_id = _conta_id(engine)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="a.csv", usuario="andre", usar_ia=False)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="b.csv", usuario="andre", usar_ia=False)

    with engine.begin() as conn:
        dup_id = dedup.pendentes(conn)[0]["dup_id"]
        nova_id = dedup.pendentes(conn)[0]["nova_id"]
        dedup.resolver(conn, dup_id, "excluir", "andre")

    with engine.begin() as conn:
        existe = conn.execute(
            sa.select(db.transacoes.c.id).where(db.transacoes.c.id == nova_id)
        ).scalar()
        resolvida = conn.execute(
            sa.select(db.duplicidades.c.resolvida, db.duplicidades.c.decisao)
            .where(db.duplicidades.c.id == dup_id)
        ).fetchone()
    assert existe is None
    assert resolvida.resolvida is True
    assert resolvida.decisao == "excluida"


def test_resolver_manter_ativa_o_novo(engine):
    conta_id = _conta_id(engine)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="a.csv", usuario="andre", usar_ia=False)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="b.csv", usuario="andre", usar_ia=False)

    with engine.begin() as conn:
        par = dedup.pendentes(conn)[0]
        dedup.resolver(conn, par["dup_id"], "manter", "andre")

    with engine.begin() as conn:
        nova = conn.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == par["nova_id"])
        ).scalar()
    assert nova is True


def test_resolver_todas_exatas(engine):
    conta_id = _conta_id(engine)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="a.csv", usuario="andre", usar_ia=False)
    repo.importar(engine, conta_id=conta_id, lancamentos=_lote_basico(),
                  arquivo="b.csv", usuario="andre", usar_ia=False)

    with engine.begin() as conn:
        quantidade = dedup.resolver_todas_exatas(conn, "andre")
    assert quantidade == 3

    with engine.begin() as conn:
        fila = dedup.pendentes(conn)
        restantes = conn.execute(sa.select(sa.func.count()).select_from(db.transacoes)).scalar()
    assert fila == []
    assert restantes == 3  # so sobrou o primeiro lote; o segundo foi excluido


# --------------------------------------------------------------------------
# duplicata provavel: mesmo estabelecimento/valor, uploads diferentes, poucos dias
# --------------------------------------------------------------------------
def test_duplicata_provavel_uploads_diferentes(engine):
    conta_id = _conta_id(engine)
    repo.importar(
        engine, conta_id=conta_id,
        lancamentos=[Lancamento(data=date(2026, 7, 12), descricao="UBER *TRIP", valor_centavos=-3200)],
        arquivo="fatura_parcial.csv", usuario="andre", usar_ia=False,
    )
    resumo = repo.importar(
        engine, conta_id=conta_id,
        lancamentos=[Lancamento(data=date(2026, 7, 14), descricao="UBER *TRIP", valor_centavos=-3200)],
        arquivo="fatura_completa.csv", usuario="andre", usar_ia=False,
    )
    assert resumo["duplicados_provaveis"] == 1
    assert resumo["duplicados_exatos"] == 0

    with engine.begin() as conn:
        fila = dedup.pendentes(conn)
    provaveis = [p for p in fila if p["tipo"] == "provavel"]
    assert len(provaveis) == 1
    assert "dia" in provaveis[0]["motivo"]
