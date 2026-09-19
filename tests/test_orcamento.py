"""Testes de core.analytics.orcamento: realizado x meta por categoria."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
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


# ---------------------------------------------------------------------------
# o orçamento do período: há gasto que não cabe num mês
# ---------------------------------------------------------------------------
def test_gasto_concentrado_num_mes_estoura_o_mes_e_cabe_no_ano(engine, conn):
    """A viagem do ano inteiro acontece em julho. No mês ela aparece com
    2.700% da meta; no ano, dentro dela. Nenhum dos doze meses descreve a
    casa — o do ano descreve."""
    conta = _conta_id(conn)
    lazer = _categoria_id(conn, "Lazer & Viagens")
    for mes in range(1, 9):
        _inserir(conn, conta, date(2026, mes, 10), "PADARIA", -10_000,
                 _categoria_id(conn, "Alimentação"))
    _inserir(conn, conta, date(2026, 7, 10), "PACOTE DE VIAGEM", -3_000_000, lazer)

    renda_base = 7_000_000          # R$ 70.000 por mês
    metas = {lazer: 5.0}            # 5% da renda = R$ 3.500 por mês
    competencias = [f"2026-{m:02d}" for m in range(1, 9)]

    no_mes = analytics.orcamento(conn, "2026-07", metas, renda_base=renda_base)
    linha_mes = next(l for l in no_mes if l["categoria_id"] == lazer)
    assert linha_mes["meta"] == 350_000
    assert round(linha_mes["uso"]) == 857, "no mês da viagem, estourou muito"

    no_periodo = analytics.orcamento_do_periodo(conn, competencias, metas, renda_base)
    linha_ano = next(l for l in no_periodo if l["categoria_id"] == lazer)
    assert linha_ano["meta"] == 350_000 * 8, "a meta do período é a mensal repetida"
    assert linha_ano["realizado"] == 3_000_000
    assert round(linha_ano["uso"]) == 107, "no período, passou de pouco"
    assert linha_ano["meses_com_gasto"] == 1
    assert linha_ano["pico_mes"] == "2026-07"
    assert linha_ano["concentracao"] == 100, "tudo num mês só"
    assert linha_ano["ritmo"] == 375_000


def test_o_que_passa_de_pouco_todo_mes_estoura_o_periodo(engine, conn):
    """O contrário também: nenhum mês acende alarme e o ano estoura."""
    conta = _conta_id(conn)
    alimentacao = _categoria_id(conn, "Alimentação")
    for mes in range(1, 7):
        _inserir(conn, conta, date(2026, mes, 10), "MERCADO", -620_000, alimentacao)

    competencias = [f"2026-{m:02d}" for m in range(1, 7)]
    linhas = analytics.orcamento_do_periodo(
        conn, competencias, {alimentacao: 8.0}, 7_000_000
    )
    linha = next(l for l in linhas if l["categoria_id"] == alimentacao)
    assert linha["meta"] == 560_000 * 6
    assert linha["realizado"] == 620_000 * 6
    assert linha["estourou"] is True
    assert linha["concentracao"] < 50, "espalhado, não concentrado"
    assert linha["saldo"] == 560_000 * 6 - 620_000 * 6


def test_a_renda_considerada_fica_salva(engine):
    """A meta é percentual, e percentual de nada não é meta: a renda com que a
    casa planeja é decisão, e decisão se guarda."""
    with engine.connect() as conn:
        assert repo.renda_base_gravada(conn, 2026) is None
    repo.salvar_renda_base(engine, 2026, 7_000_000)
    with engine.connect() as conn:
        assert repo.renda_base_gravada(conn, 2026) == 7_000_000
    repo.salvar_renda_base(engine, 2026, 8_000_000)
    with engine.connect() as conn:
        assert repo.renda_base_gravada(conn, 2026) == 8_000_000
        assert repo.renda_base_gravada(conn, 2025) is None


def test_a_tela_abre_no_ultimo_mes_que_ja_aconteceu():
    """A lista do banco vem do mais recente para o mais antigo. Pegar "o
    último que já aconteceu" dela abria a tela em janeiro — e, com a janela do
    ano, o período inteiro valia um mês só."""
    from views.orcamento import _meses_do_ano

    do_banco = ["2026-08", "2026-07", "2026-06", "2026-05", "2026-04",
                "2026-03", "2026-02", "2026-01"]
    meses, inicial = _meses_do_ano(do_banco, 2026)

    assert meses == sorted(do_banco), "em ordem, para o período somar tudo"
    assert inicial == "2026-08"
    assert _meses_do_ano([], 2026) == (["2026-01"], "2026-01")
