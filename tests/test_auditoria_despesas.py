"""Por onde a despesa de um mês dobra — e a crítica que devia pegar isso.

A aba "Crítica planilha × extratos" existe para confrontar a planilha com o
extrato do mesmo mês. Só que ela comparava por (conta, mês), e a planilha mora
numa conta reservada só dela: a interseção era vazia por construção, e a
crítica respondia "ainda não há período com as duas origens" para sempre. As
despesas de agosto dobraram com a ferramenta certa desligada.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import auditoria, db, reconcile, repo
from parsers.base import Lancamento


def _conta(engine, nome, tipo="corrente", titular="André"):
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=nome, tipo=tipo, titular=titular, instituicao="Banco",
                parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def _planilha(engine, lancamentos):
    return repo.importar(
        engine, conta_id=repo.conta_da_planilha(engine), arquivo="planilha.xlsx",
        origem="planilha", usuario="André", usar_ia=False,
        lancamentos=[Lancamento(origem="planilha", **l) for l in lancamentos],
    )


def _extrato(engine, conta_id, lancamentos):
    return repo.importar(
        engine, conta_id=conta_id, arquivo="extrato.pdf", origem="extrato",
        usuario="André", usar_ia=False,
        lancamentos=[Lancamento(**l) for l in lancamentos],
    )


def test_critica_enxerga_planilha_e_extrato_em_contas_diferentes(engine):
    """O bug de base: a planilha numa conta, o extrato noutra, o mesmo mês."""
    bradesco = _conta(engine, "Bradesco C/C teste")
    _planilha(engine, [dict(data=date(2026, 8, 5), descricao="Condominio", valor_centavos=-150_000)])
    _extrato(engine, bradesco, [
        dict(data=date(2026, 8, 12), descricao="DEB AUTOM COND EDIF X", valor_centavos=-150_000),
    ])
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert critica["sem_conferencia"] is False, "as duas origens existem em agosto"
    assert critica["periodos"] == 1


def test_mesmo_valor_no_mesmo_mes_vira_divergencia_e_nao_so_na_planilha(engine):
    """Condomínio anotado no dia 5 e debitado no dia 12: o mesmo gasto."""
    bradesco = _conta(engine, "Bradesco C/C teste")
    _planilha(engine, [dict(data=date(2026, 8, 5), descricao="Condominio", valor_centavos=-150_000)])
    _extrato(engine, bradesco, [
        dict(data=date(2026, 8, 12), descricao="DEB AUTOM COND EDIF X", valor_centavos=-150_000),
    ])
    with engine.connect() as conn:
        critica = reconcile.criticar(conn)
    assert len(critica["divergencias"]) == 1
    assert critica["so_planilha"] == []
    par = critica["divergencias"][0]
    assert par["planilha"]["descricao"] == "Condominio"
    assert par["diferenca"] == 0


def test_auditoria_ve_planilha_e_extrato_valendo_juntos(engine):
    bradesco = _conta(engine, "Bradesco C/C teste")
    _planilha(engine, [dict(data=date(2026, 8, 5), descricao="Condominio", valor_centavos=-150_000)])
    _extrato(engine, bradesco, [
        dict(data=date(2026, 8, 12), descricao="DEB AUTOM COND EDIF X", valor_centavos=-150_000),
    ])
    with engine.connect() as conn:
        resultado = auditoria.auditar(conn, "2026-08")
    assert (resultado["previsto"], resultado["realizado"]) == (150_000, 150_000)


def test_pagamento_de_fatura_no_extrato_ja_entra_como_transferencia(engine):
    """O gravador reconhece o pagamento na entrada: a auditoria não tem o que apontar."""
    bradesco = _conta(engine, "Bradesco C/C teste")
    _extrato(engine, bradesco, [
        dict(data=date(2026, 8, 10), descricao="PAGTO ELETRON COBRANCA NUBANK",
             valor_centavos=-5_355_331),
        dict(data=date(2026, 8, 11), descricao="SUPERMERCADO Y", valor_centavos=-30_000),
    ])
    with engine.connect() as conn:
        assert auditoria.pagamentos_de_fatura_soltos(conn, "2026-08") == []
        resultado = auditoria.auditar(conn, "2026-08")
    assert resultado["realizado"] == 30_000, "só o mercado é despesa"


def test_pagamento_gravado_por_fora_do_gravador_e_apontado_e_marcado(engine):
    """A auditoria não confia no gravador: olha o banco como ele está."""
    from core.dedup import hash_lancamento
    from core.texto import normalizar

    bradesco = _conta(engine, "Bradesco C/C teste")
    with engine.begin() as conn:
        conn.execute(sa.insert(db.transacoes).values(
            data=date(2026, 8, 10), competencia="2026-08",
            descricao="PAGTO ELETRON COBRANCA NUBANK",
            descricao_norm=normalizar("PAGTO ELETRON COBRANCA NUBANK"),
            valor_centavos=-5_355_331, conta_id=bradesco, pessoa="Casal", status="pendente",
            origem="extrato", ativo=True,
            hash_dedup=hash_lancamento(bradesco, date(2026, 8, 10), -5_355_331,
                                       normalizar("PAGTO ELETRON COBRANCA NUBANK")),
        ))
    with engine.connect() as conn:
        soltos = auditoria.pagamentos_de_fatura_soltos(conn, "2026-08")
    assert [l["valor_centavos"] for l in soltos] == [-5_355_331]
    assert soltos[0]["motivo"].startswith("pagamento")

    repo.marcar_transferencia(engine, [l["id"] for l in soltos], "André",
                              subcategoria="Pagamento de Fatura")
    with engine.connect() as conn:
        assert auditoria.pagamentos_de_fatura_soltos(conn, "2026-08") == []
        assert auditoria.auditar(conn, "2026-08")["realizado"] == 0


def test_transferencia_entre_contas_da_casa_e_apontada(engine):
    ro = _conta(engine, "Itaú teste", titular="Rô")
    andre = _conta(engine, "Bradesco teste")
    _extrato(engine, ro, [dict(data=date(2026, 8, 14), descricao="TED ANDRE", valor_centavos=-314_327)])
    _extrato(engine, andre, [dict(data=date(2026, 8, 15), descricao="TED RECEBIDA RO", valor_centavos=314_327)])
    with engine.connect() as conn:
        pares = auditoria.transferencias_nao_marcadas(conn, "2026-08")
    assert len(pares) == 1
    assert pares[0]["valor"] == 314_327

    repo.marcar_transferencia(engine, [pares[0]["saida"]["id"], pares[0]["entrada"]["id"]], "André")
    with engine.connect() as conn:
        assert auditoria.transferencias_nao_marcadas(conn, "2026-08") == []
        resultado = auditoria.auditar(conn, "2026-08")
    assert resultado["realizado"] == 0


def test_duplicata_exata_e_apontada_e_desativada(engine):
    bradesco = _conta(engine, "Bradesco C/C teste")
    with engine.begin() as conn:
        from core.dedup import hash_lancamento
        from core.texto import normalizar
        for _ in range(2):
            conn.execute(sa.insert(db.transacoes).values(
                data=date(2026, 8, 3), competencia="2026-08", descricao="FARMACIA Z",
                descricao_norm=normalizar("FARMACIA Z"), valor_centavos=-4_240,
                conta_id=bradesco, pessoa="Casal", status="pendente", origem="extrato",
                hash_dedup=hash_lancamento(bradesco, date(2026, 8, 3), -4_240, normalizar("FARMACIA Z")),
                ativo=True, natureza="despesa",
            ))
    with engine.connect() as conn:
        copias = auditoria.duplicatas_internas(conn, "2026-08")
    assert len(copias) == 1
    repo.desativar_transacoes(engine, [copias[0]["copia"]["id"]], "teste")
    with engine.connect() as conn:
        assert auditoria.duplicatas_internas(conn, "2026-08") == []
        assert auditoria.auditar(conn, "2026-08")["realizado"] == 4_240


def test_mes_limpo_nao_tem_o_que_dizer(engine):
    bradesco = _conta(engine, "Bradesco C/C teste")
    _extrato(engine, bradesco, [dict(data=date(2026, 8, 11), descricao="SUPERMERCADO Y", valor_centavos=-30_000)])
    with engine.connect() as conn:
        r = auditoria.auditar(conn, "2026-08")
    assert r["previsto"] == 0 and r["transferencias"] == [] and r["pagamentos_de_fatura"] == [] and r["duplicatas"] == []


def test_aposentar_em_massa_so_os_pares_de_valor_exato(engine):
    bradesco = _conta(engine, "Bradesco C/C teste")
    _planilha(engine, [
        dict(data=date(2026, 8, 5), descricao="Condominio", valor_centavos=-150_000),
        dict(data=date(2026, 8, 6), descricao="Luz", valor_centavos=-18_000),
        dict(data=date(2026, 8, 7), descricao="Feira", valor_centavos=-9_000),   # em dinheiro
    ])
    _extrato(engine, bradesco, [
        dict(data=date(2026, 8, 12), descricao="DEB AUTOM COND EDIF X", valor_centavos=-150_000),
        dict(data=date(2026, 8, 13), descricao="DA ELETROPAULO 1", valor_centavos=-18_450),
    ])
    with engine.connect() as conn:
        antes = auditoria.auditar(conn, "2026-08")
    assert (antes["previsto"], antes["realizado"]) == (177_000, 168_450)

    assert reconcile.aposentar_pares_exatos(engine, "André", "2026-08") == 1
    with engine.connect() as conn:
        depois = auditoria.auditar(conn, "2026-08")
        critica = reconcile.criticar(conn)
    assert depois["previsto"] == 27_000, "só o condomínio saiu; luz (divergente) e feira ficam"
    # "Luz" e "DA ELETROPAULO" não se reconhecem nem pelo nome nem pelo valor:
    # ficam para alguém decidir, cada uma do seu lado da crítica
    assert {i["descricao"] for i in critica["so_planilha"]} == {"Luz", "Feira"}
    assert [i["descricao"] for i in critica["faltantes"]] == ["DA ELETROPAULO 1"]
    assert reconcile.aposentar_pares_exatos(engine, "André", "2026-08") == 0
