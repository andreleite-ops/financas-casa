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

# O apartamento veio de heranca: e ganho de verdade, patrimonio que a casa nao
# tinha e passou a ter. Entra nas receitas, com todas as letras. O que ele nao
# e: mensal. Por isso fica de fora da renda que serve de base para o orcamento
# — meio milhao que acontece uma vez nao pode virar "renda do mes" e afrouxar
# todas as metas em % pelo ano inteiro. O criterio aqui e recorrencia, nao
# merito: o dinheiro e tao ganho quanto o salario, so nao se repete.
SUBCATEGORIAS_NAO_RECORRENTES = ("Venda de Bens",)

# Pagar a fatura do cartao nao e gasto: o gasto foram as compras, que ja estao
# na fatura. Mas o dinheiro aparece duas vezes — como credito na fatura
# ("Pagamento recebido") e como debito na conta corrente. Somando os dois sem
# cuidado, a despesa do mes dobra e nasce uma receita que nunca existiu.
#
# Esta categoria e o lugar dos dois lados. Ela nao entra em despesas nem em
# receitas; aparece a parte, como a poupanca, para o dinheiro continuar
# visivel sem contaminar o resultado do mes.
CATEGORIA_TRANSFERENCIA = "Transferências entre Contas"


def _id_poupanca(conn) -> int | None:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == CATEGORIA_POUPANCA)
    ).scalar()


def meses_decorridos(ano: int) -> int:
    """Quantos meses do ano já aconteceram.

    É este o divisor de qualquer média mensal. Dividir pelos meses que têm
    algum lançamento contava setembro a dezembro, que a planilha já traz
    agendados: 823 mil de despesa até agosto viravam uma "média" de 68 mil,
    quando a média real do que já se gastou é 103 mil. Ano passado divide por
    doze, porque doze meses aconteceram.
    """
    hoje = date.today()
    if ano < hoje.year:
        return 12
    if ano > hoje.year:
        return 1
    return hoje.month


def receitas_nao_recorrentes(
    conn, competencia: str | None = None, ano: int | None = None, pessoa: str | None = None
) -> int:
    """Quanto das receitas do periodo veio de venda de bem, e nao de renda."""
    total = conn.execute(
        sa.select(sa.func.sum(db.transacoes.c.valor_centavos))
        .select_from(
            db.transacoes.join(
                db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id
            )
        )
        .where(
            *_base(competencia, ano, pessoa),
            db.subcategorias.c.nome.in_(SUBCATEGORIAS_NAO_RECORRENTES),
        )
    ).scalar()
    return int(total or 0)


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
    """Cards do topo: receitas, despesas correntes, poupanca e sobra.

    Uma consulta só. Eram tres — esta, a que buscava o id da poupanca e a que
    somava a venda de bens —, e `resumo` e chamado varias vezes por tela: a
    Visao Geral gastava 27 idas ao banco, quase quatro segundos de espera com o
    Supabase em Sao Paulo. Trazendo o nome da categoria e da subcategoria na
    propria linha, a separacao acontece aqui, sem voltar ao banco.
    """
    consulta = (
        sa.select(
            db.categorias.c.natureza,
            db.categorias.c.nome.label("categoria"),
            db.transacoes.c.categoria_id,
            # natureza declarada pela origem; decide o lado quando falta categoria
            db.transacoes.c.natureza.label("natureza_origem"),
            db.subcategorias.c.nome.label("subcategoria"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa))
        .group_by(
            db.categorias.c.natureza,
            db.categorias.c.nome,
            db.transacoes.c.categoria_id,
            db.transacoes.c.natureza,
            db.subcategorias.c.nome,
        )
    )
    receitas = despesas = poupanca = sem_classe = nao_recorrentes = 0
    transferencias = 0
    for linha in conn.execute(consulta):
        total = int(linha.total or 0)
        if linha.subcategoria in SUBCATEGORIAS_NAO_RECORRENTES:
            nao_recorrentes += total
        if linha.categoria == CATEGORIA_TRANSFERENCIA:
            # dinheiro andando entre contas do mesmo dono: nem gasto nem ganho
            transferencias += total
            continue
        if linha.categoria_id is None:
            sem_classe += total
            # sem categoria o sinal decide, a menos que a origem tenha dito de
            # que lado o lançamento está (estorno de despesa entra positivo)
            lado = linha.natureza_origem or ("receita" if total > 0 else "despesa")
            if lado == "receita":
                receitas += total
            else:
                despesas += -total
        elif linha.categoria == CATEGORIA_POUPANCA:
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
        # venda de bem entra em "receitas" (o dinheiro entrou), mas fica de fora
        # daqui: e esta linha que o orçamento usa como renda
        "receitas_nao_recorrentes": nao_recorrentes,
        "renda_recorrente": receitas - nao_recorrentes,
        # o que só mudou de bolso: fica visível, fora dos dois totais
        "transferencias": transferencias,
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
        .where(
            *_base(competencia, ano, pessoa),
            db.categorias.c.natureza == natureza,
            # transferência entre contas não é gasto nem ganho: é o mesmo
            # dinheiro mudando de bolso. Deixá-la aqui punha o pagamento da
            # fatura como a maior barra do gráfico, virava meta de orçamento
            # sugerida e não fechava com o card do topo, que já a exclui.
            db.categorias.c.nome != CATEGORIA_TRANSFERENCIA,
        )
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
    """Abertura de uma categoria pelas subcategorias dela.

    Junção externa de propósito: o que está na categoria sem subcategoria é
    justamente o que falta detalhar, e escondê-lo faria a soma das partes ficar
    menor que o total sem explicação nenhuma.
    """
    consulta = (
        sa.select(
            db.subcategorias.c.nome,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.count(db.transacoes.c.id).label("qtd"),
        )
        .select_from(
            db.transacoes.outerjoin(
                db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id
            )
        )
        .where(*_base(competencia, ano, pessoa), db.transacoes.c.categoria_id == categoria_id)
        .group_by(db.subcategorias.c.nome)
    )
    linhas = [
        {
            "subcategoria": linha.nome or "— sem subcategoria —",
            "detalhada": linha.nome is not None,
            "total": abs(int(linha.total or 0)),
            "qtd": linha.qtd,
        }
        for linha in conn.execute(consulta)
    ]
    return sorted(linhas, key=lambda linha: -linha["total"])


