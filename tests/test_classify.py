"""Testes do motor de classificacao (core.classify), usando o seed real."""

from __future__ import annotations

import sqlalchemy as sa

from core import classify, db


def _id_categoria(conn, nome: str, natureza: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(
            db.categorias.c.nome == nome, db.categorias.c.natureza == natureza
        )
    ).scalar_one()


def _nomes(conn, resultado: classify.Resultado) -> tuple[str | None, str | None]:
    cat = sub = None
    if resultado.categoria_id:
        cat = conn.execute(
            sa.select(db.categorias.c.nome).where(db.categorias.c.id == resultado.categoria_id)
        ).scalar()
    if resultado.subcategoria_id:
        sub = conn.execute(
            sa.select(db.subcategorias.c.nome).where(
                db.subcategorias.c.id == resultado.subcategoria_id
            )
        ).scalar()
    return cat, sub


# --------------------------------------------------------------------------
# regras do dicionario inicial
# --------------------------------------------------------------------------
def test_ifood_vira_alimentacao_fora_do_domicilio(conn):
    resultado = classify.classificar(conn, "IFOOD *IFD1234 RIO DE JANEIRO", -4590)
    assert resultado.status == "auto_regra"
    assert _nomes(conn, resultado) == ("Alimentação", "Fora do Domicílio")


def test_supermercado_pao_de_acucar_vira_alimentacao_no_domicilio(conn):
    resultado = classify.classificar(conn, "SUPERM PAO DE ACUCAR 245", -32050)
    assert resultado.status == "auto_regra"
    assert _nomes(conn, resultado) == ("Alimentação", "No Domicílio")


def test_drogaria_vira_saude_farmacia(conn):
    resultado = classify.classificar(conn, "DROGARIA SAO PAULO LTDA", -5990)
    assert resultado.status == "auto_regra"
    assert _nomes(conn, resultado) == ("Saúde", "Farmácia")


def test_pro_labore_positivo_vira_receita_trabalho(conn):
    resultado = classify.classificar(conn, "PRO LABORE ANDRE MAIO/2026", 500000)
    assert resultado.status == "auto_regra"
    assert _nomes(conn, resultado) == ("Trabalho", "Pró-labore / Salário")
    naturezas = classify._natureza_por_categoria(conn)
    assert naturezas[resultado.categoria_id] == "receita"


# --------------------------------------------------------------------------
# guarda de natureza: receita nunca classifica dinheiro que saiu da conta
# --------------------------------------------------------------------------
def test_guarda_de_natureza_nao_deixa_saida_virar_receita_trabalho(conn):
    # "SALARIO" casa com a regra de receita Trabalho, mas aqui o valor e
    # negativo (dinheiro saindo para pagar a empregada) - a guarda tem que
    # bloquear essa regra.
    resultado = classify.classificar(conn, "PAGAMENTO SALARIO EMPREGADA", -280000)
    if resultado.categoria_id is not None:
        naturezas = classify._natureza_por_categoria(conn)
        assert naturezas[resultado.categoria_id] == "despesa"
    else:
        assert resultado.status == "pendente"


def test_guarda_de_natureza_mesma_palavra_funciona_para_receita(conn):
    # controle: o mesmo texto com valor positivo pode virar receita Trabalho
    resultado = classify.classificar(conn, "SALARIO RECEBIDO", 500000)
    assert resultado.categoria_id is not None
    naturezas = classify._natureza_por_categoria(conn)
    assert naturezas[resultado.categoria_id] == "receita"


# --------------------------------------------------------------------------
# aprender / memoria de estabelecimentos
# --------------------------------------------------------------------------
def test_aprender_gera_memoria_reconhecida_em_nova_grafia(conn):
    descricao_original = "LOJA MISTERIOSA CENTER 12/07"
    antes = classify.classificar(conn, descricao_original, -3000)
    assert antes.status == "pendente"
    assert antes.categoria_id is None

    categoria_id = _id_categoria(conn, "Lazer & Viagens", "despesa")
    subcategoria_id = conn.execute(
        sa.select(db.subcategorias.c.id).where(
            db.subcategorias.c.categoria_id == categoria_id,
            db.subcategorias.c.nome == "Passeios & Eventos",
        )
    ).scalar_one()

    classify.aprender(conn, descricao_original, categoria_id, subcategoria_id, "andre")

    # mesma loja, escrita diferente: outra data/parcela
    depois = classify.classificar(conn, "LOJA MISTERIOSA CENTER PARC 02/05", -4500)
    assert depois.status == "auto_memoria"
    assert depois.categoria_id == categoria_id
    assert depois.subcategoria_id == subcategoria_id
    assert depois.confianca == 1.0


def test_aprender_nao_grava_chave_curta_demais(conn):
    # chave com menos de 3 caracteres nao deve gerar regra nenhuma
    antes = conn.execute(sa.select(sa.func.count()).select_from(db.regras)).scalar()
    classify.aprender(conn, "AB", 1, None, "andre")
    depois = conn.execute(sa.select(sa.func.count()).select_from(db.regras)).scalar()
    assert depois == antes
