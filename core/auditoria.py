"""Auditoria das despesas de um mes: por onde elas dobram.

Extratos de conta corrente chegando em cima de uma planilha e de faturas de
cartao dobram despesa de tres jeitos conhecidos, e nenhum deles e erro de
digitacao: (1) o mesmo gasto na planilha e no extrato; (2) o pagamento da
fatura no extrato somado as compras que ja estao na fatura; (3) dinheiro
andando entre as contas da propria casa, saida numa e entrada na outra.

Cada funcao aqui responde a uma dessas perguntas para uma competencia, com os
ids das linhas, para a tela poder consertar num clique. Nenhuma decide nada
sozinha.
"""

from __future__ import annotations

import re
from datetime import timedelta

import sqlalchemy as sa

from . import db
from .analytics import CATEGORIA_TRANSFERENCIA, _lado_da_linha

JANELA = timedelta(days=3)

# o pagamento da fatura, como os bancos escrevem no extrato da conta corrente
_PAGAMENTO_DE_FATURA = re.compile(
    r"(PAG|PGTO|PAGTO|PAGAMENTO)\S*\s.*(CART|FATURA)|FATURA.*CART|CARTAO DE CREDITO"
    r"|\bNUBANK\b|\bNU PAGAMENTOS\b|XP VISA|VISA XP|BTG PACTUAL|ITAUCARD|BRADESCARD",
    re.IGNORECASE,
)


def _juncao():
    return (
        db.transacoes
        .join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
        .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
    )


def _colunas():
    return (
        db.transacoes.c.id, db.transacoes.c.data, db.transacoes.c.descricao,
        db.transacoes.c.descricao_norm, db.transacoes.c.valor_centavos,
        db.transacoes.c.origem, db.transacoes.c.conta_id,
        db.contas.c.nome.label("conta"), db.contas.c.tipo.label("tipo_conta"),
        db.categorias.c.nome.label("categoria"),
    )


def _linhas_do_mes(conn, competencia: str) -> list[dict]:
    lado = _lado_da_linha().label("lado")
    return [
        dict(l._mapping) for l in conn.execute(
            sa.select(*_colunas(), lado)
            .select_from(_juncao())
            .where(db.transacoes.c.ativo == sa.true(),
                   db.transacoes.c.competencia == competencia)
            .order_by(db.transacoes.c.data, db.transacoes.c.id)
        )
    ]


def despesas_por_origem(conn, competencia: str) -> list[dict]:
    """Quanto da despesa do mes veio de cada origem, por conta.

    Planilha e extrato valendo juntas no mesmo mes e a assinatura de gasto
    contado duas vezes — a mesma da renda, do outro lado.
    """
    acumulado: dict[tuple[str, str], dict] = {}
    for l in _linhas_do_mes(conn, competencia):
        if l["lado"] != "despesa" or l["categoria"] == CATEGORIA_TRANSFERENCIA:
            continue
        chave = (l["origem"], l["conta"])
        item = acumulado.setdefault(chave, {"origem": l["origem"], "conta": l["conta"],
                                            "quantos": 0, "total": 0})
        item["quantos"] += 1
        item["total"] += -l["valor_centavos"]
    return sorted(acumulado.values(), key=lambda i: (-i["total"], i["origem"]))


def previsto_e_realizado_despesa(por_origem: list[dict]) -> tuple[int, int]:
    previsto = sum(i["total"] for i in por_origem if i["origem"] in ("planilha", "manual"))
    realizado = sum(i["total"] for i in por_origem if i["origem"] == "extrato")
    return previsto, realizado


def transferencias_nao_marcadas(conn, competencia: str) -> list[dict]:
    """Saida numa conta da casa e entrada noutra, mesmo valor, ate tres dias.

    E dinheiro mudando de bolso: a Ro cobrindo a conta do Andre, a 8839
    alimentando a 0660. Contado como despesa de um lado e receita do outro,
    infla os dois totais pelo mesmo valor. So aparece aqui o par em que pelo
    menos um lado ainda nao esta em Transferencias.
    """
    linhas = [l for l in _linhas_do_mes(conn, competencia) if l["origem"] == "extrato"]
    saidas = [l for l in linhas if l["valor_centavos"] < 0]
    entradas = [l for l in linhas if l["valor_centavos"] > 0]
    usadas: set[int] = set()
    pares = []
    for s in saidas:
        for e in entradas:
            if e["id"] in usadas or e["conta_id"] == s["conta_id"]:
                continue
            if e["valor_centavos"] != -s["valor_centavos"]:
                continue
            if abs(e["data"] - s["data"]) > JANELA:
                continue
            if (s["categoria"] == CATEGORIA_TRANSFERENCIA
                    and e["categoria"] == CATEGORIA_TRANSFERENCIA):
                usadas.add(e["id"])
                break
            usadas.add(e["id"])
            pares.append({"saida": s, "entrada": e, "valor": e["valor_centavos"]})
            break
    return pares


def pagamentos_de_fatura_soltos(conn, competencia: str) -> list[dict]:
    """Debito de conta corrente que e pagamento de cartao e nao esta marcado.

    As compras ja sao despesa na fatura; o pagamento e so o dinheiro saindo
    para cobri-las. Somado como despesa, o mes paga o cartao duas vezes.
    """
    from . import cartoes

    emissores = cartoes.emissores(conn)
    totais = cartoes.totais_de_fatura(conn) if emissores else {}
    achados = []
    for l in _linhas_do_mes(conn, competencia):
        if (l["origem"] != "extrato" or l["tipo_conta"] != "corrente"
                or l["valor_centavos"] >= 0 or l["categoria"] == CATEGORIA_TRANSFERENCIA):
            continue
        motivo = cartoes.reconhecer(
            l["descricao"], l["valor_centavos"], competencia,
            emissores_cadastrados=emissores, totais=totais,
        )
        if motivo:
            achados.append({**l, "motivo": motivo})
    return achados


def duplicatas_internas(conn, competencia: str) -> list[dict]:
    """A mesma linha duas vezes na mesma conta: mesmo dia, valor e texto.

    E o rastro de um arquivo enviado duas vezes que escapou da fila de
    duplicidade, ou do mesmo mes vindo em dois extratos que se sobrepoem.
    """
    vistos: dict[tuple, dict] = {}
    copias = []
    for l in _linhas_do_mes(conn, competencia):
        chave = (l["conta_id"], l["data"], l["valor_centavos"], l["descricao_norm"])
        if chave in vistos:
            copias.append({"original": vistos[chave], "copia": l})
        else:
            vistos[chave] = l
    return copias


def auditar(conn, competencia: str) -> dict:
    por_origem = despesas_por_origem(conn, competencia)
    previsto, realizado = previsto_e_realizado_despesa(por_origem)
    return {
        "por_origem": por_origem,
        "previsto": previsto,
        "realizado": realizado,
        "transferencias": transferencias_nao_marcadas(conn, competencia),
        "pagamentos_de_fatura": pagamentos_de_fatura_soltos(conn, competencia),
        "duplicatas": duplicatas_internas(conn, competencia),
    }
