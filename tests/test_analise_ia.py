"""A seção de Análise IA, com um cliente falso no lugar da API.

Nenhum teste aqui sai para a rede. O que se testa é o que fica do nosso lado:
o que vai dentro do contexto, as travas antes de gravar, a persistência do
texto e a recusa de uma subcategoria que não existe no plano.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa

from core import ai, analytics, db, repo
from core.dedup import hash_lancamento
from core.texto import normalizar


# ---------------------------------------------------------------------------
# apoio
# ---------------------------------------------------------------------------
@dataclass
class _Resposta:
    content: list


@dataclass
class _Bloco:
    text: str


class _ClienteFalso:
    """Devolve sempre a mesma resposta e guarda o prompt que recebeu."""

    def __init__(self, texto: str):
        self.texto = texto
        self.prompts: list[str] = []
        self.messages = self

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        return _Resposta(content=[_Bloco(text=self.texto)])


def _ligar_ia(monkeypatch, texto: str) -> _ClienteFalso:
    cliente = _ClienteFalso(texto)
    monkeypatch.setattr(ai, "disponivel", lambda: True)
    monkeypatch.setattr(ai, "_cliente", lambda: cliente)
    return cliente


def _categoria_id(conn, nome: str) -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(db.categorias.c.nome == nome)
    ).scalar_one()


def _subcategorias(conn, categoria_id: int) -> list[str]:
    return [
        linha.nome
        for linha in conn.execute(
            sa.select(db.subcategorias.c.nome).where(
                db.subcategorias.c.categoria_id == categoria_id
            )
        )
    ]


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
# cobertura: o número que precede qualquer conclusão
# ---------------------------------------------------------------------------
def test_cobertura_mede_o_que_ainda_esta_na_fila(engine, conn):
    """Uma análise sobre um mês metade classificado descreve metade do mês."""
    alimentacao = _categoria_id(conn, "Alimentação")
    _inserir(conn, date(2026, 8, 3), "SUPERMERCADO", -60_000, alimentacao)
    _inserir(conn, date(2026, 8, 4), "PIX SEM NOME", -40_000)          # pendente

    cobertura = analytics.cobertura_da_classificacao(conn, "2026-08")

    assert cobertura["gasto_total"] == 100_000
    assert cobertura["gasto_classificado"] == 60_000
    assert cobertura["percentual_classificado"] == 60.0
    assert cobertura["sem_categoria"] == 1


def test_cobertura_conta_quem_ficou_sem_subcategoria(engine, conn):
    """O caso de quem classifica em volume: categoria sim, subcategoria não."""
    saude = _categoria_id(conn, "Saúde")
    sub = conn.execute(
        sa.select(db.subcategorias.c.id).where(db.subcategorias.c.categoria_id == saude).limit(1)
    ).scalar_one()
    _inserir(conn, date(2026, 8, 3), "FARMACIA", -10_000, saude, sub)
    _inserir(conn, date(2026, 8, 4), "CLINICA", -20_000, saude)

    cobertura = analytics.cobertura_da_classificacao(conn, "2026-08")

    assert cobertura["sem_subcategoria"] == 1
    assert cobertura["percentual_classificado"] == 100.0
    assert cobertura["percentual_com_subcategoria"] < 100.0


def test_transferencia_nao_conta_como_gasto_por_classificar(engine, conn):
    """Pagar a fatura não é gasto — nem gasto pendente de classificação."""
    transf = _categoria_id(conn, analytics.CATEGORIA_TRANSFERENCIA)
    _inserir(conn, date(2026, 8, 10), "PAGAMENTO DE FATURA", -500_000, transf)
    alimentacao = _categoria_id(conn, "Alimentação")
    _inserir(conn, date(2026, 8, 3), "SUPERMERCADO", -60_000, alimentacao)

    cobertura = analytics.cobertura_da_classificacao(conn, "2026-08")

    assert cobertura["gasto_total"] == 60_000
    # e nem aparece na lista de gasto por categoria, para ninguém somá-la de volta
    texto = analytics.contexto_para_ia(conn, "2026-08")
    linhas_de_categoria = texto.split("Gasto por categoria no mês:")[1].split("\n\n")[0]
    assert analytics.CATEGORIA_TRANSFERENCIA not in linhas_de_categoria


# ---------------------------------------------------------------------------
# contexto: a IA só pode dizer o que está aqui
# ---------------------------------------------------------------------------
def test_contexto_avisa_o_que_falta_classificar(engine, conn):
    alimentacao = _categoria_id(conn, "Alimentação")
    _inserir(conn, date(2026, 8, 3), "SUPERMERCADO", -60_000, alimentacao)
    _inserir(conn, date(2026, 8, 4), "PIX SEM NOME", -40_000)

    texto = analytics.contexto_para_ia(conn, "2026-08")

    assert "COBERTURA DA CLASSIFICAÇÃO" in texto
    assert "60%" in texto
    assert "Por pessoa neste mês" in texto


def test_contexto_traz_o_que_fugiu_da_media_do_ano(engine, conn):
    """"Fora do padrão" é conta nossa, não estimativa da IA."""
    alimentacao = _categoria_id(conn, "Alimentação")
    for mes in (1, 2, 3, 4, 5, 6, 7):
        _inserir(conn, date(2026, mes, 10), "SUPERMERCADO", -100_000, alimentacao)
    _inserir(conn, date(2026, 8, 10), "SUPERMERCADO FESTA", -400_000, alimentacao)

    texto = analytics.contexto_para_ia(conn, "2026-08")

    assert "Fora do padrão" in texto
    assert "Alimentação" in texto


def test_com_um_mes_so_nao_existe_fora_do_padrao(engine, conn):
    """Um alarme que dispara sempre não avisa nada.

    Com um único mês importado, comparar contra "acumulado do ano ÷ meses
    decorridos" acusava +700% em toda categoria — e a IA escreveria que tudo
    explodiu, quando o que houve foi um mês só de dados.
    """
    alimentacao = _categoria_id(conn, "Alimentação")
    _inserir(conn, date(2026, 3, 10), "SUPERMERCADO", -140_356, alimentacao)

    assert analytics.desvios_do_mes(conn, "2026-03") == []
    texto = analytics.contexto_para_ia(conn, "2026-03")
    assert "não há meses suficientes" in texto


def test_transferencia_nunca_entra_no_fora_do_padrao(engine, conn):
    """Pagar a fatura varia muito de mês para mês e não é gasto."""
    transf = _categoria_id(conn, analytics.CATEGORIA_TRANSFERENCIA)
    for mes, valor in ((5, -100_000), (6, -120_000), (7, -110_000)):
        _inserir(conn, date(2026, mes, 10), "PAGAMENTO DE FATURA", valor, transf)
    _inserir(conn, date(2026, 8, 10), "PAGAMENTO DE FATURA", -900_000, transf)

    categorias = [item["categoria"] for item in analytics.desvios_do_mes(conn, "2026-08")]
    assert analytics.CATEGORIA_TRANSFERENCIA not in categorias


def test_contexto_separa_compromisso_que_se_repete_todo_mes(engine, conn):
    """Cortar assinatura vale o ano; cortar um jantar vale uma semana."""
    assinaturas = _categoria_id(conn, "Assinaturas & Tecnologia")
    for mes in (5, 6, 7, 8):
        _inserir(conn, date(2026, mes, 9), "Dm*Spotify", -4_090, assinaturas)
    _inserir(conn, date(2026, 8, 12), "RESTAURANTE DA ESQUINA", -30_000, assinaturas)

    fixos = analytics.compromissos_recorrentes(conn, "2026-08")
    nomes = [item["estabelecimento"] for item in fixos]

    assert any("SPOTIFY" in nome for nome in nomes)
    assert not any("RESTAURANTE" in nome for nome in nomes)


# ---------------------------------------------------------------------------
# a leitura do mês, gravada
# ---------------------------------------------------------------------------
def test_analise_fica_gravada_e_volta_com_quem_gerou(engine, conn, monkeypatch):
    _ligar_ia(monkeypatch, "O mês fechou dentro da média.")
    contexto = analytics.contexto_para_ia(conn, "2026-08")

    texto = ai.analisar_mes(contexto)
    repo.salvar_analise(
        engine, competencia="2026-08", texto=texto, modelo=ai.MODELO_ANALISE,
        contexto=contexto, usuario="André",
    )

    with engine.connect() as leitura:
        guardada = repo.ultima_analise(leitura, "2026-08", contexto)
    assert guardada["texto"] == "O mês fechou dentro da média."
    assert guardada["gerada_por"] == "André"
    assert guardada["desatualizada"] is False


def test_analise_se_marca_desatualizada_quando_os_numeros_mudam(engine, conn, monkeypatch):
    """Texto velho lido como se fosse de hoje é pior que texto nenhum."""
    _ligar_ia(monkeypatch, "Análise do mês.")
    contexto = analytics.contexto_para_ia(conn, "2026-08")
    repo.salvar_analise(
        engine, competencia="2026-08", texto="Análise do mês.", modelo="m",
        contexto=contexto, usuario="Rô",
    )

    alimentacao = _categoria_id(conn, "Alimentação")
    _inserir(conn, date(2026, 8, 20), "MERCADO NOVO", -90_000, alimentacao)
    conn.commit()

    with engine.connect() as leitura:
        novo = analytics.contexto_para_ia(leitura, "2026-08")
        guardada = repo.ultima_analise(leitura, "2026-08", novo)
    assert guardada["desatualizada"] is True


def test_prompt_leva_a_cobertura_e_a_proibicao_de_inventar(engine, conn, monkeypatch):
    """A trava principal precisa chegar ao modelo, não só ao nosso código."""
    cliente = _ligar_ia(monkeypatch, "ok")
    ai.analisar_mes(analytics.contexto_para_ia(conn, "2026-08"))

    prompt = cliente.prompts[0]
    assert "COBERTURA DA CLASSIFICAÇÃO" in prompt
    assert "Não estime" in prompt or "não estime" in prompt


def test_sem_chave_a_analise_explica_em_vez_de_quebrar(engine, conn, monkeypatch):
    monkeypatch.setattr(ai, "disponivel", lambda: False)
    resposta = ai.analisar_mes("qualquer contexto")
    assert "não configurada" in resposta
    assert "ANTHROPIC_API_KEY" in resposta


def test_pergunta_livre_usa_os_mesmos_numeros(engine, conn, monkeypatch):
    cliente = _ligar_ia(monkeypatch, "Ficou caro por causa da viagem.")
    contexto = analytics.contexto_para_ia(conn, "2026-08")

    resposta = ai.responder_pergunta(contexto, "Por que este mês ficou caro?")

    assert resposta == "Ficou caro por causa da viagem."
    assert "Por que este mês ficou caro?" in cliente.prompts[0]
    assert "COBERTURA DA CLASSIFICAÇÃO" in cliente.prompts[0]


# ---------------------------------------------------------------------------
# subcategoria: a pergunta estreita, com a categoria já decidida por gente
# ---------------------------------------------------------------------------
def test_sugestao_de_subcategoria_nao_mexe_na_categoria(engine, conn, monkeypatch):
    saude = _categoria_id(conn, "Saúde")
    opcoes = _subcategorias(conn, saude)
    _inserir(conn, date(2026, 8, 5), "DROGARIA SAO PAULO", -12_000, saude)
    conn.commit()

    cliente = _ligar_ia(monkeypatch, f'[{{"i": 0, "subcategoria": "{opcoes[0]}", "confianca": 0.95}}]')
    with engine.connect() as leitura:
        sugestoes = repo.sugerir_subcategorias(leitura, competencia="2026-08")

    assert len(sugestoes) == 1
    assert sugestoes[0]["subcategoria"] == opcoes[0]
    assert sugestoes[0]["categoria"] == "Saúde"
    # o prompt pergunta a subcategoria, e oferece só as opções daquela categoria
    assert "SUBCATEGORIA" in cliente.prompts[0]
    assert opcoes[0] in cliente.prompts[0]


def test_subcategoria_inventada_pela_ia_e_descartada(engine, conn, monkeypatch):
    """Nome parecido não basta: ou existe naquela categoria, ou não entra."""
    saude = _categoria_id(conn, "Saúde")
    _inserir(conn, date(2026, 8, 5), "DROGARIA", -12_000, saude)
    conn.commit()

    _ligar_ia(monkeypatch, '[{"i": 0, "subcategoria": "Remédios e afins", "confianca": 0.99}]')
    with engine.connect() as leitura:
        assert repo.sugerir_subcategorias(leitura, competencia="2026-08") == []


def test_aplicar_subcategoria_nao_sobrescreve_quem_ja_tem(engine, conn):
    saude = _categoria_id(conn, "Saúde")
    opcoes = _subcategorias(conn, saude)
    ja_tem = conn.execute(
        sa.select(db.subcategorias.c.id).where(
            db.subcategorias.c.categoria_id == saude, db.subcategorias.c.nome == opcoes[0]
        )
    ).scalar_one()
    com_sub = _inserir(conn, date(2026, 8, 5), "CONSULTA", -20_000, saude, ja_tem)
    sem_sub = _inserir(conn, date(2026, 8, 6), "DROGARIA", -12_000, saude)
    conn.commit()

    gravadas = repo.aplicar_subcategorias(
        engine, {com_sub: opcoes[1], sem_sub: opcoes[1]}, "André"
    )

    assert gravadas == 1
    with engine.connect() as leitura:
        depois = {
            linha.id: linha.subcategoria_id
            for linha in leitura.execute(
                sa.select(db.transacoes.c.id, db.transacoes.c.subcategoria_id).where(
                    db.transacoes.c.id.in_([com_sub, sem_sub])
                )
            )
        }
    assert depois[com_sub] == ja_tem                       # intocado
    assert depois[sem_sub] is not None and depois[sem_sub] != ja_tem


def test_sem_chave_a_tela_de_subcategoria_nao_chama_nada(engine, conn, monkeypatch):
    saude = _categoria_id(conn, "Saúde")
    _inserir(conn, date(2026, 8, 5), "DROGARIA", -12_000, saude)
    conn.commit()

    monkeypatch.setattr(ai, "disponivel", lambda: False)

    def _explode():
        raise AssertionError("não pode tentar falar com a API sem chave")

    monkeypatch.setattr(ai, "_cliente", _explode)
    with engine.connect() as leitura:
        assert repo.sugerir_subcategorias(leitura, competencia="2026-08") == []
        # a lista do que falta continua respondendo, para a tela mostrar
        assert len(repo.sem_subcategoria(leitura, competencia="2026-08")) == 1
