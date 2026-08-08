"""Conexao e schema do banco.

Roda em SQLite (desenvolvimento/testes, arquivo local) ou PostgreSQL do
Supabase (producao, base unica compartilhada). O schema e o mesmo nos dois:
basta definir DATABASE_URL em .streamlit/secrets.toml ou no ambiente.
"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa

metadata = sa.MetaData()

PESSOAS = ("André", "Rô", "Casal")
NATUREZAS = ("receita", "despesa")

contas = sa.Table(
    "contas", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("nome", sa.String(80), nullable=False, unique=True),
    sa.Column("tipo", sa.String(20), nullable=False),          # cartao | corrente
    sa.Column("titular", sa.String(20), nullable=False),        # André | Rô | Casal
    sa.Column("instituicao", sa.String(60), nullable=False),
    sa.Column("parser", sa.String(40), nullable=False, server_default="generico"),
    sa.Column("ativa", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("criada_em", sa.DateTime, server_default=sa.func.now()),
)

categorias = sa.Table(
    "categorias", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("nome", sa.String(60), nullable=False),
    sa.Column("natureza", sa.String(10), nullable=False),        # receita | despesa
    sa.Column("ordem", sa.Integer, nullable=False, server_default="0"),
    sa.Column("ativa", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.UniqueConstraint("nome", "natureza", name="uq_categoria_nome_natureza"),
)

subcategorias = sa.Table(
    "subcategorias", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("categoria_id", sa.Integer, sa.ForeignKey("categorias.id"), nullable=False),
    sa.Column("nome", sa.String(60), nullable=False),
    sa.Column("ordem", sa.Integer, nullable=False, server_default="0"),
    sa.Column("ativa", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.UniqueConstraint("categoria_id", "nome", name="uq_subcategoria"),
)

transacoes = sa.Table(
    "transacoes", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("data", sa.Date, nullable=False),
    sa.Column("competencia", sa.String(7), nullable=False),      # AAAA-MM
    sa.Column("descricao", sa.Text, nullable=False),
    sa.Column("descricao_norm", sa.String(200), nullable=False),
    # negativo = saida, positivo = entrada
    sa.Column("valor_centavos", sa.BigInteger, nullable=False),
    sa.Column("conta_id", sa.Integer, sa.ForeignKey("contas.id"), nullable=False),
    sa.Column("categoria_id", sa.Integer, sa.ForeignKey("categorias.id")),
    sa.Column("subcategoria_id", sa.Integer, sa.ForeignKey("subcategorias.id")),
    sa.Column("pessoa", sa.String(20), nullable=False),
    # pendente | auto_memoria | auto_regra | auto_ia | manual
    sa.Column("status", sa.String(20), nullable=False, server_default="pendente"),
    sa.Column("confianca", sa.Float),
    sa.Column("origem", sa.String(20), nullable=False, server_default="extrato"),  # extrato | planilha | manual
    sa.Column("hash_dedup", sa.String(64), nullable=False, index=True),
    sa.Column("upload_id", sa.Integer, sa.ForeignKey("uploads.id")),
    # false enquanto pende confirmacao de duplicidade: fica fora dos relatorios
    sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("observacao", sa.Text),
    sa.Column("classificado_por", sa.String(20)),
    sa.Column("criado_em", sa.DateTime, server_default=sa.func.now()),
)

regras = sa.Table(
    "regras", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("padrao", sa.String(200), nullable=False),
    sa.Column("tipo_match", sa.String(15), nullable=False, server_default="contem"),  # contem | exato | regex
    sa.Column("categoria_id", sa.Integer, sa.ForeignKey("categorias.id"), nullable=False),
    sa.Column("subcategoria_id", sa.Integer, sa.ForeignKey("subcategorias.id")),
    sa.Column("pessoa", sa.String(20)),
    sa.Column("prioridade", sa.Integer, nullable=False, server_default="100"),
    sa.Column("origem", sa.String(15), nullable=False, server_default="sistema"),  # sistema | aprendida
    sa.Column("criada_por", sa.String(20)),
    sa.Column("criada_em", sa.DateTime, server_default=sa.func.now()),
    sa.UniqueConstraint("padrao", "tipo_match", name="uq_regra_padrao"),
)

metas = sa.Table(
    "metas", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("ano", sa.Integer, nullable=False),
    sa.Column("categoria_id", sa.Integer, sa.ForeignKey("categorias.id"), nullable=False),
    sa.Column("percentual", sa.Float, nullable=False),
    sa.UniqueConstraint("ano", "categoria_id", name="uq_meta_ano_categoria"),
)

config = sa.Table(
    "config", metadata,
    sa.Column("chave", sa.String(50), primary_key=True),
    sa.Column("valor", sa.Text),
)

uploads = sa.Table(
    "uploads", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("arquivo", sa.String(200), nullable=False),
    sa.Column("conta_id", sa.Integer, sa.ForeignKey("contas.id")),
    sa.Column("competencia", sa.String(7)),
    sa.Column("origem", sa.String(20), nullable=False, server_default="extrato"),
    sa.Column("enviado_por", sa.String(20)),
    sa.Column("lidos", sa.Integer, server_default="0"),
    sa.Column("importados", sa.Integer, server_default="0"),
    sa.Column("auto", sa.Integer, server_default="0"),
    sa.Column("pendentes", sa.Integer, server_default="0"),
    sa.Column("duplicados", sa.Integer, server_default="0"),
    sa.Column("criado_em", sa.DateTime, server_default=sa.func.now()),
)

duplicidades = sa.Table(
    "duplicidades", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    # fica nulo depois que a transacao nova e apagada em resolver(..., "excluir"):
    # sem isso a FK trava a exclusao (a linha de duplicidade ainda apontaria
    # para um id que deixou de existir).
    sa.Column("transacao_nova_id", sa.Integer, sa.ForeignKey("transacoes.id"), nullable=True),
    sa.Column("transacao_existente_id", sa.Integer, sa.ForeignKey("transacoes.id"), nullable=False),
    sa.Column("tipo", sa.String(15), nullable=False),           # exata | provavel
    sa.Column("motivo", sa.String(200)),
    sa.Column("resolvida", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("decisao", sa.String(20)),                         # excluida | mantida
    sa.Column("decidida_por", sa.String(20)),
    sa.Column("criada_em", sa.DateTime, server_default=sa.func.now()),
)

_engine = None


def url_do_banco() -> str:
    """Prioridade: st.secrets -> variavel de ambiente -> SQLite local."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            import streamlit as st

            url = st.secrets.get("DATABASE_URL")  # type: ignore[assignment]
        except Exception:
            url = None
    if not url:
        destino = Path(__file__).resolve().parent.parent / "dados" / "financas.db"
        destino.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{destino}"
    # o Supabase entrega a URL como postgres://; SQLAlchemy 2 exige postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def diagnostico() -> dict:
    """Em que estado esta a conexao — para a barra lateral explicar o problema.

    Separa os dois casos que, vistos de fora, pareciam a mesma coisa: o segredo
    nao chegou ao app, ou chegou e a conexao falhou.
    """
    url = url_do_banco()
    if url.startswith("sqlite"):
        return {"ok": False, "motivo": "sem_url", "detalhe": ""}
    try:
        with get_engine().connect() as conn:
            conn.execute(sa.text("select 1"))
        return {"ok": True, "motivo": "conectado", "detalhe": ""}
    except Exception as exc:  # senha errada, host errado, rede
        return {"ok": False, "motivo": "falha_conexao",
                "detalhe": f"{type(exc).__name__}: {exc}"[:300]}


def get_engine():
    global _engine
    if _engine is None:
        url = url_do_banco()
        kwargs = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs.pop("pool_pre_ping")
            kwargs["connect_args"] = {"check_same_thread": False}
            # o caminho pode vir de DATABASE_URL apontando para pasta inexistente
            caminho = url.split("///", 1)[-1]
            if caminho and caminho != ":memory:":
                Path(caminho).expanduser().parent.mkdir(parents=True, exist_ok=True)
        _engine = sa.create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @sa.event.listens_for(_engine, "connect")
            def _fk_on(dbapi_con, _):
                dbapi_con.execute("PRAGMA foreign_keys=ON")
    return _engine


def resetar_engine() -> None:
    """Usado pelos testes para trocar de banco entre um caso e outro."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def criar_schema(engine=None) -> None:
    metadata.create_all(engine or get_engine())
