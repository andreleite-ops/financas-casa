"""Testes de core.analytics.contexto_para_ia: resumo textual sem chamada de rede."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import ai, analytics, db
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


def _bloquear_rede(monkeypatch):
    """Faz explodir qualquer tentativa de sair para a API da IA."""

    def _explode(*_a, **_k):
        raise AssertionError("contexto_para_ia nao deveria chamar a camada de IA")

    monkeypatch.setattr(ai, "_cliente", _explode)
    monkeypatch.setattr(ai, "disponivel", _explode)
    monkeypatch.setattr(ai, "sugerir_categorias", _explode)
    monkeypatch.setattr(ai, "analisar_mes", _explode)


def test_contexto_gera_texto_com_competencia_categorias_e_maiores_saidas(engine, conn, monkeypatch):
    _bloquear_rede(monkeypatch)
    conta_id = _conta_id(conn)
    trabalho_id = _categoria_id(conn, "Trabalho")
    alimentacao_id = _categoria_id(conn, "Alimentação")

    _inserir(conn, conta_id, date(2026, 7, 5), "PRO LABORE", 500_000, trabalho_id)
    _inserir(conn, conta_id, date(2026, 7, 10), "SUPERMERCADO GRANDE", -180_000, alimentacao_id)
    _inserir(conn, conta_id, date(2026, 7, 12), "PADARIA ESQUINA", -3_000, alimentacao_id)

    texto = analytics.contexto_para_ia(conn, "2026-07")

    assert texto and texto.strip()
    assert "2026-07" in texto
    assert "Alimentação" in texto
    # maior saida do mes deve aparecer entre as "dez maiores saidas"
    assert "SUPERMERCADO GRANDE" in texto


def test_contexto_nao_estoura_com_mes_sem_lancamentos(engine, conn, monkeypatch):
    _bloquear_rede(monkeypatch)

    texto = analytics.contexto_para_ia(conn, "2026-08")

    assert texto and texto.strip()
    assert "2026-08" in texto
    assert "Receitas: R$ 0,00" in texto