def subcategorias_de_todas(conn, competencia=None, ano=None, pessoa=None) -> dict[int, list[dict]]:
    """O mesmo de por_subcategoria, para todas as categorias de uma vez.

    Existe para o contexto da IA, que abre cada categoria do mês nas
    subcategorias dela: uma consulta por categoria eram catorze idas ao banco
    para montar um texto. Aqui é uma, agrupada também por categoria_id.

    Mantém a junção externa e a ordem por total decrescente da versão unitária
    — quem chama corta as primeiras e precisa que sejam as maiores.
    """
    consulta = (
        sa.select(
            db.transacoes.c.categoria_id,
            db.subcategorias.c.nome,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.count(db.transacoes.c.id).label("qtd"),
        )
        .select_from(
            db.transacoes.outerjoin(
                db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id
            )
        )
        .where(*_base(competencia, ano, pessoa))
        .group_by(db.transacoes.c.categoria_id, db.subcategorias.c.nome)
    )
    saida: dict[int, list[dict]] = {}
    for linha in conn.execute(consulta):
        saida.setdefault(linha.categoria_id, []).append({
            "subcategoria": linha.nome or "— sem subcategoria —",
            "detalhada": linha.nome is not None,
            "total": abs(int(linha.total or 0)),
            "qtd": linha.qtd,
        })
    return {
        categoria_id: sorted(linhas, key=lambda linha: -linha["total"])
        for categoria_id, linhas in saida.items()
    }


def serie_por_subcategoria(conn, categoria_id: int, ano: int, pessoa=None) -> dict:
    """Subcategoria × mês dentro de uma categoria — a categoria explodida."""
    consulta = (
        sa.select(
            db.transacoes.c.competencia,
            db.subcategorias.c.nome,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.outerjoin(
                db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id
            )
        )
        .where(*_base(ano=ano, pessoa=pessoa), db.transacoes.c.categoria_id == categoria_id)
        .group_by(db.transacoes.c.competencia, db.subcategorias.c.nome)
    )
    matriz: dict[str, dict[str, int]] = {}
    meses: set[str] = set()
    for linha in conn.execute(consulta):
        mes = linha.competencia[5:7]
        meses.add(mes)
        nome = linha.nome or "— sem subcategoria —"
        matriz.setdefault(nome, {})[mes] = abs(int(linha.total or 0))

    ordem = sorted(meses)
    decorridos = meses_decorridos(ano)
    linhas = []
    for nome, valores in matriz.items():
        acumulado = sum(valores.values())
        linhas.append({
            "categoria": nome,
            "meses": {mes: valores.get(mes, 0) for mes in ordem},
            "acumulado": acumulado,
            "media": acumulado // max(decorridos, 1),
            "ano_anterior": 0,
        })
    linhas.sort(key=lambda linha: -linha["acumulado"])
    return {"meses": ordem, "linhas": linhas}


