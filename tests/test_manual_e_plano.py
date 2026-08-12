"""Lançamento à mão, exclusão de categoria e metas sugeridas pelo histórico.

Pedidos da Rô e do André depois de usarem o app pela primeira vez a sério.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from core import analytics, db, repo


def _categoria(engine, nome, sub=None):
    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)
    cat = next(c for c in plano if c["nome"] == nome)
    return cat["id"], next((s["id"] for s in cat["subcategorias"] if s["nome"] == sub), None)


# ---------------------------------------------------------------------------
# despesa avulsa: os euros comprados em espécie
# ---------------------------------------------------------------------------
def test_despesa_a_mao_entra_negativa_mesmo_digitada_positiva(engine):
    """Quem digita pensa em "gastei 3.500", não em "menos três mil e quinhentos"."""
    cat, sub = _categoria(engine, "Lazer & Viagens", "Viagens")
    repo.lancar_manual(
        engine, competencia="2026-07", valor_centavos=350_000, pessoa="Casal",
        categoria_id=cat, subcategoria_id=sub, descricao="COMPRA DE EUROS",
        usuario="Rô", natureza="despesa",
    )

    with engine.connect() as conn:
        resumo = analytics.resumo(conn, competencia="2026-07")
        gravado = conn.execute(
            sa.select(db.transacoes.c.valor_centavos, db.transacoes.c.natureza)
            .where(db.transacoes.c.descricao == "COMPRA DE EUROS")
        ).first()

    assert gravado.valor_centavos == -350_000
    assert gravado.natureza == "despesa"
    assert resumo["despesas"] == 350_000
    assert resumo["receitas"] == 0


def test_a_mesma_porta_serve_para_receita_e_despesa(engine):
    cat_d, sub_d = _categoria(engine, "Alimentação", "Fora do Domicílio")
    cat_r, sub_r = _categoria(engine, "Trabalho", "Prestação de Serviços")
    repo.lancar_manual(engine, competencia="2026-05", valor_centavos=20_000, pessoa="Casal",
                       categoria_id=cat_d, subcategoria_id=sub_d, descricao="JANTAR",
                       usuario="Rô", natureza="despesa")
    repo.lancar_manual(engine, competencia="2026-05", valor_centavos=900_000, pessoa="Rô",
                       categoria_id=cat_r, subcategoria_id=sub_r, descricao="ATENDIMENTOS",
                       usuario="Rô", natureza="receita")

    with engine.connect() as conn:
        resumo = analytics.resumo(conn, competencia="2026-05")
        despesas = repo.lancamentos_manuais(conn, 2026, natureza="despesa")
        receitas = repo.lancamentos_manuais(conn, 2026, natureza="receita")

    assert resumo["despesas"] == 20_000
    assert resumo["receitas"] == 900_000
    assert [d["descricao"] for d in despesas] == ["JANTAR"]
    assert [r["descricao"] for r in receitas] == ["ATENDIMENTOS"]


def test_recusa_valor_zero_ou_natureza_invalida(engine):
    cat, _ = _categoria(engine, "Alimentação")
    for valor in (0, -1):
        with pytest.raises(ValueError):
            repo.lancar_manual(engine, competencia="2026-05", valor_centavos=valor,
                               pessoa="Casal", categoria_id=cat, subcategoria_id=None,
                               descricao="X", usuario="Rô", natureza="despesa")
    with pytest.raises(ValueError):
        repo.lancar_manual(engine, competencia="2026-05", valor_centavos=100,
                           pessoa="Casal", categoria_id=cat, subcategoria_id=None,
                           descricao="X", usuario="Rô", natureza="patrimonio")


# ---------------------------------------------------------------------------
# apagar categoria criada por engano
# ---------------------------------------------------------------------------
def test_categoria_nova_e_vazia_pode_ser_apagada(engine):
    nova = repo.salvar_categoria(engine, nome="Criada por engano", natureza="despesa")
    repo.salvar_subcategoria(engine, categoria_id=nova, nome="Sub qualquer")

    with engine.connect() as conn:
        assert repo.uso_da_categoria(conn, nova)["pode_apagar"]
    assert repo.excluir_categoria(engine, nova)

    with engine.connect() as conn:
        plano = repo.plano_de_contas(conn)
        sobrou = conn.execute(
            sa.select(sa.func.count()).select_from(db.subcategorias)
            .where(db.subcategorias.c.categoria_id == nova)
        ).scalar()
    assert "Criada por engano" not in [c["nome"] for c in plano]
    assert sobrou == 0   # as subcategorias vão junto


def test_categoria_com_lancamento_nao_e_apagada(engine):
    """Apagar deixaria lançamentos sem gaveta e o total mudaria sozinho."""
    cat, _ = _categoria(engine, "Alimentação")
    repo.lancar_manual(engine, competencia="2026-05", valor_centavos=10_000, pessoa="Casal",
                       categoria_id=cat, subcategoria_id=None, descricao="MERCADO",
                       usuario="Rô", natureza="despesa")

    with engine.connect() as conn:
        uso = repo.uso_da_categoria(conn, cat)
    assert uso["lancamentos"] == 1
    assert not uso["pode_apagar"]
    assert repo.excluir_categoria(engine, cat) is False

    with engine.connect() as conn:
        assert analytics.resumo(conn, competencia="2026-05")["despesas"] == 10_000


def test_subcategoria_com_lancamento_tambem_e_protegida(engine):
    cat, sub = _categoria(engine, "Alimentação", "Fora do Domicílio")
    repo.lancar_manual(engine, competencia="2026-05", valor_centavos=5_000, pessoa="Casal",
                       categoria_id=cat, subcategoria_id=sub, descricao="PIZZA",
                       usuario="Rô", natureza="despesa")
    assert repo.excluir_subcategoria(engine, sub) is False

    _, livre = _categoria(engine, "Alimentação", "No Domicílio")
    assert repo.excluir_subcategoria(engine, livre) is True


# ---------------------------------------------------------------------------
# média mensal e meta sugerida
# ---------------------------------------------------------------------------
def test_media_divide_pelos_meses_que_ja_aconteceram(monkeypatch):
    """823 mil até agosto são 103 mil por mês, não 68 — o ano ainda não acabou."""
    import core.analytics as a

    class Agosto(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 10)

    monkeypatch.setattr(a, "date", Agosto)
    assert a.meses_decorridos(2026) == 8
    assert a.meses_decorridos(2025) == 12       # ano fechado divide por doze
    assert 82_303_728 // 8 == 10_287_966


def test_metas_sugeridas_saem_do_que_a_casa_gasta(engine):
    """Um orçamento que começa em zero e pede treze palpites não é preenchido."""
    cat, _ = _categoria(engine, "Alimentação")
    # 5.000 por mês em dois meses, com renda base de 10.000
    for mes in ("2026-01", "2026-02"):
        repo.lancar_manual(engine, competencia=mes, valor_centavos=500_000, pessoa="Casal",
                           categoria_id=cat, subcategoria_id=None, descricao="MERCADO",
                           usuario="Rô", natureza="despesa")

    with engine.connect() as conn:
        sugestao = repo.metas_pela_media(conn, 2026, renda_base=1_000_000)

    meses = analytics.meses_decorridos(2026)
    esperado = round(1_000_000 / meses / 1_000_000 * 100, 1)
    assert sugestao[cat] == esperado
    # sem renda base não há percentual que faça sentido
    with engine.connect() as conn:
        assert repo.metas_pela_media(conn, 2026, renda_base=0) == {}


# ---------------------------------------------------------------------------
# explodir a categoria
# ---------------------------------------------------------------------------
def test_abertura_por_subcategoria_mostra_o_que_falta_detalhar(engine):
    """Esconder o que não tem subcategoria faria a soma das partes ficar menor
    que o total, sem explicação nenhuma."""
    cat, sub = _categoria(engine, "Alimentação", "Fora do Domicílio")
    repo.lancar_manual(engine, competencia="2026-03", valor_centavos=30_000, pessoa="Casal",
                       categoria_id=cat, subcategoria_id=sub, descricao="RESTAURANTE",
                       usuario="Rô", natureza="despesa")
    repo.lancar_manual(engine, competencia="2026-03", valor_centavos=10_000, pessoa="Casal",
                       categoria_id=cat, subcategoria_id=None, descricao="MERCADO",
                       usuario="Rô", natureza="despesa")

    with engine.connect() as conn:
        fatias = analytics.por_subcategoria(conn, cat, competencia="2026-03")
        aberto = analytics.serie_por_subcategoria(conn, cat, 2026)

    por_nome = {f["subcategoria"]: f for f in fatias}
    assert por_nome["Fora do Domicílio"]["total"] == 30_000
    assert por_nome["— sem subcategoria —"]["total"] == 10_000
    assert por_nome["— sem subcategoria —"]["detalhada"] is False
    # as partes somam o total da categoria
    assert sum(f["total"] for f in fatias) == 40_000
    assert {l["categoria"] for l in aberto["linhas"]} == {
        "Fora do Domicílio", "— sem subcategoria —"
    }


def test_media_do_total_bate_com_a_soma_das_medias_das_categorias(engine, monkeypatch):
    """O André pegou isto olhando: 823 no ano dava 82 de média, não 103.

    A linha TOTAL dividia pelo número de colunas de mês, e havia colunas de
    setembro e outubro só porque um agendamento de R$ 200 caiu lá. A média do
    TOTAL saía menor que a soma das médias das categorias — o que é impossível,
    e é justamente essa impossibilidade que este teste vigia.
    """
    import core.analytics as a
    from views import dashboard

    class Agosto(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 10)

    monkeypatch.setattr(a, "date", Agosto)

    alimentacao, _ = _categoria(engine, "Alimentação")
    transporte, _ = _categoria(engine, "Transporte")
    # oito meses de gasto de verdade...
    for mes in range(1, 9):
        repo.lancar_manual(
            engine, competencia=f"2026-{mes:02d}", valor_centavos=800_000, pessoa="Casal",
            categoria_id=alimentacao, subcategoria_id=None, descricao="MERCADO",
            usuario="Rô", natureza="despesa",
        )
    # ...e um agendamento solitário em outubro, que cria uma coluna a mais
    repo.lancar_manual(
        engine, competencia="2026-10", valor_centavos=20_000, pessoa="Casal",
        categoria_id=transporte, subcategoria_id=None, descricao="PEDAGIO",
        usuario="Rô", natureza="despesa",
    )

    with engine.connect() as conn:
        matriz = a.tabela_mes_a_mes(conn, 2026)

    assert len(matriz["meses"]) == 9        # nove colunas...
    assert a.meses_decorridos(2026) == 8    # ...mas oito meses decorridos

    soma_das_medias = sum(linha["media"] for linha in matriz["linhas"])
    acumulado = sum(linha["acumulado"] for linha in matriz["linhas"])
    media_do_total = acumulado // a.meses_decorridos(2026)
    # a diferença só pode vir do arredondamento de cada linha, nunca de um
    # divisor diferente
    assert abs(media_do_total - soma_das_medias) < len(matriz["linhas"])

    html = dashboard._matriz_mes_a_mes(matriz, 2026)
    linha_total = html[html.index(">TOTAL<"):]
    # 64.200,00 acumulados; em 8 meses dá 8.025,00, em 9 daria 7.133,33
    assert ">64,2<" in linha_total     # o acumulado, para garantir que é a linha certa
    assert ">8,0<" in linha_total      # a média pelos meses decorridos
    assert ">7,1<" not in linha_total  # e não pelo número de colunas
