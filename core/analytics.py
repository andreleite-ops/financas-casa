"""Agregacoes das telas: mes a mes, acumulado no ano e ano a ano.

Tratamento da poupanca: aporte nao e consumo. O total de "despesas" exclui a
categoria Poupanca & Investimentos, que aparece em separado. Assim
receitas - despesas - poupanca = sobra livre do mes, que e o numero que
interessa.

Todos os valores saem em centavos inteiros; a formatacao fica nas telas.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from . import db

CATEGORIA_POUPANCA = "Poupança & Investimentos"


def _id_poupanca(conn) -> int | None:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == CATEGORIA_POUPANCA)
    ).scalar()


def _base(competencia: str | None = None, ano: int | None = None, pessoa: str | None = None):
    filtros = [db.transacoes.c.ativo == sa.true()]
    if competencia:
        filtros.append(db.transacoes.c.competencia == competencia)
    if ano:
        filtros.append(db.transacoes.c.competencia.like(f"{ano}-%"))
    if pessoa and pessoa != "Todos":
        filtros.append(db.transacoes.c.pessoa == pessoa)
    return filtros


def resumo(conn, competencia: str | None = None, ano: int | None = None, pessoa: str | None = None) -> dict:
    """Cards do topo: receitas, despesas correntes, poupanca e sobra."""
    poupanca_id = _id_poupanca(conn)
    consulta = (
        sa.select(
            db.categorias.c.natureza,
            db.transacoes.c.categoria_id,
            # natureza declarada pela origem; decide o lado quando falta categoria
            db.transacoes.c.natureza.label("natureza_origem"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa))
        .group_by(
            db.categorias.c.natureza,
            db.transacoes.c.categoria_id,
            db.transacoes.c.natureza,
        )
    )
    receitas = despesas = poupanca = sem_classe = 0
    for linha in conn.execute(consulta):
        total = int(linha.total or 0)
        if linha.categoria_id is None:
            sem_classe += total
            # sem categoria o sinal decide, a menos que a origem tenha dito de
            # que lado o lançamento está (estorno de despesa entra positivo)
            lado = linha.natureza_origem or ("receita" if total > 0 else "despesa")
            if lado == "receita":
                receitas += total
            else:
                despesas += -total
        elif linha.categoria_id == poupanca_id:
            poupanca += -total
        elif linha.natureza == "receita":
            receitas += total
        else:
            despesas += -total

    return {
        "receitas": receitas,
        "despesas": despesas,
        "poupanca": poupanca,
        "sobra": receitas - despesas - poupanca,
        "nao_classificado": sem_classe,
    }


def por_categoria(
    conn, competencia: str | None = None, ano: int | None = None,
    natureza: str = "despesa", pessoa: str | None = None,
) -> list[dict]:
    consulta = (
        sa.select(
            db.categorias.c.id,
            db.categorias.c.nome,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.count(db.transacoes.c.id).label("qtd"),
        )
        .select_from(
            db.transacoes.join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa), db.categorias.c.natureza == natureza)
        .group_by(db.categorias.c.id, db.categorias.c.nome)
    )
    linhas = [
        {
            "categoria_id": linha.id,
            "categoria": linha.nome,
            "total": abs(int(linha.total or 0)),
            "qtd": linha.qtd,
        }
        for linha in conn.execute(consulta)
    ]
    return sorted(linhas, key=lambda linha: -linha["total"])


def por_subcategoria(conn, categoria_id: int, competencia=None, ano=None, pessoa=None) -> list[dict]:
    consulta = (
        sa.select(
            db.subcategorias.c.nome,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.count(db.transacoes.c.id).label("qtd"),
        )
        .select_from(
            db.transacoes.join(
                db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id
            )
        )
        .where(*_base(competencia, ano, pessoa), db.transacoes.c.categoria_id == categoria_id)
        .group_by(db.subcategorias.c.nome)
    )
    linhas = [
        {"subcategoria": linha.nome, "total": abs(int(linha.total or 0)), "qtd": linha.qtd}
        for linha in conn.execute(consulta)
    ]
    return sorted(linhas, key=lambda linha: -linha["total"])


def serie_mensal(conn, ano: int, pessoa: str | None = None) -> list[dict]:
    """Receitas x despesas x poupanca por mes do ano."""
    poupanca_id = _id_poupanca(conn)
    consulta = (
        sa.select(
            db.transacoes.c.competencia,
            db.categorias.c.natureza,
            db.transacoes.c.categoria_id,
            db.transacoes.c.natureza.label("natureza_origem"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(ano=ano, pessoa=pessoa))
        .group_by(
            db.transacoes.c.competencia,
            db.categorias.c.natureza,
            db.transacoes.c.categoria_id,
            db.transacoes.c.natureza,
        )
    )
    meses: dict[str, dict] = {}
    for linha in conn.execute(consulta):
        alvo = meses.setdefault(
            linha.competencia, {"competencia": linha.competencia, "receitas": 0, "despesas": 0, "poupanca": 0}
        )
        total = int(linha.total or 0)
        if linha.categoria_id == poupanca_id and poupanca_id is not None:
            alvo["poupanca"] += -total
        elif linha.natureza == "receita" or (
            linha.categoria_id is None
            and (linha.natureza_origem or ("receita" if total > 0 else "despesa")) == "receita"
        ):
            alvo["receitas"] += total
        else:
            alvo["despesas"] += -total
    for alvo in meses.values():
        alvo["sobra"] = alvo["receitas"] - alvo["despesas"] - alvo["poupanca"]
    return sorted(meses.values(), key=lambda linha: linha["competencia"])


def tabela_mes_a_mes(conn, ano: int, pessoa: str | None = None) -> dict:
    """Matriz categoria x mes, com acumulado no ano e total do ano anterior."""
    consulta = (
        sa.select(
            db.categorias.c.nome,
            db.categorias.c.id,
            db.transacoes.c.competencia,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(ano=ano, pessoa=pessoa), db.categorias.c.natureza == "despesa")
        .group_by(db.categorias.c.nome, db.categorias.c.id, db.transacoes.c.competencia)
    )
    matriz: dict[str, dict[str, int]] = {}
    meses: set[str] = set()
    for linha in conn.execute(consulta):
        mes = linha.competencia[5:7]
        meses.add(mes)
        matriz.setdefault(linha.nome, {})[mes] = abs(int(linha.total or 0))

    anterior = {
        linha.nome: abs(int(linha.total or 0))
        for linha in conn.execute(
            sa.select(
                db.categorias.c.nome, sa.func.sum(db.transacoes.c.valor_centavos).label("total")
            )
            .select_from(
                db.transacoes.join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            )
            .where(*_base(ano=ano - 1, pessoa=pessoa), db.categorias.c.natureza == "despesa")
            .group_by(db.categorias.c.nome)
        )
    }

    ordem_meses = sorted(meses)
    linhas = []
    for categoria, valores in matriz.items():
        acumulado = sum(valores.values())
        linhas.append(
            {
                "categoria": categoria,
                "meses": {mes: valores.get(mes, 0) for mes in ordem_meses},
                "acumulado": acumulado,
                "media": acumulado // max(len(valores), 1),
                "ano_anterior": anterior.get(categoria, 0),
            }
        )
    linhas.sort(key=lambda linha: -linha["acumulado"])
    return {"meses": ordem_meses, "linhas": linhas, "ano": ano}


def comparativo_anual(conn, pessoa: str | None = None) -> list[dict]:
    """Um registro por ano, para o quadro ano a ano."""
    anos = [
        int(linha.competencia[:4])
        for linha in conn.execute(
            sa.select(db.transacoes.c.competencia)
            .where(db.transacoes.c.ativo == sa.true())
            .distinct()
        )
    ]
    saida = []
    for ano in sorted(set(anos)):
        dados = resumo(conn, ano=ano, pessoa=pessoa)
        dados["ano"] = ano
        saida.append(dados)
    return saida


def receitas_por_pessoa_e_tipo(conn, ano: int) -> dict:
    """Matriz de receitas: pessoa e tipo nas linhas, meses nas colunas.

    A pergunta da tela e "quanto cada um trouxe, de onde, em cada mes" — e isso
    nao se le numa lista de lancamentos misturados.
    """
    consulta = (
        sa.select(
            db.transacoes.c.pessoa,
            db.subcategorias.c.nome.label("tipo"),
            db.categorias.c.nome.label("categoria"),
            db.transacoes.c.competencia,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(ano=ano), db.categorias.c.natureza == "receita")
        .group_by(
            db.transacoes.c.pessoa, db.subcategorias.c.nome,
            db.categorias.c.nome, db.transacoes.c.competencia,
        )
    )
    linhas: dict[tuple[str, str], dict[str, int]] = {}
    meses: set[str] = set()
    for registro in conn.execute(consulta):
        mes = registro.competencia[5:7]
        meses.add(mes)
        chave = (registro.pessoa, registro.tipo or registro.categoria)
        alvo = linhas.setdefault(chave, {})
        alvo[mes] = alvo.get(mes, 0) + int(registro.total or 0)

    ordem = sorted(meses)
    saida = [
        {
            "pessoa": pessoa,
            "tipo": tipo,
            "meses": {mes: valores.get(mes, 0) for mes in ordem},
            "total": sum(valores.values()),
        }
        for (pessoa, tipo), valores in linhas.items()
    ]
    saida.sort(key=lambda linha: (linha["pessoa"], -linha["total"]))
    return {"meses": ordem, "linhas": saida}


def receitas_por_pessoa(conn, competencia=None, ano=None) -> list[dict]:
    consulta = (
        sa.select(
            db.transacoes.c.pessoa, sa.func.sum(db.transacoes.c.valor_centavos).label("total")
        )
        .select_from(
            db.transacoes.join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencia, ano), db.categorias.c.natureza == "receita")
        .group_by(db.transacoes.c.pessoa)
    )
    return sorted(
        [{"pessoa": linha.pessoa, "total": int(linha.total or 0)} for linha in conn.execute(consulta)],
        key=lambda linha: -linha["total"],
    )


def lancamentos(
    conn, competencia=None, ano=None, natureza=None, pessoa=None,
    categoria_id=None, limite: int = 500,
) -> list[dict]:
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.data,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.status,
            db.categorias.c.nome.label("categoria"),
            db.categorias.c.natureza,
            db.subcategorias.c.nome.label("subcategoria"),
            db.contas.c.nome.label("conta"),
        )
        .select_from(
            db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa))
        .order_by(db.transacoes.c.data.desc(), db.transacoes.c.id.desc())
        .limit(limite)
    )
    if natureza:
        consulta = consulta.where(db.categorias.c.natureza == natureza)
    if categoria_id:
        consulta = consulta.where(db.transacoes.c.categoria_id == categoria_id)
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def orcamento(conn, competencia: str, metas: dict[int, float], renda_base: int | None = None) -> list[dict]:
    """Realizado x meta por categoria de despesa (a meta e % da renda)."""
    if renda_base is None:
        renda_base = resumo(conn, competencia=competencia)["receitas"]
    gastos = {linha["categoria_id"]: linha for linha in por_categoria(conn, competencia=competencia)}
    poupanca_id = _id_poupanca(conn)
    if poupanca_id:
        total_poupanca = resumo(conn, competencia=competencia)["poupanca"]
        gastos.setdefault(
            poupanca_id,
            {"categoria_id": poupanca_id, "categoria": CATEGORIA_POUPANCA, "total": total_poupanca, "qtd": 0},
        )

    categorias = {
        linha.id: linha.nome
        for linha in conn.execute(
            sa.select(db.categorias.c.id, db.categorias.c.nome).where(
                db.categorias.c.natureza == "despesa", db.categorias.c.ativa == sa.true()
            )
        )
    }
    saida = []
    for categoria_id, nome in categorias.items():
        pct = metas.get(categoria_id, 0.0)
        meta = int(round(renda_base * pct / 100))
        real = gastos.get(categoria_id, {}).get("total", 0)
        # na poupanca a meta e piso, nao teto: passar dela e bom, ficar abaixo
        # e que merece atencao. Nas demais categorias vale o contrario.
        e_piso = categoria_id == poupanca_id
        saida.append(
            {
                "categoria_id": categoria_id,
                "categoria": nome,
                "percentual": pct,
                "meta": meta,
                "realizado": real,
                "uso": (real / meta * 100) if meta else None,
                "meta_e_piso": e_piso,
                "estourou": bool(meta and real > meta and not e_piso),
                "abaixo_do_piso": bool(meta and e_piso and real < meta),
            }
        )
    return sorted(saida, key=lambda linha: (-linha["percentual"], -linha["realizado"]))


def contexto_para_ia(conn, competencia: str) -> str:
    """Resumo numerico que alimenta a analise escrita."""
    from .money import fmt_brl

    ano = int(competencia[:4])
    atual = resumo(conn, competencia=competencia)
    linhas = [
        f"Competência: {competencia}",
        f"Receitas: {fmt_brl(atual['receitas'])}",
        f"Despesas correntes: {fmt_brl(atual['despesas'])}",
        f"Poupança/investimentos: {fmt_brl(atual['poupanca'])}",
        f"Sobra livre: {fmt_brl(atual['sobra'])}",
        "",
        "Gasto por categoria no mês:",
    ]
    metas = None
    for linha in por_categoria(conn, competencia=competencia):
        linhas.append(f"- {linha['categoria']}: {fmt_brl(linha['total'])} ({linha['qtd']} lançamentos)")
        for sub in por_subcategoria(conn, linha["categoria_id"], competencia=competencia)[:4]:
            linhas.append(f"    · {sub['subcategoria']}: {fmt_brl(sub['total'])}")

    linhas += ["", "Evolução dos últimos meses:"]
    for mes in serie_mensal(conn, ano)[-6:]:
        linhas.append(
            f"- {mes['competencia']}: receitas {fmt_brl(mes['receitas'])}, "
            f"despesas {fmt_brl(mes['despesas'])}, poupança {fmt_brl(mes['poupanca'])}"
        )

    from . import repo

    metas = repo.listar_metas(conn, ano)
    if metas:
        linhas += ["", "Metas do ano (% da renda) x realizado no mês:"]
        for item in orcamento(conn, competencia, metas):
            if item["percentual"]:
                uso = f"{item['uso']:.0f}%" if item["uso"] is not None else "—"
                linhas.append(
                    f"- {item['categoria']}: meta {item['percentual']:.0f}% "
                    f"({fmt_brl(item['meta'])}), realizado {fmt_brl(item['realizado'])} = {uso} da meta"
                )

    maiores = lancamentos(conn, competencia=competencia, natureza="despesa", limite=500)
    maiores = sorted(maiores, key=lambda linha: linha["valor_centavos"])[:10]
    if maiores:
        linhas += ["", "Dez maiores saídas do mês:"]
        for item in maiores:
            linhas.append(
                f"- {item['data']:%d/%m} {item['descricao'][:45]}: "
                f"{fmt_brl(abs(item['valor_centavos']))} ({item['categoria'] or 'sem categoria'})"
            )
    return "\n".join(linhas)
