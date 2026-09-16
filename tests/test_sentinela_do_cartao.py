"""Como saber se o erro voltar: o app olha sozinho.

Cartão não gera receita. Se alguma linha de cartão está somando do lado da
renda — em qualquer mês —, a Visão Geral diz de qual cartão e quanto, na
abertura. É a resposta para "como terei certeza": não é preciso desconfiar de
um número; o aviso aparece antes.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from parsers.base import Lancamento


def _conta(engine, nome, tipo):
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=nome, tipo=tipo, titular="André", instituicao="Banco",
                parser="generico", ativa=True,
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


def _sentinela(engine):
    with engine.connect() as conn:
        return analytics.receita_em_cartao(conn)


def test_fatura_certa_nao_acende_nada(engine):
    cartao = _conta(engine, "Cartão A", "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="SUPERMERCADO", valor_centavos=-54_010),
        Lancamento(data=date(2026, 9, 5), descricao="POSTO", valor_centavos=-21_000),
        Lancamento(data=date(2026, 9, 9), descricao="ESTORNO POSTO", valor_centavos=5_000),
        Lancamento(data=date(2026, 9, 10), descricao="PAGAMENTO RECEBIDO", valor_centavos=-70_010 * -1),
    ])
    assert _sentinela(engine) == []


def test_estorno_classificado_como_receita_acende(engine, conn):
    """O caso dos R$ 511,88 em 'Outras Receitas' do cartão."""
    cartao = _conta(engine, "Cartão A", "cartao")
    resumo = _importar(engine, cartao, [
        Lancamento(data=date(2026, 8, 20), descricao="ESTORNO ANUIDADE", valor_centavos=51_188),
        Lancamento(data=date(2026, 8, 21), descricao="LOJA", valor_centavos=-100_000),
        Lancamento(data=date(2026, 8, 22), descricao="LOJA 2", valor_centavos=-100_000),
    ])
    with engine.begin() as escrita:
        outras = _categoria(escrita, "Outras Receitas")
        escrita.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.descricao == "ESTORNO ANUIDADE")
            .values(categoria_id=outras)
        )

    achados = _sentinela(engine)
    assert len(achados) == 1
    assert achados[0]["conta"] == "Cartão A"
    assert achados[0]["total"] == 51_188
    assert achados[0]["quantos"] == 1


def test_linha_de_cartao_gravada_por_fora_do_gravador_tambem_acende(engine):
    """A sentinela não confia na trava: olha o banco como ele está.

    Se alguém inserir uma linha de cartão positiva sem natureza por qualquer
    caminho — script, migração, mão no banco —, ela conta como receita pelo
    sinal e a sentinela acusa. É a garantia de que o aviso não depende de o
    gravador ter funcionado.
    """
    from core.dedup import hash_lancamento
    from core.texto import normalizar

    cartao = _conta(engine, "Cartão B", "cartao")
    with engine.begin() as escrita:
        escrita.execute(
            sa.insert(db.transacoes).values(
                data=date(2026, 7, 3), competencia="2026-07", descricao="COMPRA POSITIVA",
                descricao_norm=normalizar("COMPRA POSITIVA"), valor_centavos=9_900,
                conta_id=cartao, pessoa="Casal", status="pendente", origem="extrato",
                hash_dedup=hash_lancamento(cartao, date(2026, 7, 3), 9_900,
                                           normalizar("COMPRA POSITIVA")),
                ativo=True,
            )
        )
    achados = _sentinela(engine)
    assert [(a["conta"], a["competencia"], a["total"]) for a in achados] == [
        ("Cartão B", "2026-07", 9_900)
    ]


def test_conta_corrente_com_receita_e_normal(engine):
    corrente = _conta(engine, "Conta C/C", "corrente")
    _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 5), descricao="SALARIO", valor_centavos=800_000),
    ])
    assert _sentinela(engine) == []


def test_pagamento_da_fatura_e_transferencia_e_nao_acende(engine):
    """O crédito legítimo do cartão: classificado como transferência, fica de fora."""
    cartao = _conta(engine, "Cartão A", "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="LOJA", valor_centavos=-50_000),
        Lancamento(data=date(2026, 9, 2), descricao="LOJA 2", valor_centavos=-50_000),
        Lancamento(data=date(2026, 9, 10), descricao="PAGAMENTO RECEBIDO", valor_centavos=100_000),
    ])
    with engine.connect() as leitura:
        transf = leitura.execute(
            sa.select(db.categorias.c.nome)
            .select_from(db.transacoes.join(db.categorias,
                                            db.transacoes.c.categoria_id == db.categorias.c.id))
            .where(db.transacoes.c.descricao == "PAGAMENTO RECEBIDO")
        ).scalar()
    assert transf == analytics.CATEGORIA_TRANSFERENCIA, "a regra do pagamento tem de pegar"
    assert _sentinela(engine) == []
