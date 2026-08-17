"""O que garante que a tela continue rápida — e honesta enquanto rápida.

Velocidade aqui não é uma métrica de tempo, que varia com a máquina e não diz
nada num teste. É contagem de idas ao banco: o banco fica em São Paulo e o app
nos Estados Unidos, cada consulta custa uns 150ms de ida e volta, e é por isso
que um N+1 vira congelamento visível na tela.

Estes testes existem porque cada um deles já foi um problema real, e porque a
correção é do tipo que volta sozinha: basta alguém escrever um laço que parece
inocente.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, repo
from core.dedup import hash_lancamento
from core.texto import normalizar


class Contador:
    """Conta as consultas que passam pelo engine, sem olhar o relógio."""

    def __init__(self, engine):
        self.engine = engine
        self.n = 0

    def __enter__(self):
        sa.event.listen(self.engine, "before_cursor_execute", self._somar)
        return self

    def __exit__(self, *_):
        sa.event.remove(self.engine, "before_cursor_execute", self._somar)

    def _somar(self, *_a):
        self.n += 1


def _inserir(conn, dia, descricao, valor, categoria_id=None, subcategoria_id=None):
    conta_id = conn.execute(sa.select(db.contas.c.id).limit(1)).scalar_one()
    norm = normalizar(descricao)
    return conn.execute(
        sa.insert(db.transacoes).values(
            data=dia, competencia=dia.strftime("%Y-%m"), descricao=descricao,
            descricao_norm=norm, valor_centavos=valor, conta_id=conta_id,
            categoria_id=categoria_id, subcategoria_id=subcategoria_id,
            pessoa="Casal", status="manual" if categoria_id else "pendente",
            confianca=1.0, origem="extrato",
            hash_dedup=hash_lancamento(conta_id, dia, valor, norm), ativo=True,
        )
    ).inserted_primary_key[0]


# ---------------------------------------------------------------------------
# o custo não pode crescer com o número de categorias
# ---------------------------------------------------------------------------
def test_uso_das_categorias_nao_cresce_com_o_plano(engine, conn):
    """Era uma consulta por categoria — 72 idas ao banco para desenhar a tela.

    O comentário na tela já dizia "numa passada só"; o código fazia quatro
    contagens vezes dezoito categorias. O teste prende o comentário ao código.
    """
    with Contador(engine) as contador:
        usos = repo.usos_das_categorias(conn)

    quantas = len(usos)
    assert quantas >= 15                       # o plano inteiro veio
    assert contador.n <= 6, f"{contador.n} consultas para {quantas} categorias"


def test_usos_em_lote_dizem_o_mesmo_que_a_versao_de_uma_categoria(engine, conn):
    """A versão rápida só vale se responder igual à que ela substituiu."""
    saude = conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Saúde")
    ).scalar_one()
    _inserir(conn, date(2026, 8, 3), "DROGARIA", -12_000, saude)

    em_lote = repo.usos_das_categorias(conn)
    for categoria_id in list(em_lote)[:6]:
        assert em_lote[categoria_id] == repo.uso_da_categoria(conn, categoria_id)


def test_cobertura_de_doze_meses_e_uma_consulta_so(engine, conn):
    """A leitura do ano perguntava mês a mês: doze idas para uma resposta."""
    meses = [f"2026-{m:02d}" for m in range(1, 13)]
    for m in range(1, 13):
        _inserir(conn, date(2026, m, 10), "SUPERMERCADO", -50_000)

    with Contador(engine) as contador:
        coberturas = analytics.cobertura_por_competencia(conn, meses)

    assert len(coberturas) == 12
    assert contador.n == 1, f"{contador.n} consultas para 12 meses"


def test_cobertura_em_lote_bate_com_a_de_um_mes(engine, conn):
    saude = conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Saúde")
    ).scalar_one()
    _inserir(conn, date(2026, 7, 3), "CONSULTA", -30_000, saude)
    _inserir(conn, date(2026, 8, 3), "DROGARIA", -12_000)          # pendente

    em_lote = analytics.cobertura_por_competencia(conn, ["2026-07", "2026-08"])
    for mes in ("2026-07", "2026-08"):
        assert em_lote[mes] == analytics.cobertura_da_classificacao(conn, mes)


def test_mes_sem_gasto_volta_com_resposta_de_mes_vazio(engine, conn):
    """Mês sem linha nenhuma some do GROUP BY e mesmo assim precisa responder."""
    vazio = analytics.cobertura_por_competencia(conn, ["2026-01"])["2026-01"]
    assert vazio["gasto_total"] == 0
    assert vazio["percentual_classificado"] == 100.0


def test_subcategorias_de_todas_dizem_o_mesmo_que_uma_a_uma(engine, conn):
    saude = conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == "Saúde")
    ).scalar_one()
    sub = conn.execute(
        sa.select(db.subcategorias.c.id).where(db.subcategorias.c.categoria_id == saude).limit(1)
    ).scalar_one()
    _inserir(conn, date(2026, 8, 3), "FARMACIA", -10_000, saude, sub)
    _inserir(conn, date(2026, 8, 4), "CLINICA", -20_000, saude)     # sem subcategoria

    with Contador(engine) as contador:
        em_lote = analytics.subcategorias_de_todas(conn, competencia="2026-08")

    assert contador.n == 1
    # a junção externa tem de continuar trazendo o que está sem subcategoria
    assert em_lote[saude] == analytics.por_subcategoria(conn, saude, competencia="2026-08")
    assert any(not linha["detalhada"] for linha in em_lote[saude])


def test_contagem_de_pendentes_nao_para_em_500(engine, conn):
    """O crachá trazia 500 linhas para contar, e mentia a partir da 501ª."""
    for i in range(12):
        _inserir(conn, date(2026, 8, 1), f"PIX SEM NOME {i}", -1_000 - i)

    with Contador(engine) as contador:
        quantos = repo.contar_pendentes(conn)

    assert quantos == 12
    assert contador.n == 1


# ---------------------------------------------------------------------------
# o carimbo que faz o cache da tela ser seguro
# ---------------------------------------------------------------------------
def test_qualquer_escrita_muda_a_versao_dos_dados(engine, conn):
    """É este número que joga o cache da tela fora.

    Ele sobe pela própria escrita, e não por alguém lembrar de limpar o cache
    depois de gravar — que é como cache vira tela mentindo em silêncio.
    """
    antes = db.versao_dos_dados()
    repo.contar_pendentes(conn)                       # ler não conta
    assert db.versao_dos_dados() == antes

    _inserir(conn, date(2026, 8, 5), "COMPRA", -5_000)
    assert db.versao_dos_dados() > antes

    depois_da_escrita = db.versao_dos_dados()
    conn.execute(
        sa.update(db.transacoes)
        .where(db.transacoes.c.descricao == "COMPRA")
        .values(pessoa="André")
    )
    assert db.versao_dos_dados() > depois_da_escrita


def test_salvar_metas_nao_cresce_com_o_numero_de_categorias(engine, conn):
    """Eram um SELECT e um UPDATE por categoria — 26 idas num clique."""
    ids = conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.natureza == "despesa")
    ).scalars().all()
    conn.commit()

    metas = {categoria_id: 5.0 for categoria_id in ids}
    with Contador(engine) as contador:
        repo.salvar_metas(engine, 2026, metas)

    with engine.connect() as leitura:
        gravadas = repo.listar_metas(leitura, 2026)
    assert gravadas == metas
    assert contador.n <= 5, f"{contador.n} consultas para {len(ids)} categorias"

    # e o segundo salvamento é um upsert: não duplica nem vira uma por linha
    metas[ids[0]] = 9.0
    with Contador(engine) as contador:
        repo.salvar_metas(engine, 2026, metas)
    with engine.connect() as leitura:
        assert repo.listar_metas(leitura, 2026) == metas
    assert contador.n <= 6, f"{contador.n} consultas no segundo salvamento"


# ---------------------------------------------------------------------------
# a descrição do extrato não pode virar formatação na tela
# ---------------------------------------------------------------------------
def test_asterisco_do_estabelecimento_nao_vira_italico():
    """Foi visto na tela: "PCART*TAB*SAO PAULO" saiu com TAB em itálico.

    Os asteriscos são do nome do estabelecimento, não marcação — e o recado
    que confirma o que foi salvo comia parte do nome bem na hora de dizê-lo.
    """
    from core.texto import sem_marcacao

    assert sem_marcacao("PCART*TAB*SAO PAULO") == r"PCART\*TAB\*SAO PAULO"
    assert sem_marcacao("MERCADO_LIVRE") == r"MERCADO\_LIVRE"
    assert sem_marcacao("IFOOD 123") == "IFOOD 123"      # nada a escapar
