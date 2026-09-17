"""PIX ou TED entre contas da casa não é renda nem gasto.

"PIX RECEBIDO REM: Andre Luiz Rodrigues" é o André mandando para si mesmo de
outra conta. Sem reconhecer, R$ 37 mil de TED do André para o André entraram
como receita de agosto.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from parsers.base import Lancamento


def _corrente(engine, titular="André"):
    with engine.begin() as conn:
        return conn.execute(sa.insert(db.contas).values(
            nome=f"Conta {titular}", tipo="corrente", titular=titular, instituicao="Banco",
            parser="generico", ativa=True,
        )).inserted_primary_key[0]


def _importar(engine, conta_id, lancamentos):
    return repo.importar(
        engine, conta_id=conta_id, arquivo="a.pdf", origem="extrato", usuario="André",
        usar_ia=False, lancamentos=[Lancamento(**l) for l in lancamentos],
    )


def test_reconhece_quem_da_casa_esta_na_outra_ponta():
    assert repo.contraparte_da_casa("PIX RECEBIDO REM: Andre Luiz Rodrigues") == "André"
    assert repo.contraparte_da_casa("TED-TRANSF ELET DISPON REMET.ANDRE LUIZ RODRIGUES") == "André"
    assert repo.contraparte_da_casa("PIX ENVIADO DES: André Luiz Rodrigues") == "André"
    assert repo.contraparte_da_casa("PIX TRANSF RO 03/08") == "Rô"
    # a paciente Andrea não é o André: o primeiro nome é token inteiro
    assert repo.contraparte_da_casa("PIX TRANSF ANDREA") is None
    assert repo.contraparte_da_casa("PIX ENVIADO DES: SOFIA FULANA") is None
    assert repo.contraparte_da_casa("PAGTO ELETRON COBRANCA NU PAGAMENTOS SA") is None
    # os apelidos de uma letra ("R", "C") não podem casar por prefixo
    assert repo.contraparte_da_casa("PIX TRANSF RAFAEL") is None
    assert repo.contraparte_da_casa("PIX QR CODE DINAMICO DES: CAIXA LOTERIAS S A") is None
    assert repo.contraparte_da_casa("PIX TRANSF RICARDO") is None


def test_credito_vindo_de_instituicao_financeira_e_resgate():
    assert repo.resgate_de_investimento("PIX RECEBIDO REM: BANCO INTER SA", 1_194_457)
    assert repo.resgate_de_investimento("TED RECEBIDA XP INVESTIMENTOS CCTVM", 500_000)
    assert not repo.resgate_de_investimento("PIX RECEBIDO REM: TAG PARTNERS LTDA.", 2_059_621)
    assert not repo.resgate_de_investimento("PIX TRANSF ANDREA", 84_000)
    assert not repo.resgate_de_investimento("PIX ENVIADO DES: BANCO XP S.A", -100_000), "só crédito"


def test_resgate_nao_e_receita(engine):
    corrente = _corrente(engine)
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 3), descricao="PIX RECEBIDO REM: BANCO INTER SA", valor_centavos=1_194_457),
        dict(data=date(2026, 8, 7), descricao="PIX RECEBIDO REM: TAG PARTNERS LTDA.", valor_centavos=2_059_621),
    ])
    with engine.connect() as conn:
        agosto = analytics.resumo(conn, competencia="2026-08")
    assert agosto["receitas"] == 2_059_621
    assert agosto["transferencias"] == 1_194_457


def test_ted_para_si_mesmo_nao_e_receita(engine):
    corrente = _corrente(engine)
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 21), descricao="TED-TRANSF ELET DISPON REMET.ANDRE LUIZ", valor_centavos=3_400_000),
        dict(data=date(2026, 8, 7), descricao="PIX RECEBIDO REM: EMPRESA LTDA.", valor_centavos=2_059_621),
        dict(data=date(2026, 8, 17), descricao="PIX ENVIADO DES: André Luiz", valor_centavos=-913_311),
        dict(data=date(2026, 8, 19), descricao="PIX ENVIADO DES: SOFIA FULANA", valor_centavos=-20_000),
    ])
    with engine.connect() as conn:
        agosto = analytics.resumo(conn, competencia="2026-08")
    assert agosto["receitas"] == 2_059_621, "só o pró-labore é renda"
    assert agosto["despesas"] == 20_000, "só o PIX para a filha é gasto"
    assert agosto["transferencias"] == 3_400_000 - 913_311


def test_varredura_marca_o_que_ja_estava_gravado(engine):
    from core.dedup import hash_lancamento
    from core.texto import normalizar

    corrente = _corrente(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(db.transacoes).values(
            data=date(2026, 8, 21), competencia="2026-08",
            descricao="TED-TRANSF ELET DISPON REMET.ANDRE LUIZ",
            descricao_norm=normalizar("TED-TRANSF ELET DISPON REMET.ANDRE LUIZ"),
            valor_centavos=3_400_000, conta_id=corrente, pessoa="André", status="pendente",
            origem="extrato", ativo=True,
            hash_dedup=hash_lancamento(corrente, date(2026, 8, 21), 3_400_000,
                                       normalizar("TED-TRANSF ELET DISPON REMET.ANDRE LUIZ")),
        ))
    assert repo.marcar_transferencias_proprias(engine) == 1
    assert repo.marcar_transferencias_proprias(engine) == 0
    with engine.connect() as conn:
        assert analytics.resumo(conn, competencia="2026-08")["receitas"] == 0
