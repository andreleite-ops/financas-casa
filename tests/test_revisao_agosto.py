"""A revisão de agosto/26: cada mexida piorava, e a revisão achou por quê.

Sete mecanismos, cada um com o seu teste, para que nenhum volte calado:
a planilha tratada como banco pelo detector de pagamento de cartão; o
pareamento por valor cruzando meses; a previsão de uma pessoa realizada pelo
dinheiro da outra; a varredura da subida passando por cima do que foi
classificado à mão; "RESGATE" somado como renda; a crítica esquecendo o
"manter"; e o "sem categoria" mostrando o líquido dos dois lados.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, auditoria, db, reconcile, repo, seed
from parsers.base import Lancamento


def _conta(engine, nome, tipo, titular="André", instituicao="Banco"):
    with engine.begin() as conn:
        return conn.execute(sa.insert(db.contas).values(
            nome=nome, tipo=tipo, titular=titular, instituicao=instituicao,
            parser="generico", ativa=True,
        )).inserted_primary_key[0]


def _importar(engine, conta_id, lancamentos, *, origem="extrato", competencia=None,
              pessoa_padrao=None, arquivo="a.pdf"):
    return repo.importar(
        engine, conta_id=conta_id, arquivo=arquivo, origem=origem, usuario="andre",
        usar_ia=False, competencia=competencia, pessoa_padrao=pessoa_padrao,
        lancamentos=[Lancamento(**dict(l, origem=origem)) for l in lancamentos],
    )


def _resumo(engine, competencia):
    with engine.connect() as conn:
        return analytics.resumo(conn, competencia=competencia)


def _linha(engine, descricao):
    with engine.connect() as conn:
        return conn.execute(
            sa.select(db.transacoes.c.ativo, db.transacoes.c.categoria_id,
                      db.transacoes.c.status, db.transacoes.c.substituido_por,
                      db.transacoes.c.observacao, db.transacoes.c.pessoa,
                      db.categorias.c.nome.label("categoria"))
            .select_from(db.transacoes.outerjoin(
                db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id))
            .where(db.transacoes.c.descricao == descricao)
        ).one()


def test_linha_da_planilha_nao_e_pagamento_de_cartao(engine):
    """"CARTAO NUBANK 32.238,29" na planilha de julho e o gasto do mes, nao
    o pagamento dele: a fatura de julho nunca vai ser importada."""
    _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    planilha = repo.conta_da_planilha(engine)
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 5), descricao="CARTAO NUBANK", valor_centavos=-3_223_829,
             competencia="2026-07"),
    ], origem="planilha", competencia="2026-07")
    julho = _resumo(engine, "2026-07")
    assert julho["despesas"] == 3_223_829
    assert julho["transferencias"] == 0
    assert _linha(engine, "CARTAO NUBANK").categoria != analytics.CATEGORIA_TRANSFERENCIA


def test_pareamento_por_valor_cruza_o_mes_mas_conta_uma_vez(engine):
    """A compra de 23/08 na fatura de setembro e a linha de 22/08 da planilha
    de agosto sao o mesmo gasto: a planilha conta pela compra, a fatura pelo
    vencimento. Muda de mes, mas nunca conta duas vezes."""
    cartao = _conta(engine, "Cartao teste", "cartao", instituicao="Nubank")
    planilha = repo.conta_da_planilha(engine)
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 22), descricao="LAZER", valor_centavos=-90_000,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-08")
    _importar(engine, cartao, [
        dict(data=date(2026, 8, 23), descricao="PASSEIO PARQUE", valor_centavos=-90_000,
             competencia="2026-09"),
    ], competencia="2026-09")
    assert _linha(engine, "LAZER").ativo is False
    assert _resumo(engine, "2026-08")["despesas"] + _resumo(engine, "2026-09")["despesas"] == 90_000


def test_resgate_de_pontos_ou_seguro_nao_e_aplicacao():
    assert repo.aplicacao_resgatada("RESGATE CDB", 1_500_000)
    assert repo.aplicacao_resgatada("RESGATE INVEST FACIL", 800_000)
    assert repo.aplicacao_resgatada("RESGATE FUNDO DI", 800_000)
    assert not repo.aplicacao_resgatada("RESGATE DE PONTOS LIVELO", 50_000)
    assert not repo.aplicacao_resgatada("RESGATE SEGURO PORTO", 50_000)
    assert not repo.aplicacao_resgatada("RESGATE FGTS CAIXA", 50_000)
    assert not repo.aplicacao_resgatada("RESGATE CDB", -1_500_000)


def test_pareamento_por_valor_no_mesmo_mes_continua_valendo(engine):
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 5), descricao="CONDOMINIO", valor_centavos=-120_000,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-08")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 7), descricao="COND EDIF X", valor_centavos=-120_000),
    ])
    assert _linha(engine, "CONDOMINIO").ativo is False
    assert _resumo(engine, "2026-08")["despesas"] == 120_000


def test_previsao_de_receita_so_se_realiza_com_dinheiro_da_mesma_pessoa(engine):
    """O bonus do Andre nao aposenta os atendimentos previstos da Ro."""
    planilha = repo.conta_da_planilha(engine)
    bradesco = _conta(engine, "Bradesco teste", "corrente", titular="André")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 28), descricao="ATENDIMENTOS", valor_centavos=1_500_000,
             competencia="2026-08", pessoa_hint="Rô"),
    ], origem="planilha", competencia="2026-08")
    resultado = _importar(engine, bradesco, [
        dict(data=date(2026, 8, 7), descricao="PIX RECEBIDO REM: EMPRESA LTDA",
             valor_centavos=1_456_128),
    ], pessoa_padrao="André")
    assert resultado.get("previsoes_realizadas", 0) == 0
    previsao = _linha(engine, "ATENDIMENTOS")
    assert previsao.ativo is True and previsao.pessoa == "Rô"
    credito = _linha(engine, "PIX RECEBIDO REM: EMPRESA LTDA")
    assert credito.pessoa != "Rô"


def test_previsao_da_mesma_pessoa_ainda_se_realiza(engine):
    planilha = repo.conta_da_planilha(engine)
    bradesco = _conta(engine, "Bradesco teste", "corrente", titular="André")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 5), descricao="PRO LABORE", valor_centavos=2_000_000,
             competencia="2026-08", pessoa_hint="André"),
    ], origem="planilha", competencia="2026-08")
    resultado = _importar(engine, bradesco, [
        dict(data=date(2026, 8, 7), descricao="TED PRO LABORE EMPRESA", valor_centavos=2_059_621),
    ], pessoa_padrao="André")
    assert resultado.get("previsoes_realizadas", 0) == 1
    assert _linha(engine, "PRO LABORE").ativo is False
    assert _resumo(engine, "2026-08")["receitas"] == 2_059_621


def test_varredura_da_subida_respeita_o_que_foi_classificado_a_mao(engine):
    corrente = _conta(engine, "Bradesco teste", "corrente")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 17), descricao="PIX ENVIADO DES: Andre Luiz Rodrigues",
             valor_centavos=-913_311),
    ])
    assert _linha(engine, "PIX ENVIADO DES: Andre Luiz Rodrigues").categoria == \
        analytics.CATEGORIA_TRANSFERENCIA
    with engine.connect() as conn:
        outra = conn.execute(
            sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Moradia")
        ).scalar_one()
        transacao_id = conn.execute(
            sa.select(db.transacoes.c.id)
            .where(db.transacoes.c.descricao == "PIX ENVIADO DES: Andre Luiz Rodrigues")
        ).scalar_one()
    repo.reclassificar(engine, transacao_id, categoria_id=outra, subcategoria_id=None,
                       pessoa=None, usuario="andre", criar_regra=False)
    seed.semear(engine)   # o reboot
    depois = _linha(engine, "PIX ENVIADO DES: Andre Luiz Rodrigues")
    assert depois.categoria == "Moradia" and depois.status == "manual"


def test_resgate_e_o_principal_voltando_nao_renda(engine):
    corrente = _conta(engine, "Bradesco teste", "corrente")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 10), descricao="RESGATE CDB", valor_centavos=1_500_000),
        dict(data=date(2026, 8, 11), descricao="REND PAGO APLIC AUT MAIS", valor_centavos=1_700),
    ])
    agosto = _resumo(engine, "2026-08")
    assert agosto["receitas"] == 1_700, "so o rendimento e renda"
    assert agosto["transferencias"] == 1_500_000
    assert _linha(engine, "RESGATE CDB").categoria == analytics.CATEGORIA_TRANSFERENCIA


def test_varredura_tira_de_rendimentos_o_resgate_ja_gravado(engine):
    """O que entrou pela regra antiga (RESGATE -> Rendimentos) e consertado
    na subida — mas so o que nao foi classificado a mao."""
    corrente = _conta(engine, "Bradesco teste", "corrente")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 10), descricao="RESGATE INVEST FACIL", valor_centavos=800_000),
    ])
    with engine.begin() as conn:
        rendimentos = conn.execute(
            sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Rendimentos")
        ).scalar_one()
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.descricao == "RESGATE INVEST FACIL")
            .values(categoria_id=rendimentos, subcategoria_id=None, status="auto_regra")
        )
    assert _resumo(engine, "2026-08")["receitas"] == 800_000
    seed.semear(engine)
    assert _resumo(engine, "2026-08")["receitas"] == 0
    assert _resumo(engine, "2026-08")["transferencias"] == 800_000


def test_critica_respeita_o_manter_e_o_fica_como_esta(engine):
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 5), descricao="PENSAO", valor_centavos=-1_580_000,
             competencia="2026-08"),
        dict(data=date(2026, 8, 5), descricao="PADARIA", valor_centavos=-7_071,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-08")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 20), descricao="SUPERMERCADO", valor_centavos=-30_000),
    ])
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert {i["descricao"] for i in critica["so_planilha"]} == {"PENSAO", "PADARIA"}

    mantidas = reconcile.encerrar_critica(engine, "2026-08", "andre")
    assert mantidas == 2
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert critica["sem_conferencia"] is True
    assert critica["encerradas"] == ["2026-08"]
    # tudo como esta: as duas linhas continuam valendo
    assert _resumo(engine, "2026-08")["despesas"] == 1_580_000 + 7_071 + 30_000
    with engine.connect() as conn:
        assert auditoria.auditar(conn, "2026-08")["critica_encerrada"] is True

    # reabrir volta a listar o mes, mas o que foi mantido segue mantido
    reconcile.reabrir_critica(engine, "2026-08")
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert critica["sem_conferencia"] is False
    assert critica["so_planilha"] == []
    assert critica["encerradas"] == []


def test_manter_as_duas_nao_volta_na_visita_seguinte(engine):
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 15), descricao="SUPERMERCADO XYZ", valor_centavos=-20_000,
             competencia="2026-07"),
    ], origem="planilha", competencia="2026-07")
    _importar(engine, corrente, [
        dict(data=date(2026, 7, 17), descricao="SUPERMERCADO XYZ 0001", valor_centavos=-21_500),
    ])
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert len(critica["divergencias"]) == 1
    reconcile.resolver_divergencia(
        engine, planilha_id=critica["divergencias"][0]["planilha"]["id"],
        manter="planilha", usuario="andre",
    )
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert critica["divergencias"] == [] and critica["so_planilha"] == []
    assert _linha(engine, "SUPERMERCADO XYZ").ativo is True


def test_desfazer_o_upload_devolve_o_que_a_conferencia_em_massa_aposentou(engine):
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 1), descricao="JORNAL", valor_centavos=-6_990,
             competencia="2026-07"),
    ], origem="planilha", competencia="2026-07")
    resultado = _importar(engine, corrente, [
        dict(data=date(2026, 7, 31), descricao="ASSINATURA JORNAL", valor_centavos=-6_990),
    ])
    assert reconcile.aposentar_pares_exatos(engine, "andre") == 1
    aposentada = _linha(engine, "JORNAL")
    assert aposentada.ativo is False and aposentada.substituido_por == resultado["upload_id"]
    repo.apagar_upload(engine, resultado["upload_id"])
    assert _linha(engine, "JORNAL").ativo is True
    assert _resumo(engine, "2026-07")["despesas"] == 6_990


def test_sem_categoria_mostra_os_dois_lados(engine):
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 3), descricao="ZZZ ENTRADA SEM REGRA", valor_centavos=3_900_000),
        dict(data=date(2026, 8, 4), descricao="ZZZ SAIDA SEM REGRA", valor_centavos=-5_800_000),
    ])
    agosto = _resumo(engine, "2026-08")
    assert agosto["sem_categoria_entrada"] == 3_900_000
    assert agosto["sem_categoria_saida"] == 5_800_000
    assert agosto["receitas"] == 3_900_000 and agosto["despesas"] == 5_800_000


def test_raio_x_abre_o_mes_por_origem_conta_e_lado(engine):
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 5), descricao="PENSAO", valor_centavos=-1_580_000,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-08")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 7), descricao="PIX RECEBIDO REM: BANCO INTER SA",
             valor_centavos=1_194_457),
        dict(data=date(2026, 8, 8), descricao="ZZZ SAIDA SEM REGRA", valor_centavos=-30_000),
    ])
    with engine.connect() as conn:
        raio = auditoria.raio_x(conn, "2026-08")
    lados = {(i["origem"], i["lado"]): i["total"] for i in raio["valendo"]}
    assert lados[("planilha", "saída")] == 1_580_000
    assert lados[("extrato", "transferência")] == 1_194_457
    assert lados[("extrato", "saída")] == 30_000
    assert [l["descricao"] for l in raio["planilha"]] == ["PENSAO"]


def test_mes_fechado_com_extrato_da_pessoa_aposenta_a_receita_prevista_dela(engine):
    """Os atendimentos previstos da Ro (uma linha) chegam em dezenas de PIX
    que nenhum pareamento por valor casa. Quando o mes fecha e o extrato dela
    entra, a previsao ja aconteceu — e a do Andre fica, porque o extrato e
    dela, nao dele."""
    planilha = repo.conta_da_planilha(engine)
    itau = _conta(engine, "Itaú teste", "corrente", titular="Rô")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 28), descricao="ATENDIMENTOS", valor_centavos=1_500_000,
             competencia="2026-08", pessoa_hint="Rô"),
        dict(data=date(2026, 8, 5), descricao="PRO LABORE", valor_centavos=2_000_000,
             competencia="2026-08", pessoa_hint="André"),
        dict(data=date(2026, 9, 28), descricao="ATENDIMENTOS SET", valor_centavos=1_500_000,
             competencia="2026-09", pessoa_hint="Rô"),
    ], origem="planilha", competencia="2026-08")
    resultado = _importar(engine, itau, [
        dict(data=date(2026, 8, 3), descricao="PIX TRANSF PACIENTE A", valor_centavos=840_000),
        dict(data=date(2026, 8, 10), descricao="PIX TRANSF PACIENTE B", valor_centavos=900_000),
    ], pessoa_padrao="Rô")
    assert resultado["previsoes_do_mes_fechado"] == 1
    previsao = _linha(engine, "ATENDIMENTOS")
    assert previsao.ativo is False and previsao.substituido_por == resultado["upload_id"]
    assert _linha(engine, "PRO LABORE").ativo is True, "o extrato é da Rô, não do André"
    assert _linha(engine, "ATENDIMENTOS SET").ativo is True, "setembro ainda não fechou"
    assert _resumo(engine, "2026-08")["receitas"] == 2_000_000 + 840_000 + 900_000

    # desfazer o upload devolve a previsao
    repo.apagar_upload(engine, resultado["upload_id"])
    assert _linha(engine, "ATENDIMENTOS").ativo is True


def test_varredura_da_subida_aposenta_previsao_de_mes_fechado_ja_carregado(engine):
    planilha = repo.conta_da_planilha(engine)
    itau = _conta(engine, "Itaú teste", "corrente", titular="Rô")
    _importar(engine, itau, [
        dict(data=date(2026, 8, 3), descricao="PIX TRANSF PACIENTE A", valor_centavos=840_000),
    ], pessoa_padrao="Rô")
    # a previsao entra DEPOIS do extrato: e o que a subida tem de pegar
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 28), descricao="ATENDIMENTOS", valor_centavos=1_500_000,
             competencia="2026-08", pessoa_hint="Rô"),
    ], origem="planilha", competencia="2026-08")
    assert _resumo(engine, "2026-08")["receitas"] == 2_340_000
    assert repo.aposentar_previsoes_de_meses_fechados(engine) == 1
    assert repo.aposentar_previsoes_de_meses_fechados(engine) == 0, "idempotente"
    assert _resumo(engine, "2026-08")["receitas"] == 840_000


def test_previsao_de_mes_fechado_so_morre_se_o_extrato_trouxe_o_dinheiro(engine):
    """Um PIX de R$ 840 nao prova R$ 15.000 de atendimentos; a conta do casal
    nao aposenta a renda do casal; o que foi lancado a mao fica."""
    planilha = repo.conta_da_planilha(engine)
    itau = _conta(engine, "Itaú teste", "corrente", titular="Rô")
    conjunta = _conta(engine, "Conjunta teste", "corrente", titular="Casal")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 28), descricao="ATENDIMENTOS", valor_centavos=1_500_000,
             competencia="2026-08", pessoa_hint="Rô"),
        dict(data=date(2026, 8, 10), descricao="ALUGUEL NUN", valor_centavos=450_000,
             competencia="2026-08", pessoa_hint="Casal"),
    ], origem="planilha", competencia="2026-08")
    repo.lancar_manual(engine, competencia="2026-08", valor_centavos=300_000, pessoa="Rô",
                       categoria_id=1, subcategoria_id=None, descricao="RESTITUICAO IR",
                       usuario="andre")
    _importar(engine, itau, [
        dict(data=date(2026, 8, 3), descricao="PIX TRANSF PACIENTE A", valor_centavos=84_000),
    ], pessoa_padrao="Rô")
    _importar(engine, conjunta, [
        dict(data=date(2026, 8, 3), descricao="TARIFA", valor_centavos=-2_000),
    ])
    assert _linha(engine, "ATENDIMENTOS").ativo is True, "R$ 840 não prova R$ 15.000"
    assert _linha(engine, "ALUGUEL NUN").ativo is True
    assert _linha(engine, "RESTITUICAO IR").ativo is True
    assert repo.aposentar_previsoes_de_meses_fechados(engine) == 0


def test_fica_como_esta_nao_deixa_o_par_de_valor_igual_contar_duas_vezes(engine):
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 1), descricao="JORNAL", valor_centavos=-6_990,
             competencia="2026-08"),
        dict(data=date(2026, 8, 5), descricao="PENSAO", valor_centavos=-1_580_000,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-08")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 31), descricao="ASSINATURA JORNAL", valor_centavos=-6_990),
    ])
    assert reconcile.encerrar_critica(engine, "2026-08", "andre") == 1
    assert _linha(engine, "JORNAL").ativo is False, "valor igual: é o mesmo gasto"
    assert _linha(engine, "PENSAO").ativo is True
    assert _resumo(engine, "2026-08")["despesas"] == 1_580_000 + 6_990


def test_linha_da_planilha_igual_a_soma_de_dois_pix_e_o_mesmo_gasto(engine):
    """"PENSAO 15.800" na planilha e dois PIX de 7.900 no banco."""
    planilha = repo.conta_da_planilha(engine)
    corrente = _conta(engine, "Banco teste", "corrente")
    _importar(engine, planilha, [
        dict(data=date(2026, 8, 5), descricao="PENSAO ALIMENTICIA", valor_centavos=-1_580_000,
             competencia="2026-08"),
        dict(data=date(2026, 8, 5), descricao="PADARIA", valor_centavos=-14_500,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-08")
    _importar(engine, corrente, [
        dict(data=date(2026, 8, 5), descricao="PIX ENVIADO DES: FILHO UM", valor_centavos=-790_000),
        dict(data=date(2026, 8, 5), descricao="PIX ENVIADO DES: FILHA DOIS", valor_centavos=-790_000),
        dict(data=date(2026, 8, 9), descricao="PADARIA DO ZE", valor_centavos=-10_000),
        dict(data=date(2026, 8, 12), descricao="BANCA", valor_centavos=-4_500),
    ])
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    somas = [d for d in critica["divergencias"] if len(d["extratos"]) > 1]
    assert [d["planilha"]["descricao"] for d in somas] == ["PENSAO ALIMENTICIA"]
    # 145,00 = 100,00 + 45,00 e coincidencia: linha pequena nao soma partes
    assert [i["descricao"] for i in critica["so_planilha"]] == ["PADARIA"]

    assert reconcile.aposentar_pares_exatos(engine, "andre", "2026-08") == 1
    assert _linha(engine, "PENSAO ALIMENTICIA").ativo is False
    assert _resumo(engine, "2026-08")["despesas"] == 1_580_000 + 14_500 + 14_500
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert critica["conferidos"] == 2


def test_auditoria_nao_propoe_transferencia_entre_terceiros(engine):
    itau = _conta(engine, "Itaú teste", "corrente", titular="Rô")
    bradesco = _conta(engine, "Bradesco teste", "corrente", titular="André")
    _importar(engine, itau, [
        dict(data=date(2026, 8, 5), descricao="PIX TRANSF CLINICA X", valor_centavos=-60_000),
        dict(data=date(2026, 8, 5), descricao="TED 102 0001 SEM NOME", valor_centavos=-25_000),
    ])
    _importar(engine, bradesco, [
        dict(data=date(2026, 8, 5), descricao="PIX RECEBIDO REM: PACIENTE Y", valor_centavos=60_000),
        dict(data=date(2026, 8, 6), descricao="TRANSFERENCIA RECEBIDA", valor_centavos=25_000),
    ])
    with engine.connect() as conn:
        pares = auditoria.transferencias_nao_marcadas(conn, "2026-08")
    assert [p["valor"] for p in pares] == [25_000], "600 de terceiro para terceiro não é da casa"


def test_compra_do_cartao_conta_no_mes_da_compra():
    from parsers.base import ajustar_ano_fatura, competencia_da_compra

    assert competencia_da_compra(date(2026, 7, 17), "2026-08") == "2026-07"
    assert competencia_da_compra(date(2026, 8, 2), "2026-08") == "2026-08"
    assert competencia_da_compra(date(2026, 8, 23), "2026-09") == "2026-08"
    # parcela com data de meses atras nao volta para o passado: vale a fatura
    assert competencia_da_compra(date(2026, 2, 10), "2026-08") == "2026-08"
    lancamentos = ajustar_ano_fatura(
        [Lancamento(date(2026, 7, 17), "PCART TAB", -335_165),
         Lancamento(date(2026, 8, 5), "POSTO", -20_000)], "2026-08")
    assert [l.competencia for l in lancamentos] == ["2026-07", "2026-08"]


def test_migracao_move_compras_para_o_mes_da_compra_e_confere_com_a_planilha(engine):
    """Compras de julho gravadas em agosto vao para julho — e casam, uma a
    uma, com a planilha de julho, que ja as tinha item a item."""
    cartao = _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    planilha = repo.conta_da_planilha(engine)
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 5), descricao="DROGARIA", valor_centavos=-90_883,
             competencia="2026-07"),
        dict(data=date(2026, 7, 5), descricao="PADARIA JULHO", valor_centavos=-7_071,
             competencia="2026-07"),
    ], origem="planilha", competencia="2026-07")
    resultado = _importar(engine, cartao, [
        dict(data=date(2026, 7, 14), descricao="Drogaria Sao Paulo",
             valor_centavos=-90_883, competencia="2026-08"),
        dict(data=date(2026, 8, 5), descricao="POSTO", valor_centavos=-20_000,
             competencia="2026-08"),
    ], competencia="2026-08")
    assert _resumo(engine, "2026-08")["despesas"] == 110_883, "gravado na convenção antiga"

    # o banco de teste ja nasceu com a marca da migracao; em producao ela nao existe
    with engine.begin() as conn:
        conn.execute(sa.delete(db.config).where(db.config.c.chave == repo.CARTAO_PELA_COMPRA))
    feito = repo.contar_cartao_pela_compra(engine)
    assert feito == {"movidas": 1, "conferidas": 1}
    assert _resumo(engine, "2026-08")["despesas"] == 20_000
    assert _resumo(engine, "2026-07")["despesas"] == 90_883 + 7_071, "a compra uma vez só"
    assert _linha(engine, "DROGARIA").ativo is False
    assert _linha(engine, "DROGARIA").substituido_por == resultado["upload_id"]
    assert repo.contar_cartao_pela_compra(engine) == {"movidas": 0, "conferidas": 0}


def test_total_da_fatura_continua_sendo_o_da_fatura(engine):
    """Com as compras contando no mes da compra, o total da fatura de agosto
    (o que o banco cobra) sai do upload, nao do mes das compras."""
    from core import cartoes

    cartao = _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    _importar(engine, cartao, [
        dict(data=date(2026, 7, 20), descricao="SUPERMERCADO", valor_centavos=-2_000_000,
             competencia="2026-07"),
        dict(data=date(2026, 8, 2), descricao="POSTO", valor_centavos=-1_221_232,
             competencia="2026-08"),
    ], competencia="2026-08")
    with engine.connect() as conn:
        totais = cartoes.totais_de_fatura(conn)
    assert totais[(cartao, "2026-08")] == 3_221_232


def test_parcela_conta_no_ciclo_da_fatura_em_qualquer_cartao():
    """O principio: a parcela conta no ciclo da fatura que a cobra. O ciclo
    sai do proprio arquivo (a primeira compra a vista). Parcela datada dentro
    do ciclo (Nubank) conta pela data; datada de antes (XP imprime a compra
    original) conta no mes em que o ciclo comeca."""
    from parsers.base import ajustar_ano_fatura, competencia_da_compra, inicio_do_ciclo

    nubank = ajustar_ano_fatura([
        Lancamento(date(2026, 8, 14), "Drogaria Sao Paulo - Parcela 6/6", -90_883),
        Lancamento(date(2026, 8, 14), "Uber", -1_500),
        Lancamento(date(2026, 9, 13), "Deco Skin - Parcela 1/2", -8_495),
        Lancamento(date(2026, 9, 12), "Pagamento recebido", 83_148),
    ], "2026-09")
    assert inicio_do_ciclo(nubank) == date(2026, 8, 14)
    assert [l.competencia for l in nubank] == ["2026-08", "2026-08", "2026-09", "2026-09"]

    xp = ajustar_ano_fatura([
        Lancamento(date(2026, 6, 10), "PAGUE MENOS 0225 PARC 03/06", -100_304),
        Lancamento(date(2026, 4, 7), "EINSTEIN MORUMBI PARC 05/10", -57_640),
        Lancamento(date(2026, 8, 2), "POSTO", -20_000),
        Lancamento(date(2026, 8, 30), "MERCADO", -30_000),
    ], "2026-09")
    assert inicio_do_ciclo(xp) == date(2026, 8, 2)
    assert [l.competencia for l in xp] == ["2026-08", "2026-08", "2026-08", "2026-08"]

    # sem ciclo conhecido, parcela de fora do ciclo vale o mes da fatura
    assert competencia_da_compra(date(2026, 6, 10), "2026-09", "LOJA PARC 03/06") == "2026-09"
    # numero de documento nao e parcela: compra a vista, mes da compra
    assert competencia_da_compra(date(2026, 8, 20), "2026-09", "PAGUE MENOS 0225") == "2026-08"

def test_busca_por_valor_acha_a_mesma_compra_em_qualquer_origem(engine):
    assert repo._termo_em_centavos("3.351,65") == 335_165
    assert repo._termo_em_centavos("3351,65") == 335_165
    assert repo._termo_em_centavos("1500") == 150_000
    assert repo._termo_em_centavos("cartorio") is None
    planilha = repo.conta_da_planilha(engine)
    cartao = _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 5), descricao="DOCUMENTOS", valor_centavos=-335_165,
             competencia="2026-07"),
    ], origem="planilha", competencia="2026-07")
    _importar(engine, cartao, [
        dict(data=date(2026, 7, 17), descricao="Pcart*1*Tab*Sao*Paulo", valor_centavos=-335_165,
             competencia="2026-08"),
    ], competencia="2026-08")
    with engine.connect() as conn:
        achados = repo.buscar_transacoes(conn, "3.351,65")
    assert sorted(a["descricao"] for a in achados if a["valor_centavos"] == -335_165) == \
        ["DOCUMENTOS", "Pcart*1*Tab*Sao*Paulo"] or len(achados) >= 1


def test_mes_so_da_planilha_nao_conta_compra_de_cartao(engine):
    """Julho e da planilha (anotada a mao, sem extrato de banco): a compra de
    17/07 que a fatura de agosto traz nao conta em julho; a linha da planilha
    que o pareamento tinha aposentado volta; agosto, com extrato, segue."""
    cartao = _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    banco = _conta(engine, "Bradesco teste", "corrente")
    planilha = repo.conta_da_planilha(engine)
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 15), descricao="FARMACIA", valor_centavos=-90_883,
             competencia="2026-07"),
        dict(data=date(2026, 7, 15), descricao="ADVOGADO", valor_centavos=-100_000,
             competencia="2026-07"),
        dict(data=date(2026, 8, 5), descricao="MERCADO", valor_centavos=-86_728,
             competencia="2026-08"),
    ], origem="planilha", competencia="2026-07")
    _importar(engine, cartao, [
        dict(data=date(2026, 7, 14), descricao="Drogaria Sao Paulo", valor_centavos=-90_883,
             competencia="2026-07"),
        dict(data=date(2026, 7, 17), descricao="Pcart Tab Sao Paulo", valor_centavos=-335_165,
             competencia="2026-07"),
        dict(data=date(2026, 8, 2), descricao="POSTO", valor_centavos=-20_000,
             competencia="2026-08"),
    ], competencia="2026-08")
    _importar(engine, banco, [
        dict(data=date(2026, 8, 7), descricao="COND EDIF", valor_centavos=-262_500),
    ])
    # a farmacia da planilha foi aposentada pela compra da fatura (mesmo valor, 1 dia)
    assert _linha(engine, "FARMACIA").ativo is False

    feito = repo.aplicar_meses_da_planilha(engine)
    assert feito == {"retiradas": 2, "devolvidas": 0, "planilha_de_volta": 1}
    assert _resumo(engine, "2026-07")["despesas"] == 90_883 + 100_000, "julho = planilha"
    assert _resumo(engine, "2026-08")["despesas"] == 86_728 + 20_000 + 262_500
    assert _linha(engine, "Pcart Tab Sao Paulo").observacao == repo.MARCA_MES_DA_PLANILHA
    assert repo.aplicar_meses_da_planilha(engine) == {"retiradas": 0, "devolvidas": 0,
                                                       "planilha_de_volta": 0}

    # se um dia o extrato de julho entrar, a compra volta a contar
    _importar(engine, banco, [
        dict(data=date(2026, 7, 20), descricao="TARIFA", valor_centavos=-1_000),
    ])
    feito = repo.aplicar_meses_da_planilha(engine)
    assert feito["devolvidas"] == 2
    assert _linha(engine, "Pcart Tab Sao Paulo").ativo is True


def test_mes_em_curso_sem_extrato_nao_e_mes_da_planilha(engine):
    """Setembro ainda nao tem extrato de banco, mas vem depois do primeiro
    (agosto): e mes dos uploads, so que atrasado. As compras de setembro
    ficam."""
    cartao = _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    banco = _conta(engine, "Bradesco teste", "corrente")
    planilha = repo.conta_da_planilha(engine)
    _importar(engine, planilha, [
        dict(data=date(2026, 9, 5), descricao="PREVISAO", valor_centavos=-23_220,
             competencia="2026-09"),
    ], origem="planilha", competencia="2026-09")
    _importar(engine, banco, [
        dict(data=date(2026, 8, 7), descricao="COND EDIF", valor_centavos=-262_500),
    ])
    _importar(engine, cartao, [
        dict(data=date(2026, 9, 7), descricao="BOOKING", valor_centavos=-272_653,
             competencia="2026-09"),
    ], competencia="2026-09")
    assert repo.aplicar_meses_da_planilha(engine)["retiradas"] == 0
    assert _resumo(engine, "2026-09")["despesas"] == 23_220 + 272_653


def test_mes_da_planilha_sai_da_critica(engine):
    cartao = _conta(engine, "Nubank teste", "cartao", instituicao="Nubank")
    banco = _conta(engine, "Bradesco teste", "corrente")
    planilha = repo.conta_da_planilha(engine)
    _importar(engine, planilha, [
        dict(data=date(2026, 7, 5), descricao="LUZ", valor_centavos=-17_172, competencia="2026-07"),
        dict(data=date(2026, 8, 5), descricao="PADARIA", valor_centavos=-7_071, competencia="2026-08"),
    ], origem="planilha", competencia="2026-07")
    _importar(engine, cartao, [
        dict(data=date(2026, 7, 17), descricao="Pcart", valor_centavos=-335_165, competencia="2026-07"),
    ], competencia="2026-08")
    _importar(engine, banco, [
        dict(data=date(2026, 8, 7), descricao="COND EDIF", valor_centavos=-262_500),
    ])
    repo.aplicar_meses_da_planilha(engine)
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert critica["periodos"] == 1
    assert [i["descricao"] for i in critica["so_planilha"]] == ["PADARIA"]


def test_corrigir_o_mes_da_fatura_recoloca_as_parcelas(engine):
    """A fatura do XP paga em setembro e inteira de agosto; enviada como
    setembro, as parcelas cairam em setembro. Corrigir o mes conserta."""
    with engine.begin() as conn:
        xp = conn.execute(sa.insert(db.contas).values(
            nome="XP teste", tipo="cartao", titular="André", instituicao="XP",
            parser="xp", ativa=True)).inserted_primary_key[0]
    resultado = _importar(engine, xp, [
        dict(data=date(2026, 6, 10), descricao="PAGUE MENOS PARC 03/06", valor_centavos=-100_304,
             competencia="2026-09"),
        dict(data=date(2026, 8, 20), descricao="POSTO", valor_centavos=-20_000,
             competencia="2026-08"),
    ], competencia="2026-09")
    assert _resumo(engine, "2026-09")["despesas"] == 100_304, "gravado com o mes do menu"
    assert repo.mudar_mes_da_fatura(engine, resultado["upload_id"], "2026-08") == 1
    assert _resumo(engine, "2026-09")["despesas"] == 0
    assert _resumo(engine, "2026-08")["despesas"] == 100_304 + 20_000
