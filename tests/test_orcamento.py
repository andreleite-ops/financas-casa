"""Testes de core.analytics.orcamento: realizado x meta por categoria."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db
from core.dedup import hash_lancamento
from core.texto import normalizar


def _categoria_id(conn, nome: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _conta_id(conn, nome: str = "Nubank Mastercard") -> int:
    return conn.execute(sa.select(db.contas.c.id).where(db.contas.c.nome == nome)).scalar_one()


def _inserir(conn, conta_id, dia, descricao, valor_centavos, categoria_id):
    descricao_norm = normalizar(descricao)
    conn.execute(
        sa.insert(db.transacoes).values(
            data=dia,
            competencia=dia.strftime("%Y-%m"),
            descricao=descricao,
            descricao_norm=descricao_norm,
            valor_centavos=valor_centavos,
            conta_id=conta_id,
            categoria_id=categoria_id,
            pessoa="Casal",
            status="manual",
            confianca=1.0,
            origem="extrato",
            hash_dedup=hash_lancamento(conta_id, dia, valor_centavos, descricao_norm),
            ativo=True,
        )
    )


def _linha(resultado, categoria_id):
    return next(l for l in resultado if l["categoria_id"] == categoria_id)


# --------------------------------------------------------------------------
# meta em % da renda -> valor absoluto
# --------------------------------------------------------------------------
def test_meta_percentual_vira_valor_absoluto_com_renda_base_explicita(engine, conn):
    conta_id = _conta_id(conn)
    alimentacao_id = _categoria_id(conn, "Alimentação")
    _inserir(conn, conta_id, date(2026, 7, 10), "SUPERMERCADO", -50_000, alimentacao_id)

    resultado = analytics.orcamento(conn, "2026-07", {alimentacao_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, alimentacao_id)

    assert linha["percentual"] == 20.0
    assert linha["meta"] == 200_000  # 20% de R$ 10.000,00


# --------------------------------------------------------------------------
# uso e estourou: realizado acima e abaixo da meta
# --------------------------------------------------------------------------
def test_uso_e_estourou_quando_realizado_maior_que_meta(engine, conn):
    conta_id = _conta_id(conn)
    alimentacao_id = _categoria_id(conn, "Alimentação")
    _inserir(conn, conta_id, date(2026, 7, 10), "SUPERMERCADO", -300_000, alimentacao_id)

    resultado = analytics.orcamento(conn, "2026-07", {alimentacao_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, alimentacao_id)

    assert linha["realizado"] == 300_000
    assert linha["meta"] == 200_000
    assert linha["uso"] == 150.0
    assert linha["estourou"] is True


def test_uso_e_estourou_quando_realizado_menor_que_meta(engine, conn):
    conta_id = _conta_id(conn)
    alimentacao_id = _categoria_id(conn, "Alimentação")
    _inserir(conn, conta_id, date(2026, 7, 10), "SUPERMERCADO", -100_000, alimentacao_id)

    resultado = analytics.orcamento(conn, "2026-07", {alimentacao_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, alimentacao_id)

    assert linha["realizado"] == 100_000
    assert linha["meta"] == 200_000
    assert linha["uso"] == 50.0
    assert linha["estourou"] is False


# --------------------------------------------------------------------------
# categoria sem meta definida
# --------------------------------------------------------------------------
def test_categoria_sem_meta_devolve_meta_zero_uso_none_sem_estourar(engine, conn):
    conta_id = _conta_id(conn)
    alimentacao_id = _categoria_id(conn, "Alimentação")
    _inserir(conn, conta_id, date(2026, 7, 10), "SUPERMERCADO", -100_000, alimentacao_id)

    # dicionario de metas nao traz a categoria -> percentual 0
    resultado = analytics.orcamento(conn, "2026-07", {}, renda_base=1_000_000)
    linha = _linha(resultado, alimentacao_id)

    assert linha["percentual"] == 0.0
    assert linha["meta"] == 0
    assert linha["uso"] is None
    assert linha["estourou"] is False


# --------------------------------------------------------------------------
# poupanca: aparece com o realizado vindo do resumo (aporte), mesmo natureza
# despesa por definicao de plano de contas
# --------------------------------------------------------------------------
def test_poupanca_aparece_com_realizado_vindo_do_resumo(engine, conn):
    conta_id = _conta_id(conn)
    poupanca_id = _categoria_id(conn, analytics.CATEGORIA_POUPANCA)
    _inserir(conn, conta_id, date(2026, 7, 15), "APLICACAO CDB", -80_000, poupanca_id)

    resultado = analytics.orcamento(conn, "2026-07", {poupanca_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, poupanca_id)

    assert linha["categoria"] == analytics.CATEGORIA_POUPANCA
    # aporte lancado como saida (-80_000) vira realizado positivo, como no resumo()
    assert linha["realizado"] == 80_000


def test_poupanca_sem_nenhum_lancamento_ainda_aparece_com_realizado_zero(engine, conn):
    poupanca_id = _categoria_id(conn, analytics.CATEGORIA_POUPANCA)

    resultado = analytics.orcamento(conn, "2026-07", {poupanca_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, poupanca_id)

    assert linha["realizado"] == 0
    assert linha["meta"] == 200_000


# --------------------------------------------------------------------------
# meta_e_piso / estourou: poupanca acima da meta nao e estouro; despesa e
# --------------------------------------------------------------------------
def test_poupanca_acima_da_meta_nao_estoura(engine, conn):
    conta_id = _conta_id(conn)
    poupanca_id = _categoria_id(conn, analytics.CATEGORIA_POUPANCA)
    _inserir(conn, conta_id, date(2026, 7, 15), "APLICACAO CDB", -500_000, poupanca_id)

    resultado = analytics.orcamento(conn, "2026-07", {poupanca_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, poupanca_id)

    assert linha["meta_e_piso"] is True
    assert linha["realizado"] > linha["meta"]  # 500_000 > 200_000
    assert linha["estourou"] is False


def test_despesa_acima_da_meta_estoura(engine, conn):
    conta_id = _conta_id(conn)
    alimentacao_id = _categoria_id(conn, "Alimentação")
    _inserir(conn, conta_id, date(2026, 7, 10), "SUPERMERCADO", -300_000, alimentacao_id)

    resultado = analytics.orcamento(conn, "2026-07", {alimentacao_id: 20.0}, renda_base=1_000_000)
    linha = _linha(resultado, alimentacao_id)

    assert linha["meta_e_piso"] is False
    assert linha["realizado"] > linha["meta"]
    assert linha["estourou"] is True
