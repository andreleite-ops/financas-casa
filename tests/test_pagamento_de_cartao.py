"""O pagamento da fatura nunca é despesa — reconhecido pelo que o sistema sabe.

As compras já são despesa na fatura. O débito que as paga é dinheiro mudando
de bolso, e somado como despesa o mês paga o cartão duas vezes. Regra de texto
não resolve em definitivo: cada banco escreve de um jeito. O que resolve são
o cadastro (quais cartões existem) e o histórico (quanto cada fatura deu).
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, cartoes, db, repo, seed
from parsers.base import Lancamento


def _conta(engine, nome, tipo, instituicao):
    with engine.begin() as conn:
        return conn.execute(sa.insert(db.contas).values(
            nome=nome, tipo=tipo, titular="André", instituicao=instituicao,
            parser="generico", ativa=True,
        )).inserted_primary_key[0]


def _importar(engine, conta_id, lancamentos, competencia=None):
    return repo.importar(
        engine, conta_id=conta_id, arquivo="a.pdf", origem="extrato", usuario="André",
        usar_ia=False, competencia=competencia, lancamentos=[Lancamento(**l) for l in lancamentos],
    )


def _resumo(engine, competencia):
    with engine.connect() as conn:
        return analytics.resumo(conn, competencia=competencia)


def _fatura_nubank(engine, cartao):
    """A fatura de agosto: R$ 32.212,32 de compras, já importadas."""
    _importar(engine, cartao, [
        dict(data=date(2026, 7, 20), descricao="SUPERMERCADO", valor_centavos=-2_000_000, competencia="2026-08"),
        dict(data=date(2026, 8, 2), descricao="POSTO", valor_centavos=-1_221_232, competencia="2026-08"),
    ], competencia="2026-08")


def test_debito_com_o_valor_da_fatura_e_transferencia_seja_qual_for_o_texto(engine):
    cartao = _conta(engine, "Nubank teste", "cartao", "Nubank")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    _fatura_nubank(engine, cartao)

    _importar(engine, corrente, [
        dict(data=date(2026, 8, 10), descricao="DEB AUTOMATICO 0001 REF 987654", valor_centavos=-3_221_232),
        dict(data=date(2026, 8, 11), descricao="SUPERMERCADO Y", valor_centavos=-30_000),
    ])
    agosto = _resumo(engine, "2026-08")
    assert agosto["despesas"] == 3_221_232 + 30_000, "as compras uma vez, o mercado uma vez"
    assert agosto["transferencias"] == -3_221_232


def test_debito_que_cita_o_emissor_cadastrado_e_transferencia(engine):
    _conta(engine, "XP teste", "cartao", "XP")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 12), descricao="PAGTO ELETRON COBRANCA XP INVESTIMENTOS", valor_centavos=-812_345),
    ])
    assert _resumo(engine, "2026-08")["despesas"] == 0


def test_pix_para_alguem_que_tem_conta_no_emissor_nao_e_pagamento(engine):
    _conta(engine, "Nubank teste", "cartao", "Nubank")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 12), descricao="PIX TRANSF FULANO NU PAGAMENTOS", valor_centavos=-50_000),
    ])
    assert _resumo(engine, "2026-08")["despesas"] == 50_000


def test_a_folga_e_pequena_e_o_mes_vizinho_conta(engine):
    """A fatura fecha num mês e é paga no seguinte; juros de um dia mudam centavos."""
    cartao = _conta(engine, "Nubank teste", "cartao", "Nubank")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    _fatura_nubank(engine, cartao)
    _importar(engine, corrente, [
        dict(data=date(2026, 9, 3), descricao="PAGAMENTO", valor_centavos=-3_221_300),   # 68 centavos a mais
        dict(data=date(2026, 9, 4), descricao="OUTRA COISA", valor_centavos=-3_000_000),  # longe demais
    ])
    setembro = _resumo(engine, "2026-09")
    assert setembro["transferencias"] == -3_221_300
    assert setembro["despesas"] == 3_000_000


def test_varredura_na_subida_conserta_o_que_ja_estava_gravado(engine):
    """O pagamento que entrou antes do detector existir vira transferência no reboot."""
    cartao = _conta(engine, "Nubank teste", "cartao", "Nubank")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 10), descricao="DEB AUTOMATICO 0001", valor_centavos=-3_221_232),
    ])
    assert _resumo(engine, "2026-08")["despesas"] == 3_221_232, "sem fatura, nada a reconhecer"

    _fatura_nubank(engine, cartao)          # a fatura chega depois
    assert repo.marcar_pagamentos_de_cartao(engine) == 1
    assert _resumo(engine, "2026-08")["despesas"] == 3_221_232, "só as compras"
    assert _resumo(engine, "2026-08")["transferencias"] == -3_221_232
    assert repo.marcar_pagamentos_de_cartao(engine) == 0
    seed.semear(engine)
    assert _resumo(engine, "2026-08")["transferencias"] == -3_221_232


def test_reconhecer_explica_o_motivo():
    emissores = [{"id": 1, "nome": "Nubank Mastercard", "tokens": ["NUBANK", "NU PAGAMENTOS"]}]
    totais = {(1, "2026-08"): 3_221_232}
    assert cartoes.reconhecer("QUALQUER TEXTO", -3_221_232, "2026-08",
                              emissores_cadastrados=emissores, totais=totais) \
        == "pagamento da fatura Nubank Mastercard de 2026-08"
    assert cartoes.reconhecer("DEB NUBANK", -100, "2026-08",
                              emissores_cadastrados=emissores, totais=totais) \
        == "pagamento de cartão Nubank Mastercard"
    assert cartoes.reconhecer("PGTO FATURA CARTAO", -100, "2026-08",
                              emissores_cadastrados=emissores, totais={}) \
        == "pagamento de fatura de cartão"
    assert cartoes.reconhecer("MERCADO", -100, "2026-08",
                              emissores_cadastrados=emissores, totais=totais) is None
    assert cartoes.reconhecer("QUALQUER", 3_221_232, "2026-08",
                              emissores_cadastrados=emissores, totais=totais) is None


def test_a_transicao_da_planilha_o_pagamento_recebido_da_fatura_seguinte_basta(engine):
    """Julho está na planilha, não vai ser importado. O pagamento de julho está
    no Bradesco de agosto — e a fatura de agosto imprime "Pagamento recebido"
    com esse valor. É o que fecha a conta sem a fatura de julho."""
    cartao = _conta(engine, "Nubank teste", "cartao", "Nubank")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    # a fatura de agosto (CSV): compras de agosto e o pagamento da de julho
    _importar(engine, cartao, [
        dict(data=date(2026, 8, 2), descricao="POSTO", valor_centavos=-50_000, competencia="2026-08"),
        dict(data=date(2026, 8, 12), descricao="Pagamento recebido", valor_centavos=2_987_654, competencia="2026-08"),
    ], competencia="2026-08")
    # o Bradesco de agosto: o boleto, com texto generico, um dia antes
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 11), descricao="PAGTO ELETRON COBRANCA 00123", valor_centavos=-2_987_654),
        dict(data=date(2026, 8, 11), descricao="PAGTO ELETRON COBRANCA 00777", valor_centavos=-2_987_654 + 500_000),
    ])
    agosto = _resumo(engine, "2026-08")
    assert agosto["despesas"] == 50_000 + (2_987_654 - 500_000), "o boleto do cartão saiu; o outro boleto fica"
    assert agosto["transferencias"] == 2_987_654 - 2_987_654


def test_a_varredura_pega_o_pagamento_recebido_ja_gravado(engine):
    cartao = _conta(engine, "Nubank teste", "cartao", "Nubank")
    corrente = _conta(engine, "Bradesco teste", "corrente", "Bradesco")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 11), descricao="PAGTO ELETRON COBRANCA 00123", valor_centavos=-2_987_654),
    ])
    assert _resumo(engine, "2026-08")["despesas"] == 2_987_654
    _importar(engine, cartao, [
        dict(data=date(2026, 8, 12), descricao="Pagamento recebido", valor_centavos=2_987_654, competencia="2026-08"),
        dict(data=date(2026, 8, 2), descricao="POSTO", valor_centavos=-50_000, competencia="2026-08"),
    ], competencia="2026-08")
    assert repo.marcar_pagamentos_de_cartao(engine) == 1
    assert _resumo(engine, "2026-08")["despesas"] == 50_000
