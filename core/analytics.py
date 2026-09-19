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
# a compra de cartao datada num mes que e so da planilha fica desligada com
# esta marca (ver repo.aplicar_meses_da_planilha); mora aqui porque cartoes
# e repo precisam dela e repo importa os dois
MARCA_MES_DA_PLANILHA = "mês da planilha: a compra já está anotada nela"


def _id_poupanca(conn) -> int | None:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == CATEGORIA_POUPANCA)
    ).scalar()


def mes_anterior(competencia: str) -> str:
    """O mes anterior no calendario — nao "o anterior que tem lancamento".

    A diferenca entre os dois so aparece quando ha buraco na serie, e e
    justamente ai que ela engana: comparar setembro com julho sob o rotulo
    "mes anterior" faz um mes normal parecer o dobro do que foi.
    """
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    return f"{ano - 1:04d}-12" if mes == 1 else f"{ano:04d}-{mes - 1:02d}"


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


def _sinal():
    """+1 para entrada, -1 para saida — para agrupar sem misturar os lados."""
    return sa.case((db.transacoes.c.valor_centavos > 0, 1), else_=-1)


def _base(competencia: str | None = None, ano: int | None = None, pessoa: str | None = None,
          competencias: list[str] | None = None):
    """Os filtros de sempre. `competencias` e a janela que atravessa o ano.

    A leitura longa pode ser o ano civil ou os ultimos doze meses, e doze
    meses terminando em agosto pegam dois anos. Sem esta porta, toda consulta
    da analise sabia responder so por ano.
    """
    filtros = [db.transacoes.c.ativo == sa.true()]
    if competencia:
        filtros.append(db.transacoes.c.competencia == competencia)
    if ano:
        filtros.append(db.transacoes.c.competencia.like(f"{ano}-%"))
    if competencias:
        filtros.append(db.transacoes.c.competencia.in_(list(competencias)))
    if pessoa and pessoa != "Todos":
        filtros.append(db.transacoes.c.pessoa == pessoa)
    return filtros


def resumo(conn, competencia: str | None = None, ano: int | None = None,
           pessoa: str | None = None, competencias: list[str] | None = None) -> dict:
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
            # o sinal entra no agrupamento de proposito: sem ele, a entrada e a
            # saida ainda sem categoria caiam no mesmo grupo e se anulavam —
            # um PIX de R$ 20.000 enviado sumia dentro do pro-labore de
            # R$ 20.596 recebido, e o mes mostrava R$ 596 de receita e
            # nenhuma despesa. O lado se decide linha a linha, nunca na soma
            _sinal().label("sinal"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa, competencias))
        .group_by(
            db.categorias.c.natureza,
            db.categorias.c.nome,
            db.transacoes.c.categoria_id,
            db.transacoes.c.natureza,
            db.subcategorias.c.nome,
            _sinal(),
        )
    )
    receitas = despesas = poupanca = sem_classe = nao_recorrentes = 0
    transferencias = sem_classe_entrada = sem_classe_saida = sem_classe_estorno = 0
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
            # os dois lados, separados: o liquido esconde — 39 mil entrando e
            # 58 mil saindo sem categoria viravam "19 mil sem categoria". O
            # credito num cartao ("Ajuste a credito") nao e entrada de renda:
            # abate a despesa, e e assim que a tela tem de dizer
            if lado == "receita":
                sem_classe_entrada += total
                receitas += total
            elif total > 0:
                sem_classe_estorno += total
                despesas += -total
            else:
                sem_classe_saida += -total
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
        "sem_categoria_entrada": sem_classe_entrada,
        "sem_categoria_saida": sem_classe_saida,
        "sem_categoria_estorno": sem_classe_estorno,
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
    competencias: list[str] | None = None,
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
            *_base(competencia, ano, pessoa, competencias),
            db.categorias.c.natureza == natureza,
            # transferência entre contas não é gasto nem ganho: é o mesmo
            # dinheiro mudando de bolso. Deixá-la aqui punha o pagamento da
            # fatura como a maior barra do gráfico, virava meta de orçamento
            # sugerida e não fechava com o card do topo, que já a exclui.
            db.categorias.c.nome != CATEGORIA_TRANSFERENCIA,
        )
        .group_by(db.categorias.c.id, db.categorias.c.nome)
    )
    # despesa e positiva, e o estorno abate: -soma, nunca abs — com abs um
    # mes em que o estorno passava o gasto virava gasto de novo
    sinal = -1 if natureza == "despesa" else 1
    linhas = [
        {
            "categoria_id": linha.id,
            "categoria": linha.nome,
            "total": sinal * int(linha.total or 0),
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
            db.categorias.c.natureza,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.count(db.transacoes.c.id).label("qtd"),
        )
        .select_from(
            db.transacoes
            .join(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa), db.transacoes.c.categoria_id == categoria_id)
        .group_by(db.subcategorias.c.nome, db.categorias.c.natureza)
    )
    linhas = [
        {
            "subcategoria": linha.nome or "— sem subcategoria —",
            "detalhada": linha.nome is not None,
            "total": _pelo_lado(linha.natureza, int(linha.total or 0)),
            "qtd": linha.qtd,
        }
        for linha in conn.execute(consulta)
    ]
    return sorted(linhas, key=lambda linha: -linha["total"])


def _pelo_lado(natureza: str | None, total: int) -> int:
    """Despesa positiva (estorno abate), receita como esta."""
    return total if natureza == "receita" else -total


