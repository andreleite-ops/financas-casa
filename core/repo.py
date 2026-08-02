"""Pipeline de importacao e consultas usadas pelas telas."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from . import ai, classify, db, dedup
from .texto import normalizar

PESSOA_PADRAO = "Casal"


# --------------------------------------------------------------------------
# leituras de apoio
# --------------------------------------------------------------------------
def listar_contas(conn, so_ativas: bool = False) -> list[dict]:
    consulta = sa.select(db.contas).order_by(db.contas.c.ativa.desc(), db.contas.c.nome)
    if so_ativas:
        consulta = consulta.where(db.contas.c.ativa == sa.true())
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def conta_por_id(conn, conta_id: int) -> dict | None:
    linha = conn.execute(sa.select(db.contas).where(db.contas.c.id == conta_id)).fetchone()
    return dict(linha._mapping) if linha else None


def plano_de_contas(conn, natureza: str | None = None) -> list[dict]:
    consulta = (
        sa.select(
            db.categorias.c.id,
            db.categorias.c.nome,
            db.categorias.c.natureza,
            db.categorias.c.ordem,
            db.categorias.c.ativa,
        )
        .order_by(db.categorias.c.natureza.desc(), db.categorias.c.ordem)
    )
    if natureza:
        consulta = consulta.where(db.categorias.c.natureza == natureza)
    saida = []
    for cat in conn.execute(consulta):
        subs = conn.execute(
            sa.select(db.subcategorias.c.id, db.subcategorias.c.nome, db.subcategorias.c.ativa)
            .where(db.subcategorias.c.categoria_id == cat.id)
            .order_by(db.subcategorias.c.ordem)
        ).fetchall()
        saida.append(
            {
                **dict(cat._mapping),
                "subcategorias": [dict(s._mapping) for s in subs],
            }
        )
    return saida


def plano_para_ia(conn) -> dict[str, list[str]]:
    return {
        cat["nome"]: [s["nome"] for s in cat["subcategorias"]]
        for cat in plano_de_contas(conn)
        if cat["ativa"]
    }


def _indice_categorias(conn) -> tuple[dict[str, int], dict[tuple[int, str], int]]:
    cats = {
        linha.nome.casefold(): linha.id
        for linha in conn.execute(sa.select(db.categorias.c.id, db.categorias.c.nome))
    }
    subs = {
        (linha.categoria_id, linha.nome.casefold()): linha.id
        for linha in conn.execute(
            sa.select(db.subcategorias.c.id, db.subcategorias.c.categoria_id, db.subcategorias.c.nome)
        )
    }
    return cats, subs


def _pessoa_valida(valor: str | None, padrao: str) -> str:
    if not valor:
        return padrao
    limpo = str(valor).strip().casefold()
    for pessoa in db.PESSOAS:
        if limpo == pessoa.casefold() or limpo.startswith(pessoa.casefold()[:3]):
            return pessoa
    if limpo in ("andre", "andré", "a"):
        return "André"
    if limpo in ("ro", "rô", "rosana", "r"):
        return "Rô"
    return padrao


# --------------------------------------------------------------------------
# importacao
# --------------------------------------------------------------------------
def importar(
    engine,
    *,
    conta_id: int,
    lancamentos: list,
    arquivo: str,
    usuario: str,
    origem: str = "extrato",
    competencia: str | None = None,
    usar_ia: bool = True,
) -> dict:
    """Grava um lote de lancamentos aplicando dedup e classificacao.

    Devolve o resumo que a tela de upload mostra.
    """
    resumo = {
        "lidos": len(lancamentos),
        "importados": 0,
        "auto": 0,
        "pendentes": 0,
        "duplicados_exatos": 0,
        "duplicados_provaveis": 0,
        "conferidos_planilha": 0,
        "upload_id": None,
    }
    if not lancamentos:
        return resumo

    with engine.begin() as conn:
        conta = conta_por_id(conn, conta_id)
        if conta is None:
            raise ValueError(f"conta {conta_id} não existe")
        pessoa_padrao = conta["titular"]

        upload_id = conn.execute(
            sa.insert(db.uploads).values(
                arquivo=arquivo,
                conta_id=conta_id,
                competencia=competencia,
                origem=origem,
                enviado_por=usuario,
                lidos=len(lancamentos),
            )
        ).inserted_primary_key[0]
        resumo["upload_id"] = upload_id

        regras = classify.carregar_regras(conn)
        naturezas = classify._natureza_por_categoria(conn)
        cats_idx, subs_idx = _indice_categorias(conn)
        ja_casados: set[int] = set()
        pendentes: list[tuple[int, str, int]] = []  # (id, descricao, valor)

        for lan in lancamentos:
            descricao_norm = normalizar(lan.descricao)
            decisao = dedup.avaliar(
                conn,
                conta_id=conta_id,
                dia=lan.data,
                valor_centavos=lan.valor_centavos,
                descricao=lan.descricao,
                descricao_norm=descricao_norm,
                origem=lan.origem or origem,
                upload_id=upload_id,
                ja_casados=ja_casados,
            )
            if decisao.existente_id:
                ja_casados.add(decisao.existente_id)

            # classificacao: dica da planilha primeiro, senao regras
            categoria_id = subcategoria_id = None
            status, confianca = "pendente", None
            if lan.categoria_hint:
                categoria_id = cats_idx.get(str(lan.categoria_hint).strip().casefold())
                if categoria_id and lan.subcategoria_hint:
                    subcategoria_id = subs_idx.get(
                        (categoria_id, str(lan.subcategoria_hint).strip().casefold())
                    )
                if categoria_id:
                    status, confianca = "manual", 1.0
            if categoria_id is None:
                achado = classify.classificar_local(
                    lan.descricao, lan.valor_centavos, regras, naturezas
                )
                if achado.classificado:
                    categoria_id = achado.categoria_id
                    subcategoria_id = achado.subcategoria_id
                    status, confianca = achado.status, achado.confianca

            pessoa = _pessoa_valida(lan.pessoa_hint, pessoa_padrao)

            nova_id = conn.execute(
                sa.insert(db.transacoes).values(
                    data=lan.data,
                    competencia=lan.competencia or (competencia or lan.data.strftime("%Y-%m")),
                    descricao=lan.descricao,
                    descricao_norm=descricao_norm,
                    valor_centavos=lan.valor_centavos,
                    conta_id=conta_id,
                    categoria_id=categoria_id,
                    subcategoria_id=subcategoria_id,
                    pessoa=pessoa,
                    status=status,
                    confianca=confianca,
                    origem=lan.origem or origem,
                    hash_dedup=dedup.hash_lancamento(
                        conta_id, lan.data, lan.valor_centavos, descricao_norm
                    ),
                    upload_id=upload_id,
                    ativo=decisao.entra_ativo,
                    observacao=decisao.motivo or None,
                )
            ).inserted_primary_key[0]

            if decisao.e_duplicata:
                dedup.registrar_duplicidade(conn, nova_id, decisao)
                chave = (
                    "duplicados_exatos"
                    if decisao.situacao == "duplicata_exata"
                    else "duplicados_provaveis"
                )
                resumo[chave] += 1
                continue

            if decisao.situacao == "confere_planilha":
                # o do extrato prevalece e herda a classificacao da planilha
                antigo = conn.execute(
                    sa.select(
                        db.transacoes.c.categoria_id,
                        db.transacoes.c.subcategoria_id,
                        db.transacoes.c.pessoa,
                        db.transacoes.c.status,
                    ).where(db.transacoes.c.id == decisao.existente_id)
                ).fetchone()
                if antigo and antigo.categoria_id and categoria_id is None:
                    conn.execute(
                        sa.update(db.transacoes)
                        .where(db.transacoes.c.id == nova_id)
                        .values(
                            categoria_id=antigo.categoria_id,
                            subcategoria_id=antigo.subcategoria_id,
                            pessoa=antigo.pessoa,
                            status="manual" if antigo.status == "manual" else "auto_regra",
                            confianca=1.0,
                        )
                    )
                    categoria_id = antigo.categoria_id
                    status = "manual"
                conn.execute(
                    sa.update(db.transacoes)
                    .where(db.transacoes.c.id == decisao.existente_id)
                    .values(ativo=False, observacao="substituído pelo lançamento do extrato")
                )
                conn.execute(
                    sa.update(db.transacoes)
                    .where(db.transacoes.c.id == nova_id)
                    .values(observacao="conferido com a planilha")
                )
                resumo["conferidos_planilha"] += 1

            resumo["importados"] += 1
            if categoria_id is None:
                pendentes.append((nova_id, lan.descricao, lan.valor_centavos))
            else:
                resumo["auto"] += 1

        # camada 3: IA so no que sobrou
        if pendentes and usar_ia and ai.disponivel():
            resolvidos = _classificar_com_ia(conn, pendentes)
            resumo["auto"] += resolvidos
            resumo["ia"] = resolvidos

        resumo["pendentes"] = len(pendentes) - resumo.get("ia", 0)
        conn.execute(
            sa.update(db.uploads)
            .where(db.uploads.c.id == upload_id)
            .values(
                importados=resumo["importados"],
                auto=resumo["auto"],
                pendentes=resumo["pendentes"],
                duplicados=resumo["duplicados_exatos"] + resumo["duplicados_provaveis"],
            )
        )
    return resumo


def _classificar_com_ia(conn, pendentes: list[tuple[int, str, int]]) -> int:
    plano = plano_para_ia(conn)
    cats_idx, subs_idx = _indice_categorias(conn)
    naturezas = classify._natureza_por_categoria(conn)
    resolvidos = 0

    for inicio in range(0, len(pendentes), ai.LOTE):
        fatia = pendentes[inicio : inicio + ai.LOTE]
        entrada = [(i, desc, valor) for i, (_id, desc, valor) in enumerate(fatia)]
        for sugestao in ai.sugerir_categorias(entrada, plano):
            if sugestao.indice >= len(fatia):
                continue
            transacao_id, _desc, valor = fatia[sugestao.indice]
            categoria_id = cats_idx.get(sugestao.categoria.casefold())
            if not categoria_id:
                continue
            natureza_esperada = "receita" if valor > 0 else "despesa"
            if naturezas.get(categoria_id) != natureza_esperada:
                continue
            if sugestao.confianca < ai.LIMITE_CONFIANCA_IA:
                conn.execute(
                    sa.update(db.transacoes)
                    .where(db.transacoes.c.id == transacao_id)
                    .values(confianca=sugestao.confianca,
                            observacao=f"IA sugeriu {sugestao.categoria} (confiança baixa)")
                )
                continue
            subcategoria_id = (
                subs_idx.get((categoria_id, sugestao.subcategoria.casefold()))
                if sugestao.subcategoria
                else None
            )
            conn.execute(
                sa.update(db.transacoes)
                .where(db.transacoes.c.id == transacao_id)
                .values(
                    categoria_id=categoria_id,
                    subcategoria_id=subcategoria_id,
                    status="auto_ia",
                    confianca=sugestao.confianca,
                )
            )
            resolvidos += 1
    return resolvidos


# --------------------------------------------------------------------------
# classificacao manual
# --------------------------------------------------------------------------
def reclassificar(
    engine,
    transacao_id: int,
    *,
    categoria_id: int,
    subcategoria_id: int | None,
    pessoa: str | None,
    usuario: str,
    criar_regra: bool = True,
) -> None:
    with engine.begin() as conn:
        linha = conn.execute(
            sa.select(db.transacoes.c.descricao).where(db.transacoes.c.id == transacao_id)
        ).fetchone()
        valores = dict(
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            status="manual",
            confianca=1.0,
            classificado_por=usuario,
        )
        if pessoa:
            valores["pessoa"] = pessoa
        conn.execute(
            sa.update(db.transacoes).where(db.transacoes.c.id == transacao_id).values(**valores)
        )
        if criar_regra and linha:
            classify.aprender(conn, linha.descricao, categoria_id, subcategoria_id, usuario, pessoa)


def fila_pendentes(conn, limite: int = 200) -> list[dict]:
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.data,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.confianca,
            db.transacoes.c.observacao,
            db.contas.c.nome.label("conta"),
        )
        .select_from(db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id))
        .where(
            db.transacoes.c.status == "pendente",
            db.transacoes.c.ativo == sa.true(),
        )
        .order_by(db.transacoes.c.data.desc())
        .limit(limite)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def buscar_transacoes(conn, termo: str = "", limite: int = 100) -> list[dict]:
    consulta = (
        sa.select(
            db.transacoes.c.id,
            db.transacoes.c.data,
            db.transacoes.c.descricao,
            db.transacoes.c.valor_centavos,
            db.transacoes.c.pessoa,
            db.transacoes.c.status,
            db.transacoes.c.categoria_id,
            db.transacoes.c.subcategoria_id,
            db.categorias.c.nome.label("categoria"),
            db.subcategorias.c.nome.label("subcategoria"),
            db.contas.c.nome.label("conta"),
        )
        .select_from(
            db.transacoes.join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.subcategorias, db.transacoes.c.subcategoria_id == db.subcategorias.c.id)
        )
        .where(db.transacoes.c.ativo == sa.true())
        .order_by(db.transacoes.c.data.desc())
        .limit(limite)
    )
    if termo:
        alvo = f"%{normalizar(termo)}%"
        consulta = consulta.where(db.transacoes.c.descricao_norm.like(alvo))
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


# --------------------------------------------------------------------------
# contas e plano de contas
# --------------------------------------------------------------------------
def salvar_conta(engine, *, conta_id=None, nome, tipo, titular, instituicao, parser, ativa=True):
    with engine.begin() as conn:
        valores = dict(
            nome=nome.strip(), tipo=tipo, titular=titular,
            instituicao=instituicao.strip(), parser=parser, ativa=ativa,
        )
        if conta_id:
            conn.execute(sa.update(db.contas).where(db.contas.c.id == conta_id).values(**valores))
            return conta_id
        return conn.execute(sa.insert(db.contas).values(**valores)).inserted_primary_key[0]


def alternar_conta(engine, conta_id: int, ativa: bool) -> None:
    with engine.begin() as conn:
        conn.execute(sa.update(db.contas).where(db.contas.c.id == conta_id).values(ativa=ativa))


def salvar_categoria(engine, *, categoria_id=None, nome, natureza, ativa=True):
    with engine.begin() as conn:
        if categoria_id:
            conn.execute(
                sa.update(db.categorias)
                .where(db.categorias.c.id == categoria_id)
                .values(nome=nome.strip(), ativa=ativa)
            )
            return categoria_id
        ordem = (conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(db.categorias.c.ordem), 0)).where(
                db.categorias.c.natureza == natureza
            )
        ).scalar() or 0) + 1
        return conn.execute(
            sa.insert(db.categorias).values(
                nome=nome.strip(), natureza=natureza, ordem=ordem, ativa=ativa
            )
        ).inserted_primary_key[0]


def salvar_subcategoria(engine, *, categoria_id: int, nome: str):
    with engine.begin() as conn:
        ordem = (conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(db.subcategorias.c.ordem), 0)).where(
                db.subcategorias.c.categoria_id == categoria_id
            )
        ).scalar() or 0) + 1
        return conn.execute(
            sa.insert(db.subcategorias).values(
                categoria_id=categoria_id, nome=nome.strip(), ordem=ordem
            )
        ).inserted_primary_key[0]


def salvar_metas(engine, ano: int, percentuais: dict[int, float]) -> None:
    with engine.begin() as conn:
        for categoria_id, pct in percentuais.items():
            existente = conn.execute(
                sa.select(db.metas.c.id).where(
                    db.metas.c.ano == ano, db.metas.c.categoria_id == categoria_id
                )
            ).scalar()
            if existente:
                conn.execute(
                    sa.update(db.metas).where(db.metas.c.id == existente).values(percentual=pct)
                )
            else:
                conn.execute(
                    sa.insert(db.metas).values(ano=ano, categoria_id=categoria_id, percentual=pct)
                )


def listar_metas(conn, ano: int) -> dict[int, float]:
    return {
        linha.categoria_id: linha.percentual
        for linha in conn.execute(
            sa.select(db.metas.c.categoria_id, db.metas.c.percentual).where(db.metas.c.ano == ano)
        )
    }


def listar_uploads(conn, limite: int = 20) -> list[dict]:
    consulta = (
        sa.select(
            db.uploads.c.id,
            db.uploads.c.arquivo,
            db.uploads.c.competencia,
            db.uploads.c.origem,
            db.uploads.c.enviado_por,
            db.uploads.c.lidos,
            db.uploads.c.importados,
            db.uploads.c.auto,
            db.uploads.c.pendentes,
            db.uploads.c.duplicados,
            db.uploads.c.criado_em,
            db.contas.c.nome.label("conta"),
        )
        .select_from(db.uploads.outerjoin(db.contas, db.uploads.c.conta_id == db.contas.c.id))
        .order_by(db.uploads.c.id.desc())
        .limit(limite)
    )
    return [dict(linha._mapping) for linha in conn.execute(consulta)]


def competencias_disponiveis(conn) -> list[str]:
    return [
        linha.competencia
        for linha in conn.execute(
            sa.select(db.transacoes.c.competencia)
            .where(db.transacoes.c.ativo == sa.true())
            .distinct()
            .order_by(db.transacoes.c.competencia.desc())
        )
    ]


def apagar_upload(engine, upload_id: int) -> int:
    """Desfaz uma importacao inteira - o 'undo' de um arquivo errado."""
    with engine.begin() as conn:
        ids = [
            linha.id
            for linha in conn.execute(
                sa.select(db.transacoes.c.id).where(db.transacoes.c.upload_id == upload_id)
            )
        ]
        if ids:
            conn.execute(
                sa.delete(db.duplicidades).where(
                    sa.or_(
                        db.duplicidades.c.transacao_nova_id.in_(ids),
                        db.duplicidades.c.transacao_existente_id.in_(ids),
                    )
                )
            )
            conn.execute(sa.delete(db.transacoes).where(db.transacoes.c.id.in_(ids)))
        conn.execute(sa.delete(db.uploads).where(db.uploads.c.id == upload_id))
    return len(ids)
