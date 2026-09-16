"""Num cartão de crédito não existe receita.

O que entra num cartão é compra. O crédito que aparece na fatura é estorno ou o
pagamento da própria fatura — nenhum dos dois é renda da casa. Mas o arquivo
que o banco exporta escreve a compra com sinal positivo, e positivo, em todo o
resto do sistema, quer dizer dinheiro entrando.

Enquanto isso foi uma caixa a marcar na tela de upload, deu no que deu: a
fatura de setembro entrou inteira como receita, as despesas do cartão sumiram
do quadro do mês e a renda apareceu dobrada.

Estes testes são sobre a trava que não depende de ninguém lembrar: o gravador
declara "despesa" em todo lançamento de cartão que chegue sem natureza, e vira
o lote quando ele chega com a compra positiva. Cartão não gera receita, e
despesa é uma quantia positiva — quanto saiu.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from parsers.base import Lancamento


def _conta(engine, tipo: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=f"Conta {tipo}", tipo=tipo, titular="André",
                instituicao="Banco", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def _importar(engine, conta_id, lancamentos, competencia="2026-09"):
    return repo.importar(
        engine, lancamentos=lancamentos, conta_id=conta_id, arquivo="fatura.csv",
        origem="extrato", competencia=competencia, usuario="André", usar_ia=False,
    )


def _resumo(engine, competencia="2026-09") -> dict:
    with engine.connect() as conn:
        return analytics.resumo(conn, competencia=competencia)


def test_fatura_lida_com_o_sinal_trocado_entra_como_despesa_normal(engine):
    """O caso de setembro, reproduzido: compras lidas ao contrário.

    A primeira versão desta trava jogava o valor positivo para o lado da
    despesa e deixava o sinal como veio — despesa negativa, "feia de propósito"
    para servir de alarme. Quem usa leu esse número como o app quebrado, não
    como o arquivo invertido; alarme que precisa de explicação não é alarme.
    Agora o gravador vira o lote e a despesa sai normal, positiva.
    """
    cartao = _conta(engine, "cartao")
    resumo_importacao = _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="SUPERMERCADO", valor_centavos=54_010),
        Lancamento(data=date(2026, 9, 5), descricao="STREAMING", valor_centavos=3_990),
        Lancamento(data=date(2026, 9, 7), descricao="LIVRARIA", valor_centavos=40_540),
    ])
    assert resumo_importacao["sinal_corrigido"] is True

    resumo = _resumo(engine)
    assert resumo["receitas"] == 0, "cartão não gera receita em hipótese nenhuma"
    assert resumo["despesas"] == 98_540, "despesa é uma quantia positiva: quanto saiu"


def test_fatura_lida_certo_soma_nas_despesas(engine):
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="SUPERMERCADO", valor_centavos=-54_010),
        Lancamento(data=date(2026, 9, 5), descricao="STREAMING", valor_centavos=-3_990),
    ])

    resumo = _resumo(engine)
    assert resumo["receitas"] == 0
    assert resumo["despesas"] == 58_000


def test_estorno_no_cartao_abate_a_despesa_em_vez_de_virar_renda(engine):
    """Crédito em fatura é dinheiro que volta de um gasto, não dinheiro novo."""
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 1), descricao="LOJA DE MOVEIS", valor_centavos=-100_000),
        Lancamento(data=date(2026, 9, 9), descricao="ESTORNO LOJA DE MOVEIS",
                   valor_centavos=30_000),
    ])

    resumo = _resumo(engine)
    assert resumo["receitas"] == 0
    assert resumo["despesas"] == 70_000, "o estorno abate o gasto"


def test_a_conta_corrente_continua_tendo_os_dois_lados(engine):
    """A trava é do cartão. Em conta corrente, positivo é receita mesmo."""
    corrente = _conta(engine, "corrente")
    _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 1), descricao="SALARIO", valor_centavos=800_000),
        Lancamento(data=date(2026, 9, 3), descricao="ALUGUEL", valor_centavos=-250_000),
    ])

    resumo = _resumo(engine)
    assert resumo["receitas"] == 800_000
    assert resumo["despesas"] == 250_000


def test_natureza_declarada_pelo_arquivo_continua_mandando(engine):
    """Se a origem disse de que lado está, a trava não atropela."""
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 2), descricao="CASHBACK", valor_centavos=5_000,
                   natureza_hint="receita"),
    ])

    with engine.connect() as conn:
        natureza = conn.execute(
            sa.select(db.transacoes.c.natureza).where(db.transacoes.c.descricao == "CASHBACK")
        ).scalar_one()
    assert natureza == "receita"


def test_todo_lancamento_de_cartao_nasce_com_natureza(engine):
    """A trava é no gravador: vale para qualquer caminho que chame `importar`.

    É o que garante que ela não dependa da tela — nem do leitor do banco, nem
    do mapeamento manual de colunas, nem de quem lembrou de marcar a caixa.
    """
    cartao = _conta(engine, "cartao")
    _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 4), descricao=f"COMPRA {i}", valor_centavos=-1_000 * i)
        for i in range(1, 6)
    ])

    with engine.connect() as conn:
        naturezas = {
            linha.natureza
            for linha in conn.execute(
                sa.select(db.transacoes.c.natureza).where(db.transacoes.c.conta_id == cartao)
            )
        }
    assert naturezas == {"despesa"}
