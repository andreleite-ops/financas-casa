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
from .texto import chave_estabelecimento, normalizar, pessoa_na_descricao

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


def categorias_bidirecionais(conn) -> set[int]:
    """Categorias que aceitam os dois sinais, isentas da guarda de natureza.

    Transferencia entre contas e a unica: ela existe justamente porque o mesmo
    dinheiro aparece como credito de um lado e debito do outro. Aplicar a
    guarda aqui recusaria metade dos lancamentos e a metade recusada voltaria a
    ser contada como receita — que e exatamente o erro que a categoria existe
    para evitar.
    """
    from .analytics import CATEGORIA_TRANSFERENCIA

    return {
        linha.id
        for linha in conn.execute(sa.select(db.categorias.c.id, db.categorias.c.nome))
        if linha.nome == CATEGORIA_TRANSFERENCIA
    }


def donos_por_categoria(conn) -> dict[int, str]:
    """categoria_id -> pessoa, para as categorias que sao de uma pessoa so.

    Pensao e gasto com os filhos e do Andre: nao e despesa da casa a ser
    rateada. Sem isso, cair na categoria certa ainda deixava o gasto no dono
    errado, e o relatorio por pessoa continuava mentindo.
    """
    from .plano_contas import DONO_POR_CATEGORIA

    return {
        linha.id: DONO_POR_CATEGORIA[linha.nome]
        for linha in conn.execute(sa.select(db.categorias.c.id, db.categorias.c.nome))
        if linha.nome in DONO_POR_CATEGORIA
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
    natureza_hint: str | None = None,
    rotulo_origem: str | None = None,
    bidirecionais: set[int] | None = None,
) -> Resultado:
    """Camadas 1 e 2, sem tocar no banco nem na rede.

    natureza_hint vem da origem quando ela declara despesa ou receita numa
    coluna propria; nesse caso ela manda, porque um estorno dentro de despesa
    tem sinal de entrada sem deixar de ser despesa.

    rotulo_origem e como a origem chamou o lancamento na coluna dela ("TAG",
    "BIOS", "ALUGUEL"). Na planilha da casa a fonte de renda mora ali, nao na
    descricao: a linha diz "SALARIO" e so o rotulo conta que e da TAG, do
    Andre. Sem olhar o rotulo, toda receita ficaria com o dono da conta.

    A descricao e olhada primeiro, e o rotulo so depois que nenhuma regra casou
    com ela. O rotulo e uma gaveta larga — a venda do apartamento esta marcada
    como "TAG" porque foi dinheiro do Andre, mas a descricao diz "VENDA APTO".
    Deixar o rotulo ganhar transformaria meio milhao de uma vez em salario.
    """
    descricao_norm = normalizar(descricao)
    chave = chave_estabelecimento(descricao)
    rotulo_norm = normalizar(rotulo_origem) if rotulo_origem else ""
    natureza_esperada = natureza_hint or ("receita" if valor_centavos > 0 else "despesa")

    def _procurar(texto: str, chave_busca: str, onde: str) -> Resultado | None:
        for regra in regras:
            if not _casa(regra, texto, chave_busca):
                continue
            if (regra["categoria_id"] not in (bidirecionais or set())
                    and naturezas.get(regra["categoria_id"]) != natureza_esperada):
                continue  # guarda de natureza
            status = "auto_memoria" if regra["origem"] == "aprendida" else "auto_regra"
            return Resultado(
                categoria_id=regra["categoria_id"],
                subcategoria_id=regra["subcategoria_id"],
                pessoa=regra["pessoa"],
                status=status,
                confianca=1.0 if status == "auto_memoria" else 0.9,
                explicacao=(
                    ("memória: " if status == "auto_memoria" else "regra: ")
                    + f"{regra['padrao']} ({onde})"
                ),
            )
        return None

    por_descricao = _procurar(descricao_norm, chave, "descrição")
    por_rotulo = _procurar(rotulo_norm, rotulo_norm, "rótulo da origem") if rotulo_norm else None

    achado = por_descricao or por_rotulo
    if achado is None:
        return Resultado()

    # a descricao diz o que foi; o rotulo, de quem e. "SALARIO" com rotulo
    # "TAG" entra em Pro-labore pela descricao e como do Andre pelo rotulo —
    # e a regra do salario, sozinha, nao teria dono nenhum.
    if achado.pessoa is None:
        outro = por_rotulo if achado is por_descricao else por_descricao
        if outro is not None and outro.pessoa:
            achado.pessoa = outro.pessoa
    return achado


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
    donos = donos_por_categoria(conn)
    bidirecionais = categorias_bidirecionais(conn)
    consulta = sa.select(
        db.transacoes.c.id,
        db.transacoes.c.descricao,
        db.transacoes.c.valor_centavos,
        # a natureza e o rotulo gravados no upload valem aqui tambem: sem eles,
        # um estorno pendente cairia do lado errado e uma receita perderia o
        # dono que so o rotulo da origem sabe
        db.transacoes.c.natureza,
        db.transacoes.c.classificacao_origem,
    ).where(db.transacoes.c.status == "pendente", db.transacoes.c.ativo == sa.true())
    if limite:
        consulta = consulta.limit(limite)

    resolvidas = 0
    for linha in conn.execute(consulta).fetchall():
        resultado = classificar_local(
            linha.descricao, linha.valor_centavos, regras, naturezas,
            natureza_hint=linha.natureza,
            rotulo_origem=linha.classificacao_origem,
            bidirecionais=bidirecionais,
        )
        if resultado.classificado:
            valores = dict(
                categoria_id=resultado.categoria_id,
                subcategoria_id=resultado.subcategoria_id,
                status=resultado.status,
                confianca=resultado.confianca,
            )
            # a regra manda; senão, o que a própria descrição diz ("ALMOÇO
            # ANDRÉ", "CONSULTA RO"). Sem isso, reaplicar as regras acertava a
            # categoria e continuava atribuindo o gasto à pessoa errada.
            dono = (
                resultado.pessoa
                or pessoa_na_descricao(linha.descricao)
                or donos.get(resultado.categoria_id)
            )
            if dono:
                valores["pessoa"] = dono
            conn.execute(
                sa.update(db.transacoes).where(db.transacoes.c.id == linha.id).values(**valores)
            )
            resolvidas += 1
    return resolvidas
