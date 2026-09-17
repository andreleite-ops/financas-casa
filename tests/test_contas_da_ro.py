"""As duas contas da Rô no Itaú nascem do cadastro inicial, com a agência.

E quem já tinha a conta antiga ("Itaú C/C") não perde nada: ela é renomeada
no lugar, com o histórico preso a ela, e ganha a agência.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import db, repo, seed
from core.dedup import hash_lancamento
from core.texto import normalizar


def _contas(conn) -> dict[str, dict]:
    return {c["nome"]: c for c in repo.listar_contas(conn, so_ativas=False)}


def test_cadastro_inicial_tem_as_duas_contas_com_agencia(engine):
    with engine.connect() as conn:
        contas = _contas(conn)
    assert contas["Itaú 8839"]["identificador"] == "8839"
    assert contas["Itaú 0660"]["identificador"] == "0660"
    for nome in ("Itaú 8839", "Itaú 0660"):
        assert (contas[nome]["tipo"], contas[nome]["titular"], contas[nome]["parser"]) == (
            "corrente", "Rô", "itau",
        )


def test_conta_antiga_e_renomeada_sem_perder_o_historico(engine):
    with engine.begin() as conn:
        # o estado de quem subiu o app antes: uma conta só, com lançamento
        conn.execute(sa.delete(db.contas).where(db.contas.c.nome == "Itaú 8839"))
        antiga = conn.execute(
            sa.insert(db.contas).values(
                nome="Itaú C/C", tipo="corrente", titular="Rô",
                instituicao="Itaú", parser="itau", ativa=True,
            )
        ).inserted_primary_key[0]
        conn.execute(
            sa.insert(db.transacoes).values(
                data=date(2026, 8, 5), competencia="2026-08", descricao="PIX TRANSF X",
                descricao_norm=normalizar("PIX TRANSF X"), valor_centavos=30_000,
                conta_id=antiga, pessoa="Rô", status="pendente", origem="extrato",
                hash_dedup=hash_lancamento(antiga, date(2026, 8, 5), 30_000,
                                           normalizar("PIX TRANSF X")),
                ativo=True,
            )
        )

    seed.semear(engine)

    with engine.connect() as conn:
        contas = _contas(conn)
        assert "Itaú C/C" not in contas
        assert contas["Itaú 8839"]["id"] == antiga, "mesma conta, só o nome mudou"
        assert contas["Itaú 8839"]["identificador"] == "8839"
        ainda_la = conn.execute(
            sa.select(sa.func.count()).select_from(db.transacoes)
            .where(db.transacoes.c.conta_id == antiga)
        ).scalar()
        assert ainda_la == 1

    # e de novo, como toda subida vai rodar: nada muda
    seed.semear(engine)
    with engine.connect() as conn:
        assert _contas(conn)["Itaú 8839"]["id"] == antiga