def subcategorias_de_todas(conn, competencia=None, ano=None, pessoa=None,
                           competencias=None) -> dict[int, list[dict]]:
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
            db.categorias.c.natureza,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.count(db.transacoes.c.id).label("qtd"),
        )
        .select_from(
            db.transacoes
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa, competencias))
        .group_by(db.transacoes.c.categoria_id, db.subcategorias.c.nome, db.categorias.c.natureza)
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
            _sinal().label("sinal"),   # o lado se decide linha a linha (ver `resumo`)
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
            _sinal(),
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


def _consulta_da_matriz(ano: int | None, pessoa: str | None, competencias=None):
    """Base da matriz de despesas: junção externa, para o pendente entrar.

    Junção interna deixava de fora o que ainda não tem categoria — e era
    justamente esse o dinheiro que fazia a tabela não fechar com o card do
    topo, que sempre contou o pendente.
    """
    # o sinal entra no agrupamento pelo mesmo motivo do resumo: sem ele a
    # entrada e a saida sem categoria se anulavam no mesmo grupo
    return (
        sa.select(
            db.categorias.c.nome,
            db.categorias.c.natureza,
            db.transacoes.c.natureza.label("natureza_origem"),
            db.transacoes.c.categoria_id,
            db.transacoes.c.competencia,
            _sinal().label("sinal"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes.outerjoin(
                db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id
            )
        )
        .where(*_base(ano=ano, pessoa=pessoa, competencias=competencias))
        .group_by(
            db.categorias.c.nome, db.categorias.c.natureza, db.transacoes.c.natureza,
            db.transacoes.c.categoria_id, db.transacoes.c.competencia, _sinal(),
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
        # despesa positiva; o estorno (positivo numa categoria de gasto) abate
        acumulado[mes] = acumulado.get(mes, 0) - int(linha.total or 0)

    anterior: dict[str, int] = {}
    for linha in conn.execute(_consulta_da_matriz(ano - 1, pessoa)):
        nome = _nome_da_linha(linha)
        if nome is None:
            continue
        anterior[nome] = anterior.get(nome, 0) - int(linha.total or 0)

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
            _sinal().label("sinal"),
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
            db.subcategorias.c.nome, _sinal(),
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
            db.transacoes
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        # a entrada ainda sem categoria conta no card; tem de contar aqui
        .where(*_base(ano=ano), _lado_da_linha() == "receita")
        .group_by(
            db.transacoes.c.pessoa, db.transacoes.c.classificacao_origem,
            db.subcategorias.c.nome, db.categorias.c.nome, db.transacoes.c.competencia,
        )
    )
    # a linha e (pessoa, tipo); a fonte e o rotulo que a planilha deu a essa
    # linha. O extrato nao traz rotulo: o pro-labore de agosto, vindo do banco,
    # e a mesma linha "TAG" dos meses da planilha — nao uma segunda linha "—"
    linhas: dict[tuple[str, str], dict[str, int]] = {}
    fontes: dict[tuple[str, str], set[str]] = {}
    meses: set[str] = set()
    for registro in conn.execute(consulta):
        mes = registro.competencia[5:7]
        meses.add(mes)
        chave = (registro.pessoa, registro.tipo or registro.categoria or SEM_CATEGORIA)
        alvo = linhas.setdefault(chave, {})
        alvo[mes] = alvo.get(mes, 0) + int(registro.total or 0)
        fonte = (registro.fonte or "").strip()
        if fonte and fonte != "—":
            fontes.setdefault(chave, set()).add(fonte)

    ordem = sorted(meses)
    saida = [
        {
            "pessoa": pessoa,
            "fonte": " / ".join(sorted(fontes.get((pessoa, tipo), ()))) or "—",
            "tipo": tipo,
            "meses": {mes: valores.get(mes, 0) for mes in ordem},
            "total": sum(valores.values()),
        }
        for (pessoa, tipo), valores in linhas.items()
    ]
    saida.sort(key=lambda linha: (linha["pessoa"], -linha["total"]))
    return {"meses": ordem, "linhas": saida}


def _lado_da_linha():
    """De que lado o lancamento cai, decidido linha a linha.

    A mesma regra do `resumo`, escrita em SQL para poder agrupar por ela: manda
    a natureza da categoria; sem categoria, manda a natureza que a origem
    declarou (estorno de despesa entra positivo e nao e receita); sem nenhuma
    das duas, manda o sinal.
    """
    return sa.case(
        (db.categorias.c.natureza.isnot(None), db.categorias.c.natureza),
        (db.transacoes.c.natureza.isnot(None), db.transacoes.c.natureza),
        (db.transacoes.c.valor_centavos > 0, sa.literal("receita")),
        else_=sa.literal("despesa"),
    )


def composicao_de_receitas(conn, competencia=None, ano=None, pessoa=None,
                           competencias=None) -> list[dict]:
    """De onde veio cada real da receita do periodo, agrupado por origem.

    O cartao do topo diz *quanto* entrou; ele nao diz *de onde*, e e essa a
    pergunta quando o numero parece grande demais. Renda dobrada tem uma
    assinatura: o mesmo mes com receita `manual` — a previsao que ele digitou
    para o ano inteiro — e receita de `extrato` ao mesmo tempo, as duas ativas.
    Sem esta quebra, essa assinatura so aparecia para quem soubesse ler a
    tabela de transacoes por fora do app.

    Uma consulta. Conta e soma juntas, porque "sao trinta creditinhos" e
    "e um credito so" pedem conversas diferentes — e trinta creditinhos e
    exatamente o jeito como a Ro recebe dos pacientes.
    """
    lado = _lado_da_linha().label("lado")
    consulta = (
        sa.select(
            db.transacoes.c.origem,
            db.contas.c.nome.label("conta"),
            db.categorias.c.nome.label("categoria"),
            lado,
            sa.func.count().label("quantos"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
            sa.func.min(db.transacoes.c.data).label("primeiro"),
            sa.func.max(db.transacoes.c.data).label("ultimo"),
        )
        .select_from(
            db.transacoes
            .join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa, competencias))
        .group_by(db.transacoes.c.origem, db.contas.c.nome, db.categorias.c.nome, lado)
        .having(lado == "receita")
    )
    linhas = [
        {
            "origem": linha.origem,
            "conta": linha.conta,
            "categoria": linha.categoria or "— sem categoria —",
            "quantos": int(linha.quantos or 0),
            "total": int(linha.total or 0),
            "primeiro": linha.primeiro,
            "ultimo": linha.ultimo,
            # transferencia entra na lista, marcada: ela nao soma no total do
            # topo, e some-la aqui faria a quebra nao fechar com o cartao
            "no_total": linha.categoria != CATEGORIA_TRANSFERENCIA,
        }
        for linha in conn.execute(consulta)
    ]
    return sorted(linhas, key=lambda linha: (-linha["total"], linha["origem"]))


def _de_cartao():
    return (
        db.transacoes
        .join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
        .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
    )


def _condicoes_de_cartao():
    return [
        db.transacoes.c.ativo == sa.true(),
        db.contas.c.tipo == "cartao",
        # a COMPRA do lado da renda e sempre alarme (o arquivo lido ao
        # contrario, ou um engano a mao). O CREDITO no cartao (ajuste,
        # cashback) que o dono classificou a mao como renda e decisao dele
        sa.or_(
            db.transacoes.c.valor_centavos < 0,
            db.transacoes.c.status.is_(None),
            db.transacoes.c.status != "manual",
        ),
        sa.or_(
            db.categorias.c.nome.is_(None),
            db.categorias.c.nome != CATEGORIA_TRANSFERENCIA,
        ),
    ]


def ids_receita_em_cartao(conn) -> list[int]:
    """As linhas que a sentinela apontaria, uma a uma.

    A varredura da subida usa ESTA lista — a mesma pergunta, nao uma parecida.
    Na primeira versao a varredura so olhava linha com categoria de receita, e
    a sentinela tambem olhava linha sem categoria decidida pela natureza ou
    pelo sinal: as 24 linhas ficaram no aviso depois da subida que devia
    te-las consertado. Uma pergunta so nao deixa esse vao existir.
    """
    return [
        linha.id for linha in conn.execute(
            sa.select(db.transacoes.c.id)
            .select_from(_de_cartao())
            .where(*_condicoes_de_cartao(), _lado_da_linha() == "receita")
        )
    ]


def receita_em_cartao(conn) -> list[dict]:
    """Dinheiro de cartao de credito contado como renda — em qualquer mes.

    Num cartao nao existe receita: o que entra e compra, e o credito que aparece
    e estorno ou o pagamento da propria fatura. Se alguma linha de cartao esta
    somando do lado da renda, alguma coisa leu o arquivo ao contrario ou alguem
    classificou um estorno como receita. Foi assim que uma fatura inteira
    passou por renda tres vezes, e cada vez so se descobriu olhando o numero
    do mes e estranhando.

    Esta consulta e a resposta para "como vou saber se acontecer de novo": o
    app olha por conta propria, em todos os meses, toda vez que a tela abre,
    e diz de qual cartao e quanto. Zero linhas e o normal; qualquer coisa
    acima disso e um aviso na abertura, nao um numero estranho para se
    desconfiar.
    """
    lado = _lado_da_linha().label("lado")
    consulta = (
        sa.select(
            db.contas.c.nome.label("conta"),
            db.transacoes.c.competencia,
            sa.func.count().label("quantos"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(_de_cartao())
        .where(*_condicoes_de_cartao())
        .group_by(db.contas.c.nome, db.transacoes.c.competencia, lado)
        .having(lado == "receita")
        .order_by(db.transacoes.c.competencia.desc())
    )
    return [
        {"conta": l.conta, "competencia": l.competencia,
         "quantos": int(l.quantos or 0), "total": int(l.total or 0)}
        for l in conn.execute(consulta)
    ]


def previsto_e_realizado(composicao: list[dict]) -> tuple[int, int]:
    """Quanto da receita do periodo e previsao e quanto ja aconteceu.

    Previsao e o que foi digitado a mao ou veio na planilha da carga inicial —
    a planilha traz o ano inteiro, e dos meses futuros ela e a previsao.
    Realizado e o que veio de extrato.
    """
    from .dedup import ORIGENS_DE_PREVISAO

    previsto = realizado = 0
    for linha in composicao:
        if not linha.get("no_total", True):
            continue
        if linha["origem"] in ORIGENS_DE_PREVISAO:
            previsto += linha["total"]
        else:
            realizado += linha["total"]
    return previsto, realizado


def renda_possivelmente_dobrada(conn, competencia=None, ano=None, pessoa=None) -> list[dict]:
    """Os meses fechados em que a previsao de alguem convive com o extrato dele.

    Renda dobrada tem uma assinatura estreita: a MESMA pessoa, no MESMO mes,
    com a receita que ela previu (planilha ou lancada a mao) e a que o banco
    mostra, as duas valendo, num mes que ja acabou. Somar o ano inteiro nao
    serve: a planilha e a verdade de janeiro a julho, o extrato e a de agosto,
    e o alarme tocava sempre que houvesse um extrato no ano — dizendo "1,8
    milhao pode estar dobrado" sobre um ano que nao tem dobra nenhuma.

    Mes em curso tampouco: o extrato ainda nao chegou, e conviver com a
    previsao e o normal dele. Mes futuro so tem previsao.
    """
    from .dedup import ORIGENS_DE_PREVISAO

    lado = _lado_da_linha().label("lado")
    consulta = (
        sa.select(
            db.transacoes.c.competencia,
            db.transacoes.c.pessoa,
            db.transacoes.c.origem,
            lado,
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencia, ano, pessoa),
               db.transacoes.c.competencia < date.today().strftime("%Y-%m"),
               sa.or_(db.categorias.c.nome.is_(None),
                      db.categorias.c.nome.not_in((CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA))))
        .group_by(db.transacoes.c.competencia, db.transacoes.c.pessoa,
                  db.transacoes.c.origem, lado)
        .having(lado == "receita")
    )
    por_mes: dict[tuple[str, str], dict[str, int]] = {}
    for linha in conn.execute(consulta):
        alvo = por_mes.setdefault((linha.competencia, linha.pessoa),
                                  {"previsto": 0, "realizado": 0})
        chave = "previsto" if linha.origem in ORIGENS_DE_PREVISAO else "realizado"
        alvo[chave] += int(linha.total or 0)
    return sorted(
        ({"competencia": mes, "pessoa": quem, **valores}
         for (mes, quem), valores in por_mes.items()
         if valores["previsto"] > 0 and valores["realizado"] > 0),
        key=lambda linha: (linha["competencia"], linha["pessoa"]),
    )


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
    categoria_id=None, limite: int = 500, competencias=None,
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
            db.transacoes.c.origem,
            # de que arquivo a linha veio: e a resposta para "eu nao subi isso"
            db.uploads.c.arquivo,
            # o que o editor de classificacao precisa para reclassificar a
            # linha ali mesmo, na Visao Geral
            db.transacoes.c.categoria_id,
            db.transacoes.c.subcategoria_id,
            db.transacoes.c.classificacao_origem,
            db.contas.c.tipo.label("tipo_conta"),
        )
        .select_from(
            db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
            .outerjoin(db.uploads, db.transacoes.c.upload_id == db.uploads.c.id)
        )
        .where(*_base(competencia, ano, pessoa, competencias))
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
        # a meta e percentual da renda que se repete: venda de bem nao afrouxa
        # o orcamento do mes
        renda_base = do_mes["renda_recorrente"]
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
        if linha.categoria in (CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA):
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


ESCOPOS = {"ano": "o ano civil", "12m": "os últimos doze meses"}


def competencias_do_periodo(conn, ate: str, escopo: str = "12m") -> list[str]:
    """Os meses da leitura longa: o ano civil de `ate`, ou a janela de doze.

    As duas perguntas sao diferentes e as duas sao legitimas. O ano civil e a
    conta que se presta ao imposto, ao balanco de dezembro e a comparacao com
    o ano passado. A janela de doze meses e a que responde "como esta a casa
    hoje" sem esperar janeiro — e a unica que pode falar de sazonalidade,
    porque so ela ve o mesmo mes duas vezes.
    """
    if escopo == "12m":
        return janela_de_doze_meses(conn, ate)
    ano = int(ate[:4])
    todas = sorted(
        linha.competencia
        for linha in conn.execute(
            sa.select(db.transacoes.c.competencia)
            .where(db.transacoes.c.ativo == sa.true(),
                   db.transacoes.c.competencia.isnot(None),
                   db.transacoes.c.competencia.like(f"{ano}-%"))
            .distinct()
        )
    )
    return [c for c in todas if c <= ate]


def matriz_de_competencias(conn, competencias: list[str], pessoa: str | None = None) -> dict:
    """Categoria x mes para uma janela qualquer, inclusive atravessando o ano.

    `tabela_mes_a_mes` responde por ano civil e usa so o "MM" como coluna:
    numa janela de doze meses que atravessa dezembro, agosto de dois anos
    diferentes cairia na mesma coluna. Aqui a coluna e a competencia inteira.
    """
    if not competencias:
        return {"meses": [], "linhas": []}
    matriz: dict[str, dict[str, int]] = {}
    for linha in conn.execute(_consulta_da_matriz(None, pessoa, competencias=competencias)):
        nome = _nome_da_linha(linha)
        if nome is None or nome == CATEGORIA_TRANSFERENCIA:
            continue
        acumulado = matriz.setdefault(nome, {})
        acumulado[linha.competencia] = (acumulado.get(linha.competencia, 0)
                                        - int(linha.total or 0))
    meses = sorted(competencias)
    saida = []
    for nome, valores in matriz.items():
        serie = {mes: valores.get(mes, 0) for mes in meses}
        com_gasto = [v for v in serie.values() if v]
        total = sum(serie.values())
        if total <= 0 and not com_gasto:
            continue
        pico = max(serie.items(), key=lambda par: par[1], default=("—", 0))
        saida.append({
            "categoria": nome,
            "meses": serie,
            "total": total,
            "media": total // len(meses),
            "media_com_gasto": (sum(com_gasto) // len(com_gasto)) if com_gasto else 0,
            "meses_com_gasto": len(com_gasto),
            "pico_mes": pico[0],
            "pico": pico[1],
        })
    saida.sort(key=lambda linha: -linha["total"])
    return {"meses": meses, "linhas": saida}


def estabelecimentos(conn, competencias: list[str], quantos: int = 20) -> list[dict]:
    """Onde o dinheiro foi parar, pelo nome do lugar, no periodo inteiro.

    Categoria diz o tipo de gasto; o estabelecimento diz a decisao. "Mercado"
    nao se corta; um restaurante especifico, tres vezes por semana, se discute.
    """
    from .texto import chave_estabelecimento

    consulta = (
        sa.select(db.transacoes.c.descricao, db.transacoes.c.competencia,
                  db.transacoes.c.valor_centavos, db.categorias.c.nome.label("categoria"))
        .select_from(
            db.transacoes.outerjoin(db.categorias,
                                    db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencias=competencias), db.transacoes.c.valor_centavos < 0,
               sa.or_(db.categorias.c.nome.is_(None),
                      db.categorias.c.nome.not_in((CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA))))
    )
    por_chave: dict[str, dict] = {}
    for linha in conn.execute(consulta):
        chave = chave_estabelecimento(linha.descricao)
        if not chave or len(chave) < 4:
            continue
        registro = por_chave.setdefault(chave, {
            "estabelecimento": chave, "total": 0, "qtd": 0, "meses": set(),
            "categoria": linha.categoria or "sem categoria",
        })
        registro["total"] += abs(int(linha.valor_centavos))
        registro["qtd"] += 1
        registro["meses"].add(linha.competencia)
    saida = [
        {**r, "meses": len(r["meses"]), "primeiro_mes": min(r["meses"])}
        for r in sorted(por_chave.values(), key=lambda r: -r["total"])[:quantos]
    ]
    return saida


def estreantes(conn, competencias: list[str], mes: str, minimo: int = 20_000) -> list[dict]:
    """Lugares que aparecem em `mes` e nunca tinham aparecido antes na janela.

    Gasto novo e a explicacao mais comum de um mes fora da curva, e e o que
    nenhuma media mostra: a media sobe, e ninguem sabe por causa de quem.
    """
    from .texto import chave_estabelecimento

    consulta = (
        sa.select(db.transacoes.c.descricao, db.transacoes.c.competencia,
                  db.transacoes.c.valor_centavos, db.categorias.c.nome.label("categoria"))
        .select_from(
            db.transacoes.outerjoin(db.categorias,
                                    db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(*_base(competencias=competencias), db.transacoes.c.valor_centavos < 0,
               sa.or_(db.categorias.c.nome.is_(None),
                      db.categorias.c.nome.not_in((CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA))))
    )
    por_chave: dict[str, dict] = {}
    for linha in conn.execute(consulta):
        chave = chave_estabelecimento(linha.descricao)
        if not chave or len(chave) < 4:
            continue
        registro = por_chave.setdefault(chave, {
            "estabelecimento": chave, "no_mes": 0, "antes": 0,
            "categoria": linha.categoria or "sem categoria",
        })
        if linha.competencia == mes:
            registro["no_mes"] += abs(int(linha.valor_centavos))
        elif linha.competencia < mes:
            registro["antes"] += abs(int(linha.valor_centavos))
    novos = [r for r in por_chave.values() if r["no_mes"] >= minimo and not r["antes"]]
    return sorted(novos, key=lambda r: -r["no_mes"])


def piso_e_escolha(conn, competencias: list[str], competencia: str | None = None) -> dict:
    """Quanto do gasto e compromisso que se repete e quanto e decisao do mes.

    A diferenca entre os dois muda o que se pode fazer: o piso so cai
    cancelando alguma coisa; o resto cai decidindo diferente amanha.
    """
    fixos = compromissos_recorrentes(conn, competencia or competencias[-1],
                                     competencias=competencias)
    piso = sum(item["media"] for item in fixos)
    meses = len(competencias) or 1
    total = -sum(
        int(linha.total or 0)
        for linha in conn.execute(_consulta_da_matriz(None, None, competencias=competencias))
        if _nome_da_linha(linha) not in (None, CATEGORIA_TRANSFERENCIA)
    )
    media_total = max(total, 0) // meses
    return {
        "piso": piso,
        "media_total": media_total,
        "escolha": max(media_total - piso, 0),
        "percentual_do_piso": round(100 * piso / media_total, 1) if media_total else 0.0,
        "compromissos": fixos,
    }


def contexto_do_ano(conn, ate: str) -> str:
    """A leitura longa dos ultimos doze meses. Mantida pelo nome antigo."""
    return contexto_longo(conn, ate, escopo="12m")


def _mes_anterior_a(competencia: str) -> str:
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    return f"{ano - 1:04d}-12" if mes == 1 else f"{ano:04d}-{mes - 1:02d}"


def _variacao(agora: int, antes: int) -> str:
    if not antes:
        return "sem base de comparação"
    delta = round(100 * (agora - antes) / antes)
    return f"{'+' if delta > 0 else ''}{delta}%"


def contexto_longo(conn, ate: str, escopo: str = "12m") -> str:
    """Os numeros da leitura longa, no ano civil ou na janela de doze meses.

    O mes responde "para onde foi o dinheiro". A serie responde outra coisa:
    o que se repete e portanto e piso, o que oscila e portanto e decisao, em
    que meses a casa gasta mais, quem trouxe o que, e qual gasto e novo. Este
    texto e tudo o que a IA vai saber — ela nao consulta o banco —, entao ele
    carrega tambem o que NAO se sabe: o mes que ainda esta pela metade, a
    janela curta demais para falar de sazonalidade.
    """
    from .money import fmt_brl

    competencias = competencias_do_periodo(conn, ate, escopo)
    if not competencias:
        return f"Sem lançamentos até {ate}."

    ano = int(ate[:4])
    meses_do_periodo = len(competencias)
    ultimo = competencias[-1]
    fechada = meses_do_periodo >= 12
    total = resumo(conn, competencias=competencias)
    rotulo = "ano civil de " + str(ano) if escopo == "ano" else "últimos 12 meses"

    linhas = [
        f"PERÍODO DA LEITURA: {rotulo} — de {competencias[0]} a {ultimo}, "
        f"{meses_do_periodo} {'meses' if meses_do_periodo > 1 else 'mês'} com lançamento.",
    ]
    if not fechada:
        linhas.append(
            "ATENÇÃO: menos de doze meses de histórico. Dá para descrever o que houve e "
            "apontar concentração; NÃO dá para afirmar sazonalidade — para isso é preciso "
            "ver o mesmo mês repetir em anos diferentes. Diga isso ao falar de padrão anual."
        )

    coberturas = cobertura_por_competencia(conn, competencias)
    incompletos = [
        f"{mes} ({coberturas[mes]['percentual_classificado']:.0f}% classificado)"
        for mes in competencias
        if coberturas[mes]["percentual_classificado"] < 95
    ]
    if incompletos:
        linhas += [
            "",
            "COBERTURA (leia antes de concluir qualquer coisa): estes meses não estão "
            "classificados por inteiro — " + ", ".join(incompletos)
            + ". Os totais deles são parciais e a comparação com os outros meses fica "
            "prejudicada.",
        ]
    else:
        linhas += ["", "COBERTURA: todos os meses do período estão classificados acima de 95%."]

    sobra = total["receitas"] - total["despesas"] - total["poupanca"]
    linhas += [
        "",
        "TOTAIS DO PERÍODO:",
        f"- receitas {fmt_brl(total['receitas'])}, despesas {fmt_brl(total['despesas'])}, "
        f"poupança {fmt_brl(total['poupanca'])}, sobra livre {fmt_brl(sobra)}",
        f"- média por mês do período ({meses_do_periodo} meses): "
        f"receitas {fmt_brl(total['receitas'] // meses_do_periodo)}, "
        f"despesas {fmt_brl(total['despesas'] // meses_do_periodo)}",
        f"- a casa gastou {round(100 * total['despesas'] / total['receitas'])}% do que "
        f"recebeu no período." if total["receitas"] else
        "- sem receita lançada no período: a proporção gasto/renda não pode ser calculada.",
    ]
    if total["receitas_nao_recorrentes"]:
        linhas.append(
            f"- dentro das receitas, {fmt_brl(total['receitas_nao_recorrentes'])} é venda de "
            "bem: entrada de uma vez só, não é renda que se repita."
        )
    if total["transferencias"]:
        linhas.append(
            f"- fora dos dois totais, {fmt_brl(total['transferencias'])} de transferência "
            "entre contas do casal: dinheiro que só mudou de bolso."
        )

    linhas += ["", "MÊS A MÊS (a variação é contra o mês anterior da série):"]
    series: dict[str, dict] = {}
    for a in sorted({int(c[:4]) for c in competencias}):
        for mes in serie_mensal(conn, a):
            series[mes["competencia"]] = mes
    anterior_despesa = None
    for mes in competencias:
        dados_do_mes = series.get(mes)
        if not dados_do_mes:
            continue
        sobra_mes = (dados_do_mes["receitas"] - dados_do_mes["despesas"]
                     - dados_do_mes["poupanca"])
        variacao = (f", despesa {_variacao(dados_do_mes['despesas'], anterior_despesa)} "
                    "vs o mês anterior" if anterior_despesa is not None else "")
        linhas.append(
            f"- {mes}: receitas {fmt_brl(dados_do_mes['receitas'])}, "
            f"despesas {fmt_brl(dados_do_mes['despesas'])}, "
            f"poupança {fmt_brl(dados_do_mes['poupanca'])}, "
            f"sobra {fmt_brl(sobra_mes)}{variacao}"
        )
        anterior_despesa = dados_do_mes["despesas"]

    matriz = matriz_de_competencias(conn, competencias)
    if matriz["linhas"]:
        gasto_total = sum(l["total"] for l in matriz["linhas"]) or 1
        linhas += [
            "",
            "GASTO POR CATEGORIA, MÊS A MÊS (mês vazio = não houve gasto naquele mês; "
            "'concentração' é quando quase tudo está em um ou dois meses):",
            "  categoria | " + " | ".join(matriz["meses"])
            + " | total | % do gasto | média/mês | meses com gasto | pico",
        ]
        for linha in matriz["linhas"][:16]:
            linhas.append(
                f"  {linha['categoria']}: "
                + " | ".join(fmt_brl(linha["meses"][mes]) for mes in matriz["meses"])
                + f" | {fmt_brl(linha['total'])}"
                + f" | {round(100 * linha['total'] / gasto_total)}%"
                + f" | {fmt_brl(linha['media'])}"
                + f" | {linha['meses_com_gasto']} de {meses_do_periodo}"
                + f" | maior em {linha['pico_mes']} ({fmt_brl(linha['pico'])})"
            )

    abertura = subcategorias_de_todas(conn, competencias=competencias)
    detalhe = []
    for linha in por_categoria(conn, competencias=competencias):
        for sub in abertura.get(linha["categoria_id"], [])[:6]:
            detalhe.append((linha["categoria"], sub["subcategoria"], sub["total"], sub["qtd"]))
    if detalhe:
        linhas += ["", "ABERTURA POR SUBCATEGORIA no período (as maiores de cada categoria):"]
        for categoria, sub, valor, qtd in sorted(detalhe, key=lambda item: -item[2])[:28]:
            linhas.append(
                f"- {categoria} › {sub}: {fmt_brl(valor)} em {qtd} lançamento(s), "
                f"{fmt_brl(valor // meses_do_periodo)}/mês"
            )

    divisao = piso_e_escolha(conn, competencias)
    if divisao["media_total"]:
        linhas += [
            "",
            "PISO x ESCOLHA (a diferença mais útil do orçamento):",
            f"- gasto médio do período: {fmt_brl(divisao['media_total'])}/mês",
            f"- do que se repete (mesmo lugar em 3 meses ou mais): "
            f"{fmt_brl(divisao['piso'])}/mês, {divisao['percentual_do_piso']:.0f}% do gasto. "
            "Isso só cai cancelando alguma coisa.",
            f"- o resto, {fmt_brl(divisao['escolha'])}/mês, é decisão do mês — cai decidindo "
            "diferente, sem cancelar nada.",
        ]
    fixos = divisao["compromissos"]
    if fixos:
        recentes = competencias[-3:]
        antigos = competencias[:-3]
        linhas += ["", "COMPROMISSOS QUE SE REPETEM (com a tendência dentro do período):"]
        for item in fixos[:18]:
            por_mes = item.get("por_mes", {})
            media_recente = sum(por_mes.get(m, 0) for m in recentes) // max(len(recentes), 1)
            media_antiga = (sum(por_mes.get(m, 0) for m in antigos) // len(antigos)
                            if antigos else 0)
            tendencia = (f" — últimos 3 meses {fmt_brl(media_recente)}/mês contra "
                         f"{fmt_brl(media_antiga)}/mês antes ({_variacao(media_recente, media_antiga)})"
                         if antigos else "")
            linhas.append(
                f"- {item['estabelecimento']}: {fmt_brl(item['media'])}/mês, visto em "
                f"{item['meses']} meses, {fmt_brl(item['total'])} no total{tendencia}"
            )

    lugares = estabelecimentos(conn, competencias, quantos=18)
    if lugares:
        linhas += ["", "ONDE O DINHEIRO FOI PARAR (por estabelecimento, no período):"]
        for item in lugares:
            linhas.append(
                f"- {item['estabelecimento']} ({item['categoria']}): {fmt_brl(item['total'])} "
                f"em {item['qtd']} compra(s), em {item['meses']} mês(es)"
            )

    novos = estreantes(conn, competencias, ultimo)
    if novos:
        linhas += [
            "",
            f"GASTO NOVO em {ultimo} (lugares que não apareciam antes nesta janela — "
            "é a explicação mais comum de um mês fora da curva):",
        ]
        for item in novos[:10]:
            linhas.append(
                f"- {item['estabelecimento']} ({item['categoria']}): {fmt_brl(item['no_mes'])}"
            )

    linhas += ["", "POR PESSOA no período (sem dono declarado = Casal):"]
    for quem in db.PESSOAS:
        da_pessoa = resumo(conn, competencias=competencias, pessoa=quem)
        topo = por_categoria(conn, competencias=competencias, pessoa=quem)[:3]
        detalhe_pessoa = ("; maiores gastos: "
                          + ", ".join(f"{c['categoria']} {fmt_brl(c['total'])}" for c in topo)
                          if topo else "")
        linhas.append(
            f"- {quem}: despesas {fmt_brl(da_pessoa['despesas'])}, "
            f"receitas {fmt_brl(da_pessoa['receitas'])}{detalhe_pessoa}"
        )

    composicao = [c for c in composicao_de_receitas(conn, competencias=competencias)
                  if c["no_total"]]
    if composicao:
        linhas += ["", "DE ONDE VEIO A RECEITA do período (origem, conta e tipo):"]
        for item in composicao[:12]:
            linhas.append(
                f"- {item['categoria']} — {item['conta']} ({item['origem']}): "
                f"{fmt_brl(item['total'])} em {item['quantos']} lançamento(s)"
            )
        previsto, realizado = previsto_e_realizado(composicao)
        if previsto and realizado:
            linhas.append(
                f"  (destes, {fmt_brl(previsto)} vieram de planilha ou lançamento à mão e "
                f"{fmt_brl(realizado)} de extrato bancário.)"
            )

    maiores = lancamentos(conn, competencias=competencias, natureza="despesa", limite=800)
    maiores = [m for m in maiores
               if m["categoria"] not in (CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA)]
    maiores = sorted(maiores, key=lambda linha: linha["valor_centavos"])[:15]
    if maiores:
        linhas += ["", "AS MAIORES SAÍDAS DO PERÍODO, uma a uma:"]
        for item in maiores:
            linhas.append(
                f"- {item['data']:%d/%m/%Y} {item['descricao'][:45]}: "
                f"{fmt_brl(abs(item['valor_centavos']))} "
                f"({item['categoria'] or 'sem categoria'}, {item['pessoa']})"
            )

    from . import repo

    metas = repo.listar_metas(conn, ano)
    if metas:
        do_orcamento = [item for item in orcamento(conn, ultimo, metas)
                        if item["percentual"] and item["meta"]]
        if do_orcamento:
            linhas += ["", f"METAS DO ANO (% da renda) x realizado em {ultimo}:"]
            for item in do_orcamento:
                uso = f"{item['uso']:.0f}%" if item["uso"] is not None else "—"
                linhas.append(
                    f"- {item['categoria']}: meta {item['percentual']:.0f}% "
                    f"({fmt_brl(item['meta'])}), realizado {fmt_brl(item['realizado'])} "
                    f"= {uso} da meta"
                )

    anteriores = [item for item in comparativo_anual(conn) if item.get("ano") != ano]
    if anteriores:
        linhas += ["", "ANOS ANTERIORES, para comparar:"]
        for item in anteriores:
            linhas.append(
                f"- {item['ano']}: receitas {fmt_brl(item.get('receitas', 0))}, "
                f"despesas {fmt_brl(item.get('despesas', 0))}"
            )
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

    # o mes anterior ao lado de cada categoria: "Saude 17 mil" nao diz nada
    # sozinho, e a media do ano nao pega o que acabou de mudar
    anterior_mes = _mes_anterior_a(competencia)
    do_anterior = {
        linha["categoria"]: linha["total"]
        for linha in por_categoria(conn, competencia=anterior_mes)
    }
    gasto_do_mes = atual["despesas"] or 1
    linhas += [
        "",
        f"Gasto por categoria no mês (com o mês anterior, {anterior_mes}, ao lado):",
    ]
    # as subcategorias de todas as categorias numa consulta só: uma por
    # categoria eram catorze idas ao banco para montar este mesmo texto
    abertura = subcategorias_de_todas(conn, competencia=competencia)
    for linha in por_categoria(conn, competencia=competencia):
        antes = do_anterior.get(linha["categoria"], 0)
        comparacao = (f"; em {anterior_mes} foi {fmt_brl(antes)} "
                      f"({_variacao(linha['total'], antes)})" if antes
                      else f"; não houve gasto nesta categoria em {anterior_mes}")
        linhas.append(
            f"- {linha['categoria']}: {fmt_brl(linha['total'])} "
            f"({linha['qtd']} lançamentos, {round(100 * linha['total'] / gasto_do_mes)}% "
            f"do gasto do mês){comparacao}"
        )
        for sub in abertura.get(linha["categoria_id"], [])[:6]:
            linhas.append(
                f"    · {sub['subcategoria']}: {fmt_brl(sub['total'])} "
                f"({sub['qtd']} lançamentos)"
            )

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
    # pagamento de fatura e aporte nao sao gasto: fora da lista, como no card
    maiores = [m for m in maiores
               if m["categoria"] not in (CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA)]
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

    janela = janela_de_doze_meses(conn, competencia)
    if len(janela) > 1:
        divisao = piso_e_escolha(conn, janela, competencia)
        if divisao["media_total"]:
            linhas += [
                "",
                "Piso x escolha (olhando os últimos meses):",
                f"- do gasto médio de {fmt_brl(divisao['media_total'])}/mês, "
                f"{fmt_brl(divisao['piso'])} é compromisso que se repete "
                f"({divisao['percentual_do_piso']:.0f}%) e {fmt_brl(divisao['escolha'])} é "
                "decisão do mês. Cortar o primeiro exige cancelar algo; o segundo muda "
                "decidindo diferente.",
            ]
        novos = estreantes(conn, janela, competencia)
        if novos:
            linhas += [
                "",
                "Gasto NOVO neste mês (lugares que não apareciam nos meses anteriores — "
                "costuma ser a explicação de um mês fora da curva):",
            ]
            for item in novos[:8]:
                linhas.append(
                    f"- {item['estabelecimento']} ({item['categoria']}): "
                    f"{fmt_brl(item['no_mes'])}"
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


def compromissos_recorrentes(conn, competencia: str, minimo_de_meses: int = 3,
                             competencias: list[str] | None = None) -> list[dict]:
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
        .select_from(
            db.transacoes.outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        # o pagamento da fatura se repete todo mes e nao e compromisso: e o
        # mesmo dinheiro das compras; o aporte tampouco e gasto
        .where(*_base(ano=None if competencias else ano, competencias=competencias),
               db.transacoes.c.valor_centavos < 0,
               sa.or_(db.categorias.c.nome.is_(None),
                      db.categorias.c.nome.not_in((CATEGORIA_TRANSFERENCIA, CATEGORIA_POUPANCA))))
    )
    por_chave: dict[str, dict] = {}
    for linha in conn.execute(consulta):
        chave = chave_estabelecimento(linha.descricao)
        if not chave or len(chave) < 4:
            continue
        registro = por_chave.setdefault(
            chave, {"estabelecimento": chave, "meses": set(), "total": 0, "no_mes": 0,
                    "por_mes": {}}
        )
        registro["meses"].add(linha.competencia)
        valor = abs(int(linha.valor_centavos))
        registro["total"] += valor
        registro["por_mes"][linha.competencia] = registro["por_mes"].get(linha.competencia, 0) + valor
        if linha.competencia == competencia:
            registro["no_mes"] += valor

    saida = [
        {
            "estabelecimento": r["estabelecimento"],
            "meses": len(r["meses"]),
            "total": r["total"],
            "media": r["total"] // len(r["meses"]),
            "no_mes": r["no_mes"],
            "por_mes": r["por_mes"],
        }
        for r in por_chave.values()
        if len(r["meses"]) >= minimo_de_meses
    ]
    saida.sort(key=lambda item: -item["total"])
    return saida
