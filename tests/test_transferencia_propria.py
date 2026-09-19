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


def test_banco_como_remetente_nao_decide_nada(engine):
    """"PIX RECEBIDO REM: BANCO INTER SA" foi o aluguel de um imóvel, não um
    resgate. O banco na outra ponta não prova de onde veio o dinheiro: a linha
    fica para o dono classificar, em vez de sumir em Transferências."""
    assert repo._transferencia_propria("PIX RECEBIDO REM: BANCO INTER SA", 1_194_457) is None
    assert repo._transferencia_propria("RESGATE CDB AUTOMATICO", 500_000) is not None
    corrente = _corrente(engine)
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 3), descricao="PIX RECEBIDO REM: BANCO INTER SA", valor_centavos=1_194_457),
        dict(data=date(2026, 8, 7), descricao="PIX RECEBIDO REM: TAG PARTNERS LTDA.", valor_centavos=2_059_621),
    ])
    with engine.connect() as conn:
        agosto = analytics.resumo(conn, competencia="2026-08")
        inter = conn.execute(sa.select(db.transacoes).where(
            db.transacoes.c.descricao.like("%INTER%"))).mappings().one()
    assert agosto["transferencias"] == 0
    assert inter["categoria_id"] is None or inter["status"] != "auto_regra"


def test_transferencia_propria_nao_realiza_receita_prevista(engine):
    """A TED da Rô para ela mesma casava, por mês e ordem de grandeza, com a
    receita prevista dela, herdava a categoria e entrava como renda — a mão,
    blindada. Dinheiro da casa não realiza receita nenhuma."""
    corrente = _corrente(engine, titular="Rô")
    planilha = repo.conta_da_planilha(engine)
    repo.importar(
        engine, conta_id=planilha, arquivo="planilha.xlsx", origem="planilha", usuario="André",
        usar_ia=False, competencia="2026-08",
        lancamentos=[Lancamento(date(2026, 8, 28), "CONSULTAS PREVISTAS", 300_000,
                                categoria_hint="Trabalho", pessoa_hint="Rô",
                                competencia="2026-08")],
    )
    resultado = repo.importar(
        engine, conta_id=corrente, arquivo="b.pdf", origem="extrato", usuario="Rô",
        usar_ia=False, lancamentos=[Lancamento(date(2026, 8, 14), "TED 102.0001.RO C", 314_327)],
    )
    assert resultado["previsoes_realizadas"] == 0
    with engine.connect() as conn:
        agosto = analytics.resumo(conn, competencia="2026-08")
    assert agosto["transferencias"] == 314_327
    assert agosto["receitas"] == 300_000, "a previsão continua de pé"


def test_varredura_revê_a_linha_que_realizou_previsao(engine):
    """O que ja esta gravado com a regra antiga: a TED da Ro entrou como renda
    com status manual herdado da previsao. Ninguem a olhou — a varredura da
    subida pode passar por ela."""
    from core import dedup

    corrente = _corrente(engine, titular="Rô")
    with engine.begin() as conn:
        trabalho = conn.execute(sa.select(db.categorias.c.id).where(
            db.categorias.c.nome == "Trabalho")).scalar_one()
        conn.execute(sa.insert(db.transacoes).values(
            data=date(2026, 8, 14), competencia="2026-08", descricao="TED 102.0001.RO C",
            descricao_norm="TED RO C", valor_centavos=314_327, conta_id=corrente,
            categoria_id=trabalho, pessoa="Rô", status="manual", confianca=1.0,
            origem="extrato", hash_dedup="x", ativo=True,
            observacao=f"{dedup.MARCA_REALIZA_PREVISAO}08/2026 lançada à mão",
        ))
        conn.execute(sa.insert(db.transacoes).values(
            data=date(2026, 8, 15), competencia="2026-08", descricao="PIX RECEBIDO REM: Andre Luiz",
            descricao_norm="PIX RECEBIDO REM ANDRE LUIZ", valor_centavos=100_000, conta_id=corrente,
            categoria_id=trabalho, pessoa="Rô", status="manual", confianca=1.0,
            origem="extrato", hash_dedup="y", ativo=True,
        ))
    assert repo.marcar_transferencias_proprias(engine) == 1
    with engine.connect() as conn:
        agosto = analytics.resumo(conn, competencia="2026-08")
    assert agosto["transferencias"] == 314_327
    assert agosto["receitas"] == 100_000, "o que alguém classificou na tela fica"


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
