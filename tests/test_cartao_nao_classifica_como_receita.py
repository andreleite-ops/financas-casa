"""Cartão não gera receita — e isso vale para as três camadas e para a mão.

A natureza da linha de cartão é decidida ANTES de classificar, e é ela que as
camadas usam como guarda. Decidida depois, a guarda olhava o sinal: a fatura
que entrou positiva na primeira vez teve compras classificadas como renda,
virou memória, e a memória repetia a cada fatura seguinte — os 24 lançamentos
do cartão em "Outras Receitas" que a sentinela pegou.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from core import analytics, classify, db, repo
from parsers.base import Lancamento


def _conta(engine, tipo, nome=None):
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=nome or f"Conta {tipo}", tipo=tipo, titular="André",
                instituicao="Banco", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def _categoria(conn, nome):
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _importar(engine, conta_id, lancamentos):
    return repo.importar(
        engine, lancamentos=lancamentos, conta_id=conta_id, arquivo="a.csv",
        origem="extrato", competencia="2026-09", usuario="André", usar_ia=False,
    )


def _linha(engine, descricao):
    with engine.connect() as conn:
        return conn.execute(
            sa.select(db.transacoes.c.categoria_id, db.transacoes.c.status,
                      db.transacoes.c.natureza, db.transacoes.c.valor_centavos)
            .where(db.transacoes.c.descricao == descricao)
        ).one()


def _memoria_de_receita(engine, descricao):
    """A memória errada: alguém classificou esta compra como receita um dia."""
    with engine.begin() as conn:
        outras = _categoria(conn, "Outras Receitas")
        assert classify.aprender(conn, descricao, outras, None, "André")
        return outras


def test_memoria_de_receita_nao_pega_compra_de_cartao(engine):
    """O caso dos 14 lançamentos, com a fatura entrando invertida de novo."""
    outras = _memoria_de_receita(engine, "DECO SKIN")
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 13), descricao="DECO SKIN - Parcela 1/2", valor_centavos=8_495),
        Lancamento(data=date(2026, 9, 13), descricao="DROGARIA", valor_centavos=4_240),
        Lancamento(data=date(2026, 9, 13), descricao="DL*UBERRIDES", valor_centavos=1_500),
    ])
    linha = _linha(engine, "DECO SKIN - Parcela 1/2")
    assert linha.categoria_id != outras
    assert linha.natureza == "despesa"
    assert linha.valor_centavos == -8_495


def test_a_guarda_e_pela_natureza_nao_pelo_sinal(engine):
    """Lote pequeno demais para endireitar: a linha fica positiva — e mesmo
    assim não entra em receita, porque a natureza foi decidida antes."""
    outras = _memoria_de_receita(engine, "DECO SKIN")
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 13), descricao="DECO SKIN - Parcela 1/2", valor_centavos=8_495),
    ])
    linha = _linha(engine, "DECO SKIN - Parcela 1/2")
    assert linha.valor_centavos == 8_495          # não foi endireitada (uma linha só)
    assert linha.natureza == "despesa"
    assert linha.categoria_id != outras, "a memória de receita foi recusada"


def test_a_mesma_memoria_continua_valendo_em_conta_corrente(engine):
    outras = _memoria_de_receita(engine, "DECO SKIN")
    corrente = _conta(engine, "corrente")
    _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 13), descricao="DECO SKIN", valor_centavos=8_495),
    ])
    assert _linha(engine, "DECO SKIN").categoria_id == outras


def test_salvar_a_mao_recusa_receita_em_cartao(engine):
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 9), descricao="ESTORNO POSTO", valor_centavos=5_000),
        Lancamento(data=date(2026, 9, 1), descricao="POSTO", valor_centavos=-21_000),
        Lancamento(data=date(2026, 9, 2), descricao="LOJA", valor_centavos=-10_000),
    ])
    with engine.connect() as conn:
        outras = _categoria(conn, "Outras Receitas")
        transporte = _categoria(conn, "Transporte")
        estorno_id = conn.execute(
            sa.select(db.transacoes.c.id).where(db.transacoes.c.descricao == "ESTORNO POSTO")
        ).scalar_one()

    with pytest.raises(ValueError, match="não gera receita"):
        repo.reclassificar(engine, estorno_id, categoria_id=outras, subcategoria_id=None,
                           pessoa=None, usuario="André")
    # e para a categoria do gasto que ele devolve, vai
    repo.reclassificar(engine, estorno_id, categoria_id=transporte, subcategoria_id=None,
                       pessoa=None, usuario="André")
    assert _linha(engine, "ESTORNO POSTO").categoria_id == transporte


def test_fila_diz_o_tipo_da_conta(engine):
    cartao = _conta(engine, "cartao", nome="Cartão X")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="XPTO SEM REGRA", valor_centavos=-1_000),
    ])
    with engine.connect() as conn:
        fila = repo.fila_pendentes(conn, limite=50)
    item = next(i for i in fila if i["descricao"] == "XPTO SEM REGRA")
    assert item["tipo_conta"] == "cartao"


def test_varredura_devolve_para_a_fila_o_que_esta_em_receita_no_cartao(engine):
    """As 24 linhas que a sentinela pegou: na subida, voltam para a fila."""
    cartao = _conta(engine, "cartao")
    corrente = _conta(engine, "corrente")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="COMPRA A", valor_centavos=-1_000),
        Lancamento(data=date(2026, 9, 2), descricao="COMPRA B", valor_centavos=-1_000),
        Lancamento(data=date(2026, 9, 3), descricao="COMPRA C", valor_centavos=-1_000),
    ])
    _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 5), descricao="SALARIO X", valor_centavos=800_000),
    ])
    with engine.begin() as conn:
        outras = _categoria(conn, "Outras Receitas")
        # o estado errado, gravado por fora: compras de cartão em receita, e uma
        # receita legítima da conta corrente na mesma categoria
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.descricao.in_(["COMPRA A", "COMPRA B", "SALARIO X"]))
            .values(categoria_id=outras, status="manual")
        )
    with engine.connect() as conn:
        assert len(analytics.receita_em_cartao(conn)) == 1

    assert repo.desclassificar_receita_em_cartao(engine) == 2
    assert _linha(engine, "COMPRA A").status == "pendente"
    assert _linha(engine, "COMPRA A").categoria_id is None
    assert _linha(engine, "SALARIO X").categoria_id == outras, "conta corrente não é tocada"
    with engine.connect() as conn:
        assert analytics.receita_em_cartao(conn) == []
    # idempotente
    assert repo.desclassificar_receita_em_cartao(engine) == 0
