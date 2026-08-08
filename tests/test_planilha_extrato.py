"""O caso real: a Rô lançou o histórico na planilha, com descrição própria.

Quando o extrato do mesmo período entra, o mesmo gasto aparece escrito de
outro jeito — "Mercado" contra "SUPERM PAO DE ACUCAR 1234". Se o casamento
exigisse a descrição igual, o extrato duplicaria todo o histórico dela.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, reconcile, repo
from parsers.base import Lancamento


def _planilha_da_ro():
    """Como a Rô lançou: descrição curta, escrita à mão."""
    return [
        Lancamento(data=date(2026, 5, 4), descricao="Mercado", valor_centavos=-74218,
                   origem="planilha"),
        Lancamento(data=date(2026, 5, 9), descricao="Farmácia", valor_centavos=-11890,
                   origem="planilha"),
        Lancamento(data=date(2026, 5, 15), descricao="Uber", valor_centavos=-3215,
                   origem="planilha"),
        Lancamento(data=date(2026, 5, 20), descricao="Feira da rua", valor_centavos=-8000,
                   origem="planilha"),  # pago em dinheiro, não vai aparecer no extrato
    ]


def _extrato_do_banco():
    """Como o banco escreve os mesmos gastos, menos o que foi em dinheiro."""
    return [
        Lancamento(data=date(2026, 5, 4), descricao="SUPERM PAO DE ACUCAR 1234",
                   valor_centavos=-74218),
        Lancamento(data=date(2026, 5, 9), descricao="DROGARIA RAIA 442",
                   valor_centavos=-11890),
        Lancamento(data=date(2026, 5, 15), descricao="UBER *TRIP", valor_centavos=-3215),
        Lancamento(data=date(2026, 5, 27), descricao="NETFLIX.COM", valor_centavos=-5590),
    ]


def test_extrato_nao_duplica_o_que_a_planilha_ja_tinha(engine):
    repo.importar(engine, conta_id=4, lancamentos=_planilha_da_ro(),
                  arquivo="planilha_ro.xlsx", usuario="Rô", origem="planilha",
                  competencia="2026-05", usar_ia=False)
    resumo = repo.importar(engine, conta_id=4, lancamentos=_extrato_do_banco(),
                           arquivo="extrato_maio.pdf", usuario="André", origem="extrato",
                           competencia="2026-05", usar_ia=False)

    # três casaram com a planilha mesmo com a descrição totalmente diferente
    assert resumo["conferidos_planilha"] == 3
    assert resumo["duplicados_exatos"] == 0

    with engine.connect() as conn:
        total = analytics.resumo(conn, competencia="2026-05")
        # 74218 + 11890 + 3215 + 5590 (Netflix) + 8000 (feira, só na planilha)
        assert total["despesas"] == 74218 + 11890 + 3215 + 5590 + 8000

        ativos = conn.execute(
            sa.select(sa.func.count()).select_from(db.transacoes).where(
                db.transacoes.c.ativo == sa.true()
            )
        ).scalar()
        assert ativos == 5  # 4 do extrato + a feira, que só existe na planilha


def test_versao_do_extrato_prevalece_e_herda_a_categoria(engine):
    """A Rô já classificou na planilha; a descrição do banco não pode perder isso."""
    with engine.connect() as conn:
        alimentacao = conn.execute(
            sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Alimentação")
        ).scalar()
        sub = conn.execute(
            sa.select(db.subcategorias.c.id).where(
                db.subcategorias.c.categoria_id == alimentacao,
                db.subcategorias.c.nome == "No Domicílio",
            )
        ).scalar()

    planilha = [Lancamento(data=date(2026, 5, 4), descricao="Compras do mês",
                           valor_centavos=-50000, origem="planilha",
                           categoria_hint="Alimentação", subcategoria_hint="No Domicílio")]
    repo.importar(engine, conta_id=4, lancamentos=planilha, arquivo="p.xlsx",
                  usuario="Rô", origem="planilha", competencia="2026-05", usar_ia=False)

    # descrição que nenhuma regra reconhece, para o herdar ser o único caminho
    extrato = [Lancamento(data=date(2026, 5, 4), descricao="EC *483920 SP",
                          valor_centavos=-50000)]
    repo.importar(engine, conta_id=4, lancamentos=extrato, arquivo="e.pdf",
                  usuario="André", origem="extrato", competencia="2026-05", usar_ia=False)

    with engine.connect() as conn:
        viva = conn.execute(
            sa.select(db.transacoes.c.descricao, db.transacoes.c.categoria_id,
                      db.transacoes.c.subcategoria_id)
            .where(db.transacoes.c.ativo == sa.true())
        ).fetchall()
    assert len(viva) == 1
    assert viva[0].descricao == "EC *483920 SP"      # a do extrato prevaleceu
    assert viva[0].categoria_id == alimentacao        # herdou a classificação dela
    assert viva[0].subcategoria_id == sub


def test_critica_separa_o_que_bateu_do_que_so_existe_na_planilha(engine):
    repo.importar(engine, conta_id=4, lancamentos=_planilha_da_ro(),
                  arquivo="planilha_ro.xlsx", usuario="Rô", origem="planilha",
                  competencia="2026-05", usar_ia=False)
    repo.importar(engine, conta_id=4, lancamentos=_extrato_do_banco(),
                  arquivo="extrato_maio.pdf", usuario="André", origem="extrato",
                  competencia="2026-05", usar_ia=False)

    with engine.connect() as conn:
        critica = reconcile.criticar(conn)

    assert critica["conferidos"] == 3
    assert [i["descricao"] for i in critica["faltantes"]] == ["NETFLIX.COM"]
    assert [i["descricao"] for i in critica["so_planilha"]] == ["Feira da rua"]


def test_reenviar_o_mesmo_extrato_nao_dobra_nada(engine):
    repo.importar(engine, conta_id=4, lancamentos=_planilha_da_ro(),
                  arquivo="planilha_ro.xlsx", usuario="Rô", origem="planilha",
                  competencia="2026-05", usar_ia=False)
    repo.importar(engine, conta_id=4, lancamentos=_extrato_do_banco(),
                  arquivo="extrato_maio.pdf", usuario="André", origem="extrato",
                  competencia="2026-05", usar_ia=False)
    with engine.connect() as conn:
        antes = analytics.resumo(conn, competencia="2026-05")["despesas"]

    repetido = repo.importar(engine, conta_id=4, lancamentos=_extrato_do_banco(),
                             arquivo="extrato_maio.pdf", usuario="André",
                             origem="extrato", competencia="2026-05", usar_ia=False)

    assert repetido["importados"] == 0
    assert repetido["duplicados_exatos"] == 4
    with engine.connect() as conn:
        assert analytics.resumo(conn, competencia="2026-05")["despesas"] == antes
