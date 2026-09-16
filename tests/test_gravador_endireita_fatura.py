"""A trava que fecha o assunto: no gravador, no único lugar por onde tudo passa.

A mesma fatura passou três vezes com a compra positiva, cada vez por um caminho
que a proteção anterior não olhava: a caixa desmarcada, a conta corrente, o
leitor do banco fixando o sinal, a coluna "Tipo" com "à vista" dentro. Cada
conserto tapou um caminho e o próximo estava aberto.

Aqui a pergunta é sobre o lote que está entrando numa conta de cartão — não
sobre o arquivo, o leitor, a coluna ou a tela. Se chegou quase todo positivo,
veio invertido, e é virado antes de gravar. E para a fatura que já está no
banco errada, o reparo vira o sinal no lugar, sem reimportar e sem perder a
classificação já feita.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from parsers.base import Lancamento, endireitar, fatura_invertida


def _conta(engine, tipo: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=f"Conta {tipo}", tipo=tipo, titular="André",
                instituicao="Banco", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


# a fatura como chegou nas três vezes: compra positiva, pagamento negativo
FATURA_INVERTIDA = [
    Lancamento(data=date(2026, 9, 1), descricao="SUPERMERCADO", valor_centavos=54_010),
    Lancamento(data=date(2026, 9, 5), descricao="POSTO", valor_centavos=21_000),
    Lancamento(data=date(2026, 9, 7), descricao="DROGARIA", valor_centavos=4_240),
    Lancamento(data=date(2026, 9, 9), descricao="DL*UBERRIDES", valor_centavos=1_500),
    Lancamento(data=date(2026, 9, 10), descricao="PAGAMENTO RECEBIDO", valor_centavos=-80_750),
]


def _importar(engine, conta_id, lancamentos):
    return repo.importar(
        engine, lancamentos=lancamentos, conta_id=conta_id, arquivo="fatura.csv",
        origem="extrato", competencia="2026-09", usuario="André", usar_ia=False,
    )


def _resumo(engine) -> dict:
    with engine.connect() as conn:
        return analytics.resumo(conn, competencia="2026-09")


def test_reconhece_o_lote_invertido_pela_proporcao():
    assert fatura_invertida(FATURA_INVERTIDA)
    assert not fatura_invertida(endireitar(FATURA_INVERTIDA))
    assert not fatura_invertida([])


def test_endireitar_devolve_copias_com_o_sinal_do_sistema():
    virados = endireitar(FATURA_INVERTIDA)
    assert [lan.valor_centavos for lan in virados] == [-54_010, -21_000, -4_240, -1_500, 80_750]
    # o lote original é de quem chamou
    assert FATURA_INVERTIDA[0].valor_centavos == 54_010


def test_fatura_invertida_num_cartao_e_virada_na_gravacao(engine):
    """O caso das três vezes, resolvido onde nenhum caminho escapa."""
    cartao = _conta(engine, "cartao")
    resumo = _importar(engine, cartao, FATURA_INVERTIDA)

    assert resumo["sinal_corrigido"] is True
    numeros = _resumo(engine)
    assert numeros["despesas"] == 80_750, "as quatro compras, positivas, do lado da despesa"
    assert numeros["receitas"] == 0
    # o pagamento da fatura é transferência, classificado pelas regras
    assert numeros["transferencias"] == 80_750


def test_fatura_que_ja_veio_certa_nao_e_tocada(engine):
    cartao = _conta(engine, "cartao")
    resumo = _importar(engine, cartao, endireitar(FATURA_INVERTIDA))

    assert resumo["sinal_corrigido"] is False
    assert _resumo(engine)["despesas"] == 80_750


def test_conta_corrente_nao_passa_pela_trava(engine):
    """Em conta corrente, positivo é receita mesmo — e a maioria pode ser."""
    corrente = _conta(engine, "corrente")
    resumo = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 1), descricao="PIX PACIENTE A", valor_centavos=30_000),
        Lancamento(data=date(2026, 9, 3), descricao="PIX PACIENTE B", valor_centavos=30_000),
        Lancamento(data=date(2026, 9, 5), descricao="PIX PACIENTE C", valor_centavos=45_000),
        Lancamento(data=date(2026, 9, 8), descricao="TARIFA", valor_centavos=-1_200),
    ])
    assert resumo["sinal_corrigido"] is False
    assert _resumo(engine)["receitas"] == 105_000


def _gravar_invertida_como_cartao(engine, lancamentos=None):
    """O estado que a casa viu: fatura invertida, ja no banco, na conta do cartao.

    Importa numa conta corrente (onde o gravador nao corrige) e depois muda o
    tipo da conta para cartao, como quem descobre o erro depois.
    """
    conta = _conta(engine, "corrente")
    resumo = _importar(engine, conta, lancamentos or FATURA_INVERTIDA)
    with engine.begin() as conn:
        conn.execute(sa.update(db.contas).where(db.contas.c.id == conta).values(tipo="cartao"))
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.upload_id == resumo["upload_id"],
                   db.transacoes.c.valor_centavos > 0)
            .values(natureza="despesa", categoria_id=None)
        )
    return resumo["upload_id"]


COMPRAS = 54_010 + 21_000 + 4_240 + 1_500


def test_reparo_corrige_a_fatura_gravada_invertida(engine):
    upload_id = _gravar_invertida_como_cartao(engine)
    assert _resumo(engine)["despesas"] == -COMPRAS, "o ponto de partida e a despesa negativa"

    assert repo.endireitar_upload(engine, upload_id) == 5
    assert _resumo(engine)["despesas"] == COMPRAS


def test_reparo_e_idempotente(engine):
    """Clicar de novo nao desfaz. Foi assim que a fatura voltou ao erro."""
    upload_id = _gravar_invertida_como_cartao(engine)
    repo.endireitar_upload(engine, upload_id)
    certo = _resumo(engine)["despesas"]
    assert certo == COMPRAS

    for _ in range(3):
        assert repo.endireitar_upload(engine, upload_id) == 0
    assert _resumo(engine)["despesas"] == certo


def test_reparo_nao_toca_fatura_que_ja_esta_certa(engine):
    cartao = _conta(engine, "cartao")
    resumo = _importar(engine, cartao, endireitar(FATURA_INVERTIDA))
    assert repo.endireitar_upload(engine, resumo["upload_id"]) == 0
    assert _resumo(engine)["despesas"] == COMPRAS


def test_reparo_nao_toca_conta_corrente(engine):
    """Em conta corrente positivo e receita: o reparo nao tem o que dizer."""
    corrente = _conta(engine, "corrente")
    resumo = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 1 + i), descricao=f"PIX {i}", valor_centavos=30_000)
        for i in range(5)
    ])
    assert repo.endireitar_upload(engine, resumo["upload_id"]) == 0
    assert _resumo(engine)["receitas"] == 150_000


def test_reparo_acompanha_o_hash_de_duplicidade(engine):
    """O valor faz parte do hash: corrigir o sinal sem corrigir o hash quebraria
    a deteccao de duplicidade na proxima importacao."""
    from core.dedup import hash_lancamento

    upload_id = _gravar_invertida_como_cartao(engine)
    repo.endireitar_upload(engine, upload_id)

    with engine.connect() as conn:
        for linha in conn.execute(
            sa.select(db.transacoes).where(db.transacoes.c.upload_id == upload_id)
        ):
            assert linha.hash_dedup == hash_lancamento(
                linha.conta_id, linha.data, linha.valor_centavos, linha.descricao_norm
            )


def test_inicializacao_corrige_sozinha_o_que_estiver_invertido(engine):
    """Ninguem precisa clicar: a subida do app passa por toda fatura gravada."""
    from core import seed

    invertida = _gravar_invertida_como_cartao(engine)
    cartao_certo = _conta(engine, "cartao")
    certa = _importar(engine, cartao_certo, endireitar(FATURA_INVERTIDA))["upload_id"]
    assert _resumo(engine)["despesas"] == -COMPRAS + COMPRAS

    corrigidos = repo.endireitar_faturas_gravadas(engine)
    assert [c["upload_id"] for c in corrigidos] == [invertida]
    assert _resumo(engine)["despesas"] == COMPRAS * 2

    # e rodar de novo, como toda inicializacao vai rodar, nao mexe em nada
    assert repo.endireitar_faturas_gravadas(engine) == []
    seed.semear(engine)
    assert _resumo(engine)["despesas"] == COMPRAS * 2
