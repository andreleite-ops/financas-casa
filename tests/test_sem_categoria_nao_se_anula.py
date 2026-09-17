"""Entrada e saída ainda sem categoria não se anulam.

O resumo agrupava o que não tem categoria num grupo só e decidia o lado pela
soma: um PIX de R$ 20.000 enviado sumia dentro do pró-labore de R$ 20.596
recebido, e o mês mostrava R$ 596 de receita e nenhuma despesa. O lado se
decide linha a linha, nunca na soma.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from parsers.base import Lancamento


def test_entrada_e_saida_pendentes_contam_cada_uma_do_seu_lado(engine):
    with engine.begin() as conn:
        conta = conn.execute(sa.insert(db.contas).values(
            nome="Conta teste", tipo="corrente", titular="André", instituicao="Banco",
            parser="generico", ativa=True,
        )).inserted_primary_key[0]
    repo.importar(
        engine, conta_id=conta, arquivo="a.pdf", origem="extrato", usuario="André", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 8, 7), descricao="XPTO RECEBIDO", valor_centavos=2_059_621),
            Lancamento(data=date(2026, 8, 19), descricao="XPTO ENVIADO", valor_centavos=-20_000),
        ],
    )
    with engine.connect() as conn:
        agosto = analytics.resumo(conn, competencia="2026-08")
        serie = {m["competencia"]: m for m in analytics.serie_mensal(conn, 2026)}
    assert agosto["receitas"] == 2_059_621
    assert agosto["despesas"] == 20_000
    assert serie["2026-08"]["receitas"] == 2_059_621
    assert serie["2026-08"]["despesas"] == 20_000