def serie_mensal(conn, ano: int, pessoa: str | None = None) -> list[dict]:
    """Receitas x despesas x poupanca por mes do ano.

    O nome da categoria vem junto, para a poupanca ser separada aqui em vez de
    custar outra ida ao banco so para descobrir o id dela.
    """
    consulta = (
        sa.select(
            db.transacoes.c.competencia,
            db.categorias.c.natureza,
            db.categorias.c.nome.label("categoria"),
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
            db.categorias.c.nome,
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
        if linha.categoria == CATEGORIA_TRANSFERENCIA:
            continue
        if linha.categoria == CATEGORIA_POUPANCA:
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


SEM_CATEGORIA = "— sem categoria —"


def _consulta_da_matriz(ano: int, pessoa: str | None):
    """Base da matriz de despesas: junção externa, para o pendente entrar.

    Junção interna deixava de fora o que ainda não tem categoria — e era
    justamente esse o dinheiro que fazia a tabela não fechar com o card do
    topo, que sempre contou o pendente.
    """
    return (
        sa.select(
            db.categorias.c.nome,
            db.categorias.c.natureza,
            db.transacoes.c.natureza.label("natureza_origem"),
            db.transacoes.c.categoria_id,
            db.transacoes.c.competencia,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.outerjoin(
                db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id
            )
        )
        .where(*_base(ano=ano, pessoa=pessoa))
        .group_by(
            db.categorias.c.nome, db.categorias.c.natureza, db.transacoes.c.natureza,
            db.transacoes.c.categoria_id, db.transacoes.c.competencia,
        )
    )


def _nome_da_linha(linha) -> str | None:
    """Em que linha da matriz este grupo entra — None quando não é despesa.

    Transferência entre contas fica de fora pelo mesmo motivo que fica fora do
    card: não é gasto, é o mesmo dinheiro mudando de bolso. Deixá-la aqui punha
    o pagamento da fatura como a maior "despesa" do ano.
    """
    if linha.nome == CATEGORIA_TRANSFERENCIA:
        return None
    if linha.categoria_id is None:
        total = int(linha.total or 0)
        lado = linha.natureza_origem or ("receita" if total > 0 else "despesa")
        return SEM_CATEGORIA if lado == "despesa" else None
    return linha.nome if linha.natureza == "despesa" else None


def tabela_mes_a_mes(conn, ano: int, pessoa: str | None = None) -> dict:
    """Matriz categoria x mes, com acumulado no ano e total do ano anterior.

    Fecha com o card de despesas do topo: mesma exclusão de transferências e
    mesma inclusão do que ainda não foi classificado.
    """
    matriz: dict[str, dict[str, int]] = {}
    meses: set[str] = set()
    for linha in conn.execute(_consulta_da_matriz(ano, pessoa)):
        nome = _nome_da_linha(linha)
        if nome is None:
            continue
        mes = linha.competencia[5:7]
        meses.add(mes)
        acumulado = matriz.setdefault(nome, {})
        acumulado[mes] = acumulado.get(mes, 0) + abs(int(linha.total or 0))

    anterior: dict[str, int] = {}
    for linha in conn.execute(_consulta_da_matriz(ano - 1, pessoa)):
        nome = _nome_da_linha(linha)
        if nome is None:
            continue
        anterior[nome] = anterior.get(nome, 0) + abs(int(linha.total or 0))

    ordem_meses = sorted(meses)
    meses_ja_decorridos = meses_decorridos(ano)
    linhas = []
    for categoria, valores in matriz.items():
        acumulado = sum(valores.values())
        linhas.append(
            {
                "categoria": categoria,
                "meses": {mes: valores.get(mes, 0) for mes in ordem_meses},
                "acumulado": acumulado,
                # divide pelos meses que já aconteceram, não pelos meses em
                # que esta categoria teve gasto: uma conta que só apareceu em
                # dois meses tem média baixa no ano, e é isso que se quer saber
                "media": acumulado // max(meses_ja_decorridos, 1),
                "ano_anterior": anterior.get(categoria, 0),
            }
        )
    linhas.sort(key=lambda linha: -linha["acumulado"])
    return {"meses": ordem_meses, "linhas": linhas, "ano": ano}


def meses_com_despesa(conn) -> set[str]:
    """Competências que já têm algum gasto lançado.

    Serve para a tela abrir num mês que aconteceu. A planilha traz lançamento
    agendado até dezembro, então o mês mais recente da base costuma ser um mês
    vazio de despesa.
    """
    consulta = (
        sa.select(db.transacoes.c.competencia)
        .where(*_base(), db.transacoes.c.valor_centavos < 0)
        .distinct()
    )
    return {linha.competencia for linha in conn.execute(consulta)}


def comparativo_anual(conn, pessoa: str | None = None) -> list[dict]:
    """Um registro por ano, para o quadro ano a ano.

    Uma consulta para todos os anos. Chamar `resumo` num laço custava quatro
    idas ao banco por ano — e o quadro existe justamente para quando houver
    muitos anos.
    """
    ano_sql = sa.func.substr(db.transacoes.c.competencia, 1, 4).label("ano")
    consulta = (
        sa.select(
            ano_sql,
            db.categorias.c.natureza,
            db.categorias.c.nome.label("categoria"),
            db.transacoes.c.categoria_id,
            db.transacoes.c.natureza.label("natureza_origem"),
            db.subcategorias.c.nome.label("subcategoria"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(pessoa=pessoa))
        .group_by(
            ano_sql, db.categorias.c.natureza, db.categorias.c.nome,
            db.transacoes.c.categoria_id, db.transacoes.c.natureza,
            db.subcategorias.c.nome,
        )
    )
    por_ano: dict[int, dict] = {}
    for linha in conn.execute(consulta):
        ano = int(linha.ano)
        alvo = por_ano.setdefault(ano, {
            "ano": ano, "receitas": 0, "despesas": 0, "poupanca": 0,
            "nao_classificado": 0, "receitas_nao_recorrentes": 0,
        })
        total = int(linha.total or 0)
        if linha.subcategoria in SUBCATEGORIAS_NAO_RECORRENTES:
            alvo["receitas_nao_recorrentes"] += total
        if linha.categoria == CATEGORIA_TRANSFERENCIA:
            continue
        if linha.categoria_id is None:
            alvo["nao_classificado"] += total
            lado = linha.natureza_origem or ("receita" if total > 0 else "despesa")
            alvo["receitas" if lado == "receita" else "despesas"] += (
                total if lado == "receita" else -total
            )
        elif linha.categoria == CATEGORIA_POUPANCA:
            alvo["poupanca"] += -total
        elif linha.natureza == "receita":
            alvo["receitas"] += total
        else:
            alvo["despesas"] += -total

    for alvo in por_ano.values():
        alvo["sobra"] = alvo["receitas"] - alvo["despesas"] - alvo["poupanca"]
        alvo["renda_recorrente"] = alvo["receitas"] - alvo["receitas_nao_recorrentes"]
    return [por_ano[ano] for ano in sorted(por_ano)]


def receitas_por_pessoa_e_tipo(conn, ano: int) -> dict:
    """Matriz de receitas: pessoa, fonte e tipo nas linhas, meses nas colunas.

    A pergunta da tela e "quanto cada um trouxe, de onde, em cada mes" — e isso
    nao se le numa lista de lancamentos misturados. A fonte e o nome que a casa
    usa (TAG, BIOS, NUN); sem ela, o pro-labore do Andre e o da Ro apareceriam
    com o mesmo rotulo e a origem do dinheiro sumiria.
    """
    consulta = (
        sa.select(
            db.transacoes.c.pessoa,
            db.transacoes.c.classificacao_origem.label("fonte"),
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
            db.transacoes.c.pessoa, db.transacoes.c.classificacao_origem,
            db.subcategorias.c.nome, db.categorias.c.nome, db.transacoes.c.competencia,
        )
    )
    linhas: dict[tuple[str, str, str], dict[str, int]] = {}
    meses: set[str] = set()
    for registro in conn.execute(consulta):
        mes = registro.competencia[5:7]
        meses.add(mes)
        chave = (
            registro.pessoa,
            (registro.fonte or "—").strip() or "—",
            registro.tipo or registro.categoria,
        )
        alvo = linhas.setdefault(chave, {})
        alvo[mes] = alvo.get(mes, 0) + int(registro.total or 0)

    ordem = sorted(meses)
    saida = [
        {
            "pessoa": pessoa,
            "fonte": fonte,
            "tipo": tipo,
            "meses": {mes: valores.get(mes, 0) for mes in ordem},
            "total": sum(valores.values()),
        }
        for (pessoa, fonte, tipo), valores in linhas.items()
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


def orcamento(
    conn, competencia: str, metas: dict[int, float], renda_base: int | None = None,
    resumo_do_mes: dict | None = None, gastos_do_mes: list[dict] | None = None,
) -> list[dict]:
    """Realizado x meta por categoria de despesa (a meta e % da renda).

    `resumo_do_mes` e `gastos_do_mes` evitam recalcular o que a tela ja tem em
    maos: sem eles, esta funcao sozinha refazia o resumo duas vezes e respondia
    por nove das vinte e sete consultas da Visao Geral. O gasto por categoria e
    o mesmo que a tela ja mostra na tabela ao lado.
    """
    do_mes = resumo_do_mes or resumo(conn, competencia=competencia)
    if renda_base is None:
        renda_base = do_mes["receitas"]
    if gastos_do_mes is None:
        gastos_do_mes = por_categoria(conn, competencia=competencia)
    gastos = {linha["categoria_id"]: linha for linha in gastos_do_mes}
    poupanca_id = _id_poupanca(conn)
    if poupanca_id:
        total_poupanca = do_mes["poupanca"]
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


def cobertura_da_classificacao(conn, competencia: str) -> dict:
    """Quanto do mês já está classificado — e em que profundidade.

    É o primeiro número que a análise precisa saber. Um mês com metade dos
    lançamentos na fila permite dizer "de tudo que já foi classificado, o
    mercado é o maior gasto"; não permite dizer "o maior gasto do mês é o
    mercado". Sem esta conta, a IA escreve a segunda frase com a confiança da
    primeira, e quem lê decide a vida com base nela.
    """
    return cobertura_por_competencia(conn, [competencia])[competencia]


def _consulta_de_cobertura(competencias: list[str]):
    return (
        sa.select(
            db.transacoes.c.competencia,
            db.transacoes.c.categoria_id,
            db.transacoes.c.subcategoria_id,
            db.categorias.c.nome.label("categoria"),
            sa.func.count().label("qtd"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.outerjoin(
                db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id
            )
        )
        .where(
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.competencia.in_(competencias),
            db.transacoes.c.valor_centavos < 0,
        )
        .group_by(
            db.transacoes.c.competencia, db.transacoes.c.categoria_id,
            db.transacoes.c.subcategoria_id, db.categorias.c.nome,
        )
    )


def _somar_cobertura(linhas) -> dict:
    total = classificado = com_sub = 0
    qtd_total = qtd_sem_categoria = qtd_sem_sub = 0
    for linha in linhas:
        if linha.categoria == CATEGORIA_TRANSFERENCIA:
            continue
        valor, qtd = abs(int(linha.total or 0)), int(linha.qtd)
        total += valor
        qtd_total += qtd
        if linha.categoria_id is None:
            qtd_sem_categoria += qtd
            continue
        classificado += valor
        if linha.subcategoria_id is None:
            qtd_sem_sub += qtd
        else:
            com_sub += valor

    return {
        "gasto_total": total,
        "gasto_classificado": classificado,
        "gasto_sem_categoria": total - classificado,
        "gasto_com_subcategoria": com_sub,
        "lancamentos": qtd_total,
        "sem_categoria": qtd_sem_categoria,
        "sem_subcategoria": qtd_sem_sub,
        "percentual_classificado": round(100 * classificado / total, 1) if total else 100.0,
        "percentual_com_subcategoria": round(100 * com_sub / total, 1) if total else 100.0,
    }


def cobertura_por_competencia(conn, competencias: list[str]) -> dict[str, dict]:
    """A mesma cobertura, para vários meses de uma vez.

    A leitura do ano pergunta isso de cada um dos doze meses da janela para
    dizer quais ainda estão pela metade. Doze chamadas eram doze idas ao banco
    com a mesma pergunta mudando o mês; aqui é uma, agrupada por competência.

    Mês sem gasto nenhum não volta do GROUP BY e mesmo assim precisa de uma
    resposta — a de um mês vazio, que é 100% classificado por vacuidade.
    """
    if not competencias:
        return {}
    agrupado: dict[str, list] = {mes: [] for mes in competencias}
    for linha in conn.execute(_consulta_de_cobertura(list(competencias))):
        agrupado.setdefault(linha.competencia, []).append(linha)
    return {mes: _somar_cobertura(linhas) for mes, linhas in agrupado.items()}


def desvios_do_mes(conn, competencia: str, minimo_de_meses: int = 2) -> list[dict]:
    """Categorias que fugiram do próprio histórico neste mês.

    A média aqui **não** é a do orçamento (acumulado ÷ meses decorridos): é a
    dos outros meses em que aquela categoria teve gasto. As duas respondem
    perguntas diferentes, e usar a do orçamento aqui dizia que tudo subiu 700%
    quando só existia um mês importado — um alarme que dispara sempre não avisa
    nada.

    Exige pelo menos dois outros meses com gasto: comparar um mês contra um
    único outro mês é comparar dois pontos e chamar de tendência.
    """
    ano = int(competencia[:4])
    mes_atual = competencia[5:7]
    saida = []
    for linha in tabela_mes_a_mes(conn, ano)["linhas"]:
        if linha["categoria"] == CATEGORIA_TRANSFERENCIA:
            continue                       # não é gasto: não tem padrão a fugir
        no_mes = linha["meses"].get(mes_atual, 0)
        outros = [v for mes, v in linha["meses"].items() if mes != mes_atual and v]
        if not no_mes or len(outros) < minimo_de_meses:
            continue
        media = sum(outros) // len(outros)
        if not media:
            continue
        variacao = round(100 * (no_mes - media) / media)
        if abs(variacao) >= 30:
            saida.append({
                "categoria": linha["categoria"],
                "no_mes": no_mes,
                "media": media,
                "meses_comparados": len(outros),
                "variacao": variacao,
                "diferenca": abs(no_mes - media),
            })
    saida.sort(key=lambda item: -item["diferenca"])
    return saida


def janela_de_doze_meses(conn, ate: str) -> list[str]:
    """As competências da janela de análise longa, terminando em `ate`.

    Enquanto houver menos de doze meses de histórico, a janela é o que existe —
    e o texto tem de dizer isso. Passando de doze, ela anda: dezembro deixa de
    ser um corte natural, e comparar agosto com agosto passa a valer mais do
    que comparar agosto com janeiro.
    """
    todas = sorted(
        linha.competencia
        for linha in conn.execute(
            sa.select(db.transacoes.c.competencia)
            .where(db.transacoes.c.ativo == sa.true(), db.transacoes.c.competencia.isnot(None))
            .distinct()
        )
    )
    ate_ou_ultima = [c for c in todas if c <= ate]
    return ate_ou_ultima[-12:]


def resumo_do_ano(conn, competencia: str) -> dict:
    """O ano até aqui: acumulado, média mensal e a posição deste mês.

    A média usa a régua da casa — acumulado ÷ meses decorridos —, não a média
    dos meses que tiveram gasto. Uma conta que só apareceu em dois meses tem de
    puxar a média do ano para baixo, porque é isso que sobra para o orçamento.
    """
    ano = int(competencia[:4])
    meses = serie_mensal(conn, ano)
    decorridos = meses_decorridos(ano)
    receitas = sum(m["receitas"] for m in meses)
    despesas = sum(m["despesas"] for m in meses)
    poupanca = sum(m["poupanca"] for m in meses)

    por_despesa = sorted(meses, key=lambda m: -m["despesas"])
    posicao = next(
        (i + 1 for i, m in enumerate(por_despesa) if m["competencia"] == competencia), None
    )
    do_mes = next((m for m in meses if m["competencia"] == competencia), None)
    return {
        "ano": ano,
        "meses_com_dados": len(meses),
        "meses_decorridos": decorridos,
        "receitas": receitas,
        "despesas": despesas,
        "poupanca": poupanca,
        "media_despesa": despesas // max(decorridos, 1),
        "media_receita": receitas // max(decorridos, 1),
        "taxa_de_poupanca": round(100 * poupanca / receitas, 1) if receitas else 0.0,
        "posicao_do_mes": posicao,
        "despesa_do_mes": do_mes["despesas"] if do_mes else 0,
    }


def contexto_do_ano(conn, ate: str) -> str:
    """Os números da leitura longa: padrão, sazonalidade e o que é fixo.

    O mês responde "para onde foi o dinheiro". Só a série responde "isto
    acontece todo ano nesta época" — e é essa a diferença entre reagir ao mês e
    planejar o ano.
    """
    from .money import fmt_brl

    competencias = janela_de_doze_meses(conn, ate)
    if not competencias:
        return f"Sem lançamentos até {ate}."

    ano = int(ate[:4])
    resumo_ano = resumo_do_ano(conn, ate)
    fechada = len(competencias) >= 12

    linhas = [
        f"Janela: {competencias[0]} a {competencias[-1]} "
        f"({len(competencias)} {'meses' if len(competencias) > 1 else 'mês'} com lançamento)",
    ]
    if not fechada:
        linhas.append(
            "ATENÇÃO: menos de doze meses de histórico. Dá para descrever o que houve; "
            "NÃO dá para afirmar sazonalidade — para isso é preciso ver o mesmo mês "
            "repetir em anos diferentes. Diga isso ao falar de padrão anual."
        )

    linhas += [
        "",
        "Acumulado do ano:",
        f"- receitas {fmt_brl(resumo_ano['receitas'])}, "
        f"despesas {fmt_brl(resumo_ano['despesas'])}, "
        f"poupança {fmt_brl(resumo_ano['poupanca'])}",
        f"- média mensal (acumulado ÷ {resumo_ano['meses_decorridos']} meses decorridos): "
        f"receitas {fmt_brl(resumo_ano['media_receita'])}, "
        f"despesas {fmt_brl(resumo_ano['media_despesa'])}",
        f"- taxa de poupança sobre a receita do ano: {resumo_ano['taxa_de_poupanca']:.1f}%",
        "",
        "Mês a mês:",
    ]
    for mes in serie_mensal(conn, ano):
        if mes["competencia"] not in competencias:
            continue
        linhas.append(
            f"- {mes['competencia']}: receitas {fmt_brl(mes['receitas'])}, "
            f"despesas {fmt_brl(mes['despesas'])}, poupança {fmt_brl(mes['poupanca'])}"
        )

    tabela = tabela_mes_a_mes(conn, ano)
    if tabela["linhas"]:
        linhas += [
            "",
            "Gasto por categoria, mês a mês (é aqui que a sazonalidade aparece — "
            "meses vazios significam que não houve gasto naquele mês):",
        ]
        cabecalho = " | ".join(tabela["meses"])
        linhas.append(f"  categoria: {cabecalho} | média/mês | pico")
        for linha in tabela["linhas"][:14]:
            if linha["categoria"] == CATEGORIA_TRANSFERENCIA:
                continue
            valores = [linha["meses"].get(mes, 0) for mes in tabela["meses"]]
            pico = max(zip(valores, tabela["meses"]), default=(0, "—"))
            linhas.append(
                f"  {linha['categoria']}: "
                + " | ".join(fmt_brl(v) for v in valores)
                + f" | {fmt_brl(linha['media'])} | maior em {pico[1]}"
            )

    fixos = compromissos_recorrentes(conn, ate)
    if fixos:
        total_fixo = sum(item["media"] for item in fixos)
        linhas += [
            "",
            f"Compromissos que se repetem (3 meses ou mais): {fmt_brl(total_fixo)} por mês "
            "somados — é o piso do orçamento, o que não muda decidindo mês a mês:",
        ]
        for item in fixos[:15]:
            linhas.append(
                f"- {item['estabelecimento']}: {fmt_brl(item['media'])}/mês em "
                f"{item['meses']} meses"
            )

    anterior = [item for item in comparativo_anual(conn) if item.get("ano") == ano - 1]
    if anterior:
        linhas += ["", "Ano anterior, para comparar:"]
        for item in anterior:
            linhas.append(
                f"- {item['ano']}: receitas {fmt_brl(item.get('receitas', 0))}, "
                f"despesas {fmt_brl(item.get('despesas', 0))}"
            )

    # a cobertura dos doze meses numa consulta só: uma por mês eram doze idas
    # ao banco para descobrir quais meses ainda estão pela metade
    coberturas = cobertura_por_competencia(conn, competencias)
    faltando = [
        competencia for competencia in competencias
        if coberturas[competencia]["percentual_classificado"] < 95
    ]
    if faltando:
        linhas += [
            "",
            "COBERTURA: estes meses ainda não estão classificados por inteiro — "
            + ", ".join(faltando)
            + ". Os totais deles são parciais e as comparações com os outros meses "
            "ficam prejudicadas. Diga isso antes de apontar tendência.",
        ]
    return "\n".join(linhas)


def contexto_para_ia(conn, competencia: str) -> str:
    """Resumo numerico que alimenta a analise escrita.

    Tudo o que a IA pode dizer sai daqui — ela nao consulta o banco. Por isso
    este texto carrega tambem o que *nao* se sabe: quanto do mes ainda esta na
    fila, quanto esta sem subcategoria e o que ficou de fora dos totais.
    """
    from .money import fmt_brl

    ano = int(competencia[:4])
    atual = resumo(conn, competencia=competencia)
    cobertura = cobertura_da_classificacao(conn, competencia)
    linhas = [
        f"Competência: {competencia}",
        f"Receitas: {fmt_brl(atual['receitas'])}",
        f"Despesas correntes: {fmt_brl(atual['despesas'])}",
        f"Poupança/investimentos: {fmt_brl(atual['poupanca'])}",
        f"Sobra livre: {fmt_brl(atual['sobra'])}",
    ]
    if atual["receitas_nao_recorrentes"]:
        linhas.append(
            f"Dentro das receitas, {fmt_brl(atual['receitas_nao_recorrentes'])} é venda de bem "
            "(entrada de uma vez só, não é renda do mês)."
        )
    if atual["transferencias"]:
        linhas.append(
            f"Transferências entre contas do casal no mês: {fmt_brl(atual['transferencias'])} "
            "— dinheiro que só mudou de bolso, fora das receitas e das despesas."
        )

    linhas += [
        "",
        "COBERTURA DA CLASSIFICAÇÃO (leia antes de concluir qualquer coisa):",
        f"- do gasto do mês, {cobertura['percentual_classificado']:.0f}% está classificado "
        f"({fmt_brl(cobertura['gasto_classificado'])} de {fmt_brl(cobertura['gasto_total'])})",
        f"- ainda na fila, sem categoria: {cobertura['sem_categoria']} lançamentos, "
        f"{fmt_brl(cobertura['gasto_sem_categoria'])}",
        f"- classificados só até a categoria, sem subcategoria: "
        f"{cobertura['sem_subcategoria']} lançamentos",
    ]

    linhas += ["", "Gasto por categoria no mês:"]
    # as subcategorias de todas as categorias numa consulta só: uma por
    # categoria eram catorze idas ao banco para montar este mesmo texto
    abertura = subcategorias_de_todas(conn, competencia=competencia)
    for linha in por_categoria(conn, competencia=competencia):
        linhas.append(f"- {linha['categoria']}: {fmt_brl(linha['total'])} ({linha['qtd']} lançamentos)")
        for sub in abertura.get(linha["categoria_id"], [])[:4]:
            linhas.append(f"    · {sub['subcategoria']}: {fmt_brl(sub['total'])}")

    fora_do_padrao = desvios_do_mes(conn, competencia)
    if fora_do_padrao:
        linhas += ["", "Fora do padrão neste mês:"]
        for item in fora_do_padrao[:8]:
            sinal = "+" if item["variacao"] > 0 else ""
            linhas.append(
                f"- {item['categoria']}: {fmt_brl(item['no_mes'])} no mês contra "
                f"{fmt_brl(item['media'])} de média nos outros {item['meses_comparados']} "
                f"meses ({sinal}{item['variacao']}%)"
            )
    else:
        linhas += [
            "",
            "Ainda não há meses suficientes para dizer o que fugiu do padrão: uma "
            "categoria precisa de pelo menos três meses com gasto para ter média. Não "
            "trate o valor deste mês como alto ou baixo sem essa comparação.",
        ]

    linhas += ["", "Por pessoa neste mês (sem dono declarado = Casal):"]
    for quem in db.PESSOAS:
        da_pessoa = resumo(conn, competencia=competencia, pessoa=quem)
        linhas.append(
            f"- {quem}: despesas {fmt_brl(da_pessoa['despesas'])}, "
            f"receitas {fmt_brl(da_pessoa['receitas'])}"
        )

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
            # meta em reais zerada = ainda não há renda lançada no mês; a linha
            # só diria "0% de R$ 0,00" e convidaria a IA a concluir do nada
            if item["percentual"] and item["meta"]:
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

    fixos = compromissos_recorrentes(conn, competencia)
    if fixos:
        linhas += [
            "",
            "Compromissos que se repetem todo mês (mesmo estabelecimento em 3 meses ou mais):",
        ]
        for item in fixos[:12]:
            linhas.append(
                f"- {item['estabelecimento']}: {fmt_brl(item['media'])}/mês, "
                f"visto em {item['meses']} meses"
            )

    # o mês sozinho não diz se foi um mês caro: diz quanto se gastou. Sem o
    # acumulado e a média ao lado, "gastamos 126 mil" não tem régua nenhuma.
    do_ano = resumo_do_ano(conn, competencia)
    linhas += [
        "",
        f"O ano até aqui ({do_ano['ano']}, {do_ano['meses_com_dados']} meses com lançamento):",
        f"- acumulado: receitas {fmt_brl(do_ano['receitas'])}, "
        f"despesas {fmt_brl(do_ano['despesas'])}, poupança {fmt_brl(do_ano['poupanca'])}",
        f"- média mensal (acumulado ÷ {do_ano['meses_decorridos']} meses decorridos): "
        f"{fmt_brl(do_ano['media_despesa'])} de despesa, "
        f"{fmt_brl(do_ano['media_receita'])} de receita",
        f"- taxa de poupança no ano: {do_ano['taxa_de_poupanca']:.1f}% da receita",
    ]
    if do_ano["posicao_do_mes"]:
        linhas.append(
            f"- este mês é o {do_ano['posicao_do_mes']}º mais caro entre os "
            f"{do_ano['meses_com_dados']} meses com lançamento"
        )
    return "\n".join(linhas)


def compromissos_recorrentes(conn, competencia: str, minimo_de_meses: int = 3) -> list[dict]:
    """Gasto que aparece todo mês, pelo nome do estabelecimento.

    Separa o que é escolha do mês do que é compromisso assumido. Cortar R$ 200
    de restaurante é decisão de uma semana; cortar R$ 200 de assinatura é
    decisão de uma vez que vale o ano inteiro — e é essa a sugestão que vale a
    pena receber.
    """
    from .texto import chave_estabelecimento

    ano = int(competencia[:4])
    consulta = (
        sa.select(
            db.transacoes.c.descricao,
            db.transacoes.c.competencia,
            db.transacoes.c.valor_centavos,
        )
        .where(*_base(ano=ano), db.transacoes.c.valor_centavos < 0)
    )
    por_chave: dict[str, dict] = {}
    for linha in conn.execute(consulta):
        chave = chave_estabelecimento(linha.descricao)
        if not chave or len(chave) < 4:
            continue
        registro = por_chave.setdefault(
            chave, {"estabelecimento": chave, "meses": set(), "total": 0, "no_mes": 0}
        )
        registro["meses"].add(linha.competencia)
        registro["total"] += abs(int(linha.valor_centavos))
        if linha.competencia == competencia:
            registro["no_mes"] += abs(int(linha.valor_centavos))

    saida = [
        {
            "estabelecimento": r["estabelecimento"],
            "meses": len(r["meses"]),
            "total": r["total"],
            "media": r["total"] // len(r["meses"]),
            "no_mes": r["no_mes"],
        }
        for r in por_chave.values()
        if len(r["meses"]) >= minimo_de_meses
    ]
    saida.sort(key=lambda item: -item["total"])
    return saida
