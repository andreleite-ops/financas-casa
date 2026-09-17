"""O mês de um extrato de conta corrente sai do arquivo, não do menu.

O menu de competência abria no mês de hoje. Um extrato de agosto enviado em
setembro ficava registrado como setembro, e o mapa "o que falta carregar"
dizia que setembro tinha sido carregado — com o mês ainda nem fechado.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import db, repo
from parsers.base import Lancamento, competencia_predominante


def test_o_mes_predominante_e_o_do_lote():
    lote = [Lancamento(data=date(2026, 8, d), descricao="X", valor_centavos=-100)
            for d in (3, 10, 20, 31)]
    lote.append(Lancamento(data=date(2026, 9, 1), descricao="AGENDADO", valor_centavos=-100))
    assert competencia_predominante(lote) == "2026-08"
    assert competencia_predominante([]) is None


def test_a_competencia_declarada_manda_sobre_a_data():
    lote = [Lancamento(data=date(2026, 8, 20), descricao="COMPRA", valor_centavos=-100,
                       competencia="2026-09")]
    assert competencia_predominante(lote) == "2026-09"


def test_o_que_ha_numa_conta_num_mes_vem_com_o_arquivo(engine):
    with engine.begin() as conn:
        conta = conn.execute(sa.insert(db.contas).values(
            nome="Bradesco teste", tipo="corrente", titular="André",
            instituicao="Bradesco", parser="generico", ativa=True,
        )).inserted_primary_key[0]
    repo.importar(
        engine, conta_id=conta, arquivo="extrato-ago.pdf", origem="extrato",
        usuario="André", usar_ia=False,
        lancamentos=[
            Lancamento(data=date(2026, 8, 5), descricao="LUZ", valor_centavos=-18_000),
            Lancamento(data=date(2026, 9, 1), descricao="DEB AGENDADO", valor_centavos=-5_000),
        ],
    )
    with engine.connect() as conn:
        agosto = repo.lancamentos_da_conta_no_mes(conn, conta, "2026-08")
        setembro = repo.lancamentos_da_conta_no_mes(conn, conta, "2026-09")
    assert [i["descricao"] for i in agosto] == ["LUZ"]
    assert [(i["descricao"], i["arquivo"]) for i in setembro] == [("DEB AGENDADO", "extrato-ago.pdf")]
