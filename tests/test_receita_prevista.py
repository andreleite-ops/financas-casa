"""Receita lançada à mão para meses futuros × o extrato que vai chegar.

O André lançou à mão o que ainda vai entrar até o fim de 2026 — é previsão,
digitada uma vez para o orçamento não ficar cego. Quando o extrato do mês
chegar, o mesmo dinheiro vem de novo, agora de verdade.

Duas linhas para o mesmo salário dobram a renda do mês, e renda dobrada
estraga tudo que depende dela: a sobra, o percentual das metas, a leitura da
IA. Estes testes são sobre esse encontro.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import db, dedup, repo
from parsers.base import Lancamento


def _conta_corrente(engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome="Conta Salário", tipo="corrente", titular="André",
                instituicao="Banco", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def _categoria(conn, nome: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _prever_receita(engine, conn, competencia: str, valor: int, descricao="PRO LABORE"):
    """O que a tela de Receitas grava quando ele lança o mês futuro."""
    trabalho = _categoria(conn, "Trabalho")
    return repo.lancar_manual(
        engine, competencia=competencia, valor_centavos=valor, pessoa="André",
        categoria_id=trabalho, subcategoria_id=None, descricao=descricao,
        usuario="André", natureza="receita",
    )


def _importar_extrato(engine, conta_id, dia: date, valor: int, descricao: str):
    return repo.importar(
        engine,
        lancamentos=[Lancamento(data=dia, descricao=descricao, valor_centavos=valor)],
        conta_id=conta_id, arquivo="extrato.csv", origem="extrato",
        usuario="André", pessoa_padrao="André", usar_ia=False,
    )


def _receitas_do_mes(engine, competencia: str) -> list[tuple[str, int, bool]]:
    with engine.connect() as conn:
        return [
            (linha.descricao, linha.valor_centavos, linha.ativo)
            for linha in conn.execute(
                sa.select(
                    db.transacoes.c.descricao, db.transacoes.c.valor_centavos,
                    db.transacoes.c.ativo,
                ).where(
                    db.transacoes.c.competencia == competencia,
                    db.transacoes.c.valor_centavos > 0,
                ).order_by(db.transacoes.c.id)
            )
        ]


def test_extrato_nao_duplica_a_receita_prevista_a_mao(engine, conn):
    """O caso que ele perguntou, com o mesmo valor dos dois lados."""
    conta = _conta_corrente(engine)
    _prever_receita(engine, conn, "2026-11", 2_059_621)
    conn.commit()

    _importar_extrato(engine, conta, date(2026, 11, 5), 2_059_621, "TED PRO LABORE TAG")

    ativas = [linha for linha in _receitas_do_mes(engine, "2026-11") if linha[2]]
    assert len(ativas) == 1, f"receita contada duas vezes: {ativas}"
    assert sum(v for _d, v, _a in ativas) == 2_059_621


def test_o_lancamento_do_extrato_e_o_que_fica(engine, conn):
    """Entre a previsão e o extrato, quem vale é o extrato — é o que aconteceu."""
    conta = _conta_corrente(engine)
    _prever_receita(engine, conn, "2026-11", 2_000_000)      # previu redondo
    conn.commit()

    _importar_extrato(engine, conta, date(2026, 11, 5), 2_059_621, "TED PRO LABORE TAG")

    ativas = [linha for linha in _receitas_do_mes(engine, "2026-11") if linha[2]]
    assert len(ativas) == 1, f"receita contada duas vezes: {ativas}"
    assert ativas[0][1] == 2_059_621         # o valor real, não o previsto


def test_a_previsao_substituida_nao_e_apagada(engine, conn):
    """Ela fica inativa, fora dos relatórios — dá para conferir e desfazer."""
    conta = _conta_corrente(engine)
    id_previsto = _prever_receita(engine, conn, "2026-11", 2_059_621)
    conn.commit()

    _importar_extrato(engine, conta, date(2026, 11, 5), 2_059_621, "TED PRO LABORE TAG")

    with engine.connect() as leitura:
        ativo = leitura.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == id_previsto)
        ).scalar_one()
    assert ativo is False


def test_previsao_de_outro_mes_nao_e_tocada(engine, conn):
    """Dezembro continua previsto quando o extrato de novembro entra."""
    conta = _conta_corrente(engine)
    _prever_receita(engine, conn, "2026-11", 2_059_621)
    id_dezembro = _prever_receita(engine, conn, "2026-12", 2_059_621)
    conn.commit()

    _importar_extrato(engine, conta, date(2026, 11, 5), 2_059_621, "TED PRO LABORE TAG")

    with engine.connect() as leitura:
        ativo = leitura.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == id_dezembro)
        ).scalar_one()
    assert ativo is True


def test_duas_receitas_previstas_no_mes_casam_uma_a_uma(engine, conn):
    """Ele prevê o pró-labore e a Rô prevê os atendimentos, no mesmo mês.

    O extrato traz só o pró-labore: os atendimentos continuam previstos, e o
    pareamento não pode consumir a previsão errada.
    """
    conta = _conta_corrente(engine)
    _prever_receita(engine, conn, "2026-11", 2_059_621, "PRO LABORE")
    id_atendimentos = _prever_receita(engine, conn, "2026-11", 480_000, "ATENDIMENTOS")
    conn.commit()

    _importar_extrato(engine, conta, date(2026, 11, 5), 2_059_621, "TED PRO LABORE TAG")

    ativas = _receitas_do_mes(engine, "2026-11")
    assert sum(v for _d, v, ativo in ativas if ativo) == 2_059_621 + 480_000
    with engine.connect() as leitura:
        assert leitura.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == id_atendimentos)
        ).scalar_one() is True


def test_despesa_manual_nao_entra_nessa_regra(engine, conn):
    """Os euros comprados em espécie não são previsão de nada.

    A regra vale para receita: ela é pouca, recorrente e prevista de propósito.
    Aplicá-la a despesa faria toda compra em dinheiro sumir quando o extrato
    trouxesse um gasto do mesmo valor no mesmo mês.
    """
    conta = _conta_corrente(engine)
    lazer = _categoria(conn, "Lazer & Viagens")
    id_euros = repo.lancar_manual(
        engine, competencia="2026-11", valor_centavos=300_000, pessoa="Casal",
        categoria_id=lazer, subcategoria_id=None, descricao="COMPRA DE EUROS",
        usuario="André", natureza="despesa",
    )
    conn.commit()

    _importar_extrato(engine, conta, date(2026, 11, 9), -300_000, "SAQUE CAIXA 24H")

    with engine.connect() as leitura:
        assert leitura.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == id_euros)
        ).scalar_one() is True
