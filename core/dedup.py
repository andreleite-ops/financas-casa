"""Deteccao de duplicidade e conferencia contra a planilha.

Regra de ouro: nada e excluido sozinho. Um lancamento suspeito entra no banco
inativo (fora dos relatorios) e so some depois que o Andre ou a Ro confirmarem
na tela de conferencia.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

import sqlalchemy as sa

from . import db
from .texto import chave_estabelecimento

JANELA_PROVAVEL = timedelta(days=3)


def hash_lancamento(conta_id: int, dia: date, valor_centavos: int, descricao_norm: str) -> str:
    crua = f"{conta_id}|{dia.isoformat()}|{valor_centavos}|{descricao_norm}"
    return hashlib.sha256(crua.encode("utf-8")).hexdigest()


@dataclass
class Decisao:
    """O que fazer com um lancamento que chegou no upload."""

    situacao: str  # novo | duplicata_exata | duplicata_provavel | confere_planilha
    existente_id: int | None = None
    motivo: str = ""

    @property
    def entra_ativo(self) -> bool:
        return self.situacao in ("novo", "confere_planilha")

    @property
    def e_duplicata(self) -> bool:
        return self.situacao.startswith("duplicata")


def avaliar(
    conn,
    *,
    conta_id: int,
    dia: date,
    valor_centavos: int,
    descricao: str,
    descricao_norm: str,
    origem: str,
    upload_id: int | None,
    ja_casados: set[int] | None = None,
) -> Decisao:
    """Confronta um lancamento novo com o que ja existe no banco."""
    ja_casados = ja_casados if ja_casados is not None else set()
    h = hash_lancamento(conta_id, dia, valor_centavos, descricao_norm)

    iguais = conn.execute(
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.origem,
            db.transacoes.c.upload_id,
            db.transacoes.c.ativo,
        )
        .where(db.transacoes.c.hash_dedup == h, db.transacoes.c.ativo == sa.true())
        .order_by(db.transacoes.c.id)
    ).fetchall()

    for linha in iguais:
        if linha.id in ja_casados:
            continue
        # planilha da Ro x extrato: nao e duplicidade, e conferencia.
        # O do extrato prevalece e herda a classificacao que ela ja tinha dado.
        if origem == "extrato" and linha.origem == "planilha":
            return Decisao("confere_planilha", linha.id, "mesmo lançamento já estava na planilha")
        if origem == "planilha" and linha.origem == "extrato":
            return Decisao("duplicata_exata", linha.id, "já importado do extrato")
        # mesmo arquivo repetindo a linha pode ser compra legitima repetida
        if upload_id is not None and linha.upload_id == upload_id:
            return Decisao("duplicata_provavel", linha.id, "linha repetida no mesmo arquivo")
        return Decisao("duplicata_exata", linha.id, "mesma conta, data, valor e descrição")

    chave = chave_estabelecimento(descricao)
    if chave:
        vizinhos = conn.execute(
            sa.select(db.transacoes.c.id, db.transacoes.c.descricao, db.transacoes.c.data)
            .where(
                db.transacoes.c.conta_id == conta_id,
                db.transacoes.c.valor_centavos == valor_centavos,
                db.transacoes.c.ativo == sa.true(),
                db.transacoes.c.data >= dia - JANELA_PROVAVEL,
                db.transacoes.c.data <= dia + JANELA_PROVAVEL,
                db.transacoes.c.hash_dedup != h,
            )
            .order_by(db.transacoes.c.id)
        ).fetchall()
        for viz in vizinhos:
            if viz.id in ja_casados:
                continue
            # datas proximas mas em meses diferentes = cobranca recorrente na
            # virada do mes (Netflix dia 31 e dia 1), nao duplicata. Duplicata
            # de verdade nesse caso cai no hash exato, com a mesma data.
            if (viz.data.year, viz.data.month) != (dia.year, dia.month):
                continue
            if chave_estabelecimento(viz.descricao) == chave:
                dias = abs((viz.data - dia).days)
                return Decisao(
                    "duplicata_provavel",
                    viz.id,
                    f"mesmo valor e estabelecimento, {dias} dia(s) de diferença",
                )
    return Decisao("novo")


def registrar_duplicidade(conn, nova_id: int, decisao: Decisao) -> None:
    conn.execute(
        sa.insert(db.duplicidades).values(
            transacao_nova_id=nova_id,
            transacao_existente_id=decisao.existente_id,
            tipo="exata" if decisao.situacao == "duplicata_exata" else "provavel",
            motivo=decisao.motivo,
        )
    )


def pendentes(conn) -> list[dict]:
    """Fila da tela de conferencia: pares lado a lado, aguardando decisao."""
    nova = db.transacoes.alias("nova")
    velha = db.transacoes.alias("velha")
    consulta = (
        sa.select(
            db.duplicidades.c.id.label("dup_id"),
            db.duplicidades.c.tipo,
            db.duplicidades.c.motivo,
            nova.c.id.label("nova_id"),
            nova.c.data.label("nova_data"),
            nova.c.descricao.label("nova_descricao"),
            nova.c.valor_centavos.label("nova_valor"),
            nova.c.upload_id.label("nova_upload"),
            velha.c.id.label("velha_id"),
            velha.c.data.label("velha_data"),
            velha.c.descricao.label("velha_descricao"),
            velha.c.upload_id.label("velha_upload"),
            db.contas.c.nome.label("conta"),
        )
        .select_from(
            db.duplicidades.join(nova, db.duplicidades.c.transacao_nova_id == nova.c.id)
            .join(velha, db.duplicidades.c.transacao_existente_id == velha.c.id)
            .join(db.contas, nova.c.conta_id == db.contas.c.id)
        )
        .where(db.duplicidades.c.resolvida == sa.false())
        .order_by(db.duplicidades.c.tipo, nova.c.data)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def resolver(conn, dup_id: int, decisao: str, usuario: str) -> None:
    """decisao: 'excluir' apaga o lancamento novo; 'manter' ativa os dois."""
    registro = conn.execute(
        sa.select(db.duplicidades).where(db.duplicidades.c.id == dup_id)
    ).fetchone()
    if registro is None or registro.resolvida:
        return

    if decisao == "excluir":
        # zera a referencia antes de apagar a transacao: com a FK ativa
        # (PRAGMA foreign_keys=ON no SQLite, e sempre no Postgres) nao da
        # para apagar uma linha ainda referenciada por duplicidades.
        conn.execute(
            sa.update(db.duplicidades)
            .where(db.duplicidades.c.id == dup_id)
            .values(transacao_nova_id=None)
        )
        conn.execute(sa.delete(db.transacoes).where(db.transacoes.c.id == registro.transacao_nova_id))
        marca = "excluida"
    else:
        conn.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.id == registro.transacao_nova_id)
            .values(ativo=True)
        )
        marca = "mantida"

    conn.execute(
        sa.update(db.duplicidades)
        .where(db.duplicidades.c.id == dup_id)
        .values(resolvida=True, decisao=marca, decidida_por=usuario)
    )


def resolver_todas_exatas(conn, usuario: str) -> int:
    """Botao 'confirmar exclusao das duplicatas exatas'."""
    ids = [
        linha.id
        for linha in conn.execute(
            sa.select(db.duplicidades.c.id).where(
                db.duplicidades.c.resolvida == sa.false(), db.duplicidades.c.tipo == "exata"
            )
        )
    ]
    for dup_id in ids:
        resolver(conn, dup_id, "excluir", usuario)
    return len(ids)
