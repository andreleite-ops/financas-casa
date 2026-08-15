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


# ---------------------------------------------------------------------------
# memória: o que não identifica estabelecimento não vira regra
# ---------------------------------------------------------------------------
def test_descricao_que_so_diz_o_meio_de_pagamento_nao_vira_memoria(engine, conn):
    """O caso concreto: o pagamento da fatura chega como "PIX QR CODE DINAMICO".

    Classificá-lo como transferência está certo. Guardar essa chave faria todo
    Pix por QR virar pagamento de fatura — e cada mercado pago por QR sumiria
    dos gastos, calado, todo mês.
    """
    import sqlalchemy as sa

    from core import classify, db

    categoria = conn.execute(
        sa.select(db.categorias.c.id).where(
            db.categorias.c.nome == "Transferências entre Contas"
        )
    ).scalar_one()

    for generica in ("PIX QR CODE DINAMICO", "PIX ENVIADO", "DEBITO AUTOMATICO",
                     "PAGTO ELETRON COBRANCA", "TED RECEBIDA"):
        assert classify.aprender(conn, generica, categoria, None, "André") is False

    assert conn.execute(
        sa.select(sa.func.count()).select_from(db.regras).where(
            db.regras.c.origem == "aprendida"
        )
    ).scalar() == 0


def test_descricao_com_nome_de_estabelecimento_continua_virando_memoria(engine, conn):
    """A trava não pode engolir o caso normal, que é o que faz a fila encolher."""
    import sqlalchemy as sa

    from core import classify, db

    categoria = conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Alimentação")
    ).scalar_one()

    assert classify.aprender(conn, "PADARIA DA ESQUINA 1234", categoria, None, "André")
    assert classify.aprender(conn, "PIX TRANSF JOAQUIM16/07", categoria, None, "André")
    assert classify.aprender(conn, "DA ELETROPAULO 99887766", categoria, None, "André")

    aprendidas = conn.execute(
        sa.select(db.regras.c.padrao).where(db.regras.c.origem == "aprendida")
    ).scalars().all()
    assert len(aprendidas) == 3
