"""Motor de classificacao em tres camadas.

1. Memoria de estabelecimentos - o que ja foi corrigido a mao uma vez
2. Regras por palavra-chave - o dicionario inicial, editavel
3. IA (Claude) - so o que sobrou; confianca baixa cai na fila manual

Guarda de natureza: uma regra de receita nunca classifica um valor que saiu da
conta. "PAGAMENTO SALARIO EMPREGADA" e despesa, nao receita, mesmo casando a
palavra SALARIO.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from . import db
from .texto import chave_estabelecimento, normalizar

LIMITE_CONFIANCA_IA = 0.75


@dataclass
class Resultado:
    categoria_id: int | None = None
    subcategoria_id: int | None = None
    pessoa: str | None = None
    status: str = "pendente"          # auto_memoria | auto_regra | auto_ia | pendente
    confianca: float | None = None
    explicacao: str = ""

    @property
    def classificado(self) -> bool:
        return self.categoria_id is not None and self.status != "pendente"


def _natureza_por_categoria(conn) -> dict[int, str]:
    return {
        linha.id: linha.natureza
        for linha in conn.execute(sa.select(db.categorias.c.id, db.categorias.c.natureza))
    }


def carregar_regras(conn) -> list[dict]:
    """Memoria primeiro (prioridade menor), depois o dicionario."""
    consulta = (
        sa.select(
            db.regras.c.id,
            db.regras.c.padrao,
            db.regras.c.tipo_match,
            db.regras.c.categoria_id,
            db.regras.c.subcategoria_id,
            db.regras.c.pessoa,
            db.regras.c.prioridade,
            db.regras.c.origem,
        )
        .order_by(db.regras.c.prioridade, sa.func.length(db.regras.c.padrao).desc())
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def _casa(regra: dict, descricao_norm: str, chave: str) -> bool:
    padrao = regra["padrao"]
    if regra["tipo_match"] == "exato":
        return chave == padrao or descricao_norm == padrao
    if regra["tipo_match"] == "regex":
        import re

        try:
            return re.search(padrao, descricao_norm) is not None
        except re.error:
            return False
    return padrao in descricao_norm


def classificar_local(
    descricao: str,
    valor_centavos: int,
    regras: list[dict],
    naturezas: dict[int, str],
) -> Resultado:
    """Camadas 1 e 2, sem tocar no banco nem na rede."""
    descricao_norm = normalizar(descricao)
    chave = chave_estabelecimento(descricao)
    natureza_esperada = "receita" if valor_centavos > 0 else "despesa"

    for regra in regras:
        if not _casa(regra, descricao_norm, chave):
            continue
        if naturezas.get(regra["categoria_id"]) != natureza_esperada:
            continue  # guarda de natureza
        status = "auto_memoria" if regra["origem"] == "aprendida" else "auto_regra"
        return Resultado(
            categoria_id=regra["categoria_id"],
            subcategoria_id=regra["subcategoria_id"],
            pessoa=regra["pessoa"],
            status=status,
            confianca=1.0 if status == "auto_memoria" else 0.9,
            explicacao=("memória: " if status == "auto_memoria" else "regra: ") + regra["padrao"],
        )
    return Resultado()


def classificar(conn, descricao: str, valor_centavos: int) -> Resultado:
    """Conveniencia para uso avulso (a tela manual e os testes)."""
    return classificar_local(
        descricao, valor_centavos, carregar_regras(conn), _natureza_por_categoria(conn)
    )


def aprender(
    conn,
    descricao: str,
    categoria_id: int,
    subcategoria_id: int | None,
    usuario: str,
    pessoa: str | None = None,
) -> None:
    """Grava a correcao manual como memoria de estabelecimento.

    A chave e o estabelecimento sem parcela, data e codigo de terminal, entao a
    proxima fatura reconhece o mesmo lugar escrito de outro jeito.
    """
    chave = chave_estabelecimento(descricao)
    if not chave or len(chave) < 3:
        return

    existente = conn.execute(
        sa.select(db.regras.c.id).where(
            db.regras.c.padrao == chave, db.regras.c.tipo_match == "exato"
        )
    ).scalar()
    valores = dict(
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        pessoa=pessoa,
        origem="aprendida",
        criada_por=usuario,
    )
    if existente:
        conn.execute(sa.update(db.regras).where(db.regras.c.id == existente).values(**valores))
    else:
        conn.execute(
            sa.insert(db.regras).values(
                padrao=chave, tipo_match="exato", prioridade=10, **valores
            )
        )


def reclassificar_pendentes(conn, limite: int | None = None) -> int:
    """Reaplica as regras nas pendencias - util depois de aprender algo novo."""
    regras = carregar_regras(conn)
    naturezas = _natureza_por_categoria(conn)
    consulta = sa.select(
        db.transacoes.c.id, db.transacoes.c.descricao, db.transacoes.c.valor_centavos
    ).where(db.transacoes.c.status == "pendente", db.transacoes.c.ativo == sa.true())
    if limite:
        consulta = consulta.limit(limite)

    resolvidas = 0
    for linha in conn.execute(consulta).fetchall():
        resultado = classificar_local(linha.descricao, linha.valor_centavos, regras, naturezas)
        if resultado.classificado:
            valores = dict(
                categoria_id=resultado.categoria_id,
                subcategoria_id=resultado.subcategoria_id,
                status=resultado.status,
                confianca=resultado.confianca,
            )
            if resultado.pessoa:
                valores["pessoa"] = resultado.pessoa
            conn.execute(
                sa.update(db.transacoes).where(db.transacoes.c.id == linha.id).values(**valores)
            )
            resolvidas += 1
    return resolvidas
