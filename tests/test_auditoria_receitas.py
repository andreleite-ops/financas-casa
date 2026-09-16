"""Auditoria: por onde a receita de uma competência pode contar duas vezes.

Contexto do sintoma: Visão Geral em 2026-09, pessoa "Todos", receitas de
R$ 103.898,62 com badge "+107.2% vs mês anterior" e despesas de R$ 8.191,12
("-89.7%"). O dono já desfez a importação da fatura de cartão lida com o sinal
trocado e mesmo assim a receita de setembro continuou a mesma.

Cada teste aqui é uma hipótese. Os que FALHAM são bugs demonstrados; os que
PASSAM descartam a hipótese — e isso também é resultado. O cabeçalho de cada
teste diz de qual lado ele está.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from core import analytics, db, dedup, repo
from parsers.base import Lancamento

# ---------------------------------------------------------------------------
# helpers (mesmo estilo de tests/test_receita_prevista.py)
# ---------------------------------------------------------------------------


def _conta(engine, nome: str, tipo: str = "corrente", titular: str = "André") -> int:
    with engine.begin() as conn:
        return conn.execute(
            sa.insert(db.contas).values(
                nome=nome, tipo=tipo, titular=titular,
                instituicao="Banco", parser="generico", ativa=True,
            )
        ).inserted_primary_key[0]


def _categoria(conn, nome: str, natureza: str = "receita") -> int:
    return conn.execute(
        sa.select(db.categorias.c.id).where(
            db.categorias.c.nome == nome, db.categorias.c.natureza == natureza
        )
    ).scalar_one()


def _prever(engine, conn, competencia: str, valor: int, descricao="PRO LABORE",
            pessoa="André") -> int:
    trabalho = _categoria(conn, "Trabalho")
    return repo.lancar_manual(
        engine, competencia=competencia, valor_centavos=valor, pessoa=pessoa,
        categoria_id=trabalho, subcategoria_id=None, descricao=descricao,
        usuario="André", natureza="receita",
    )


def _importar(engine, conta_id, linhas, *, origem="extrato", arquivo="arq.csv",
              competencia=None, pessoa_padrao="André"):
    return repo.importar(
        engine, lancamentos=linhas, conta_id=conta_id, arquivo=arquivo,
        origem=origem, competencia=competencia, usuario="André",
        pessoa_padrao=pessoa_padrao, usar_ia=False,
    )


def _receitas(engine, competencia: str) -> int:
    with engine.connect() as conn:
        return analytics.resumo(conn, competencia=competencia)["receitas"]


def _ativo(engine, transacao_id: int) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(db.transacoes.c.ativo).where(db.transacoes.c.id == transacao_id)
        ).scalar_one()


# ===========================================================================
# 1. A agregação: `resumo`/`_base` podem multiplicar linhas?
# ===========================================================================


def test_resumo_nao_multiplica_com_categorias_de_mesmo_nome(engine, conn):
    """HIPÓTESE (esperada FALSA): o outerjoin duplicaria a linha.

    `UniqueConstraint("nome","natureza")` deixa existirem duas categorias
    "Trabalho", uma receita e uma despesa. Se o join de `resumo` fosse por
    nome, a mesma transação apareceria nos dois lados. Ele é por id.
    """
    with engine.begin() as escrita:
        escrita.execute(
            sa.insert(db.categorias).values(nome="Trabalho", natureza="despesa", ordem=99)
        )
        receita_id = _categoria(escrita, "Trabalho", "receita")
        despesa_id = _categoria(escrita, "Trabalho", "despesa")
        assert receita_id != despesa_id
        # subcategoria homônima nas duas, para o segundo outerjoin também ter
        # chance de casar duas vezes
        for categoria_id in (receita_id, despesa_id):
            escrita.execute(
                sa.insert(db.subcategorias).values(
                    categoria_id=categoria_id, nome="Pró-labore", ordem=1
                )
            )
        sub_id = escrita.execute(
            sa.select(db.subcategorias.c.id).where(
                db.subcategorias.c.categoria_id == receita_id,
                db.subcategorias.c.nome == "Pró-labore",
            )
        ).scalar_one()

    id_previsto = repo.lancar_manual(
        engine, competencia="2026-09", valor_centavos=2_059_621, pessoa="André",
        categoria_id=receita_id, subcategoria_id=sub_id, descricao="PRO LABORE",
        usuario="André", natureza="receita",
    )
    assert id_previsto

    assert _receitas(engine, "2026-09") == 2_059_621


def test_resumo_conta_cada_transacao_uma_vez_so(engine, conn):
    """HIPÓTESE (esperada FALSA): fanout do join de subcategorias.

    Invariante forte: a soma dos cards tem de bater com a soma bruta das
    linhas ativas do mês, uma a uma. Inclui uma transação cuja subcategoria
    pertence a OUTRA categoria (dado inconsistente, mas possível no schema —
    não há checagem cruzada).
    """
    conta = _conta(engine, "Conta Salário")
    trabalho = _categoria(conn, "Trabalho")
    moradia = _categoria(conn, "Moradia", "despesa")
    sub_de_moradia = conn.execute(
        sa.select(db.subcategorias.c.id).where(
            db.subcategorias.c.categoria_id == moradia
        ).limit(1)
    ).scalar_one()
    conn.commit()

    _importar(engine, conta, [
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG",
                   valor_centavos=2_059_621),
        Lancamento(data=date(2026, 9, 6), descricao="SUPERMERCADO", valor_centavos=-50_000),
    ])
    # subcategoria "errada" (de Moradia) numa receita de Trabalho
    with engine.begin() as escrita:
        escrita.execute(
            sa.update(db.transacoes)
            .where(db.transacoes.c.valor_centavos == 2_059_621)
            .values(categoria_id=trabalho, subcategoria_id=sub_de_moradia)
        )

    with engine.connect() as leitura:
        bruto = leitura.execute(
            sa.select(sa.func.sum(db.transacoes.c.valor_centavos)).where(
                db.transacoes.c.competencia == "2026-09",
                db.transacoes.c.ativo == sa.true(),
                db.transacoes.c.valor_centavos > 0,
            )
        ).scalar()
        resumo = analytics.resumo(leitura, competencia="2026-09")

    assert resumo["receitas"] == bruto


# ===========================================================================
# 2. `previsao_equivalente`: quando a previsão NÃO é pareada
# ===========================================================================


def test_condicoes_do_pareamento_uma_a_uma():
    """Enumera, sem banco, o que faz `previsao_equivalente` devolver None.

    Documenta as seis portas que a previsão precisa atravessar. Este teste
    PASSA: ele não é um bug, é o contrato.
    """
    base = {
        "id": 1, "origem": "manual", "ativo": True, "valor_centavos": 480_000,
        "data": date(2026, 9, 28), "pessoa": "Rô", "descricao": "ATENDIMENTOS",
    }
    real = dict(dia=date(2026, 9, 10), valor_centavos=470_000, pessoa="Rô")

    assert dedup.previsao_equivalente([base], **real) is not None

    # (a) origem diferente de 'manual' — a receita da planilha inicial não é
    #     previsão para efeito desta regra
    assert dedup.previsao_equivalente([{**base, "origem": "planilha"}], **real) is None
    # (b) previsão já inativa (foi "realizada" por outro upload antes)
    assert dedup.previsao_equivalente([{**base, "ativo": False}], **real) is None
    # (c) mês diferente — compara DATA, não competência
    assert dedup.previsao_equivalente(
        [{**base, "data": date(2026, 8, 28)}], **real
    ) is None
    # (d) fora da folga de 20% — o caso da Rô, recebimento picado
    assert dedup.previsao_equivalente(
        [base], dia=date(2026, 9, 10), valor_centavos=30_000, pessoa="Rô"
    ) is None
    # (e) lançamento que não é entrada
    assert dedup.previsao_equivalente(
        [base], dia=date(2026, 9, 10), valor_centavos=-470_000, pessoa="Rô"
    ) is None
    # (f) `_usados`: quem chama já filtrou; a função não sabe disso sozinha
    assert dedup.previsao_equivalente([], **real) is None

def test_recebimento_picado_da_esposa_nao_pareia_e_por_isso_pergunta(engine, conn):
    """Dezesseis PIX de R$ 300 contra uma previsão de R$ 4.800.

    Nenhum dos dezesseis chega perto do total, e `previsao_equivalente` recusa
    todos pela folga de 20% — corretamente: casar R$ 300 com R$ 4.800 seria
    apagar a previsão inteira por causa de um paciente.

    Este é o limite honesto da regra automática, e ele é por omissão de
    propósito. O que não pode é a renda dobrar em silêncio: a soma dos créditos
    do mês bate com o total previsto, e é isso que vira pergunta. Quem decide é
    quem sabe — pela tela de Receitas, que desde este mês mostra previsão e
    extrato lado a lado com o alerta em vermelho.
    """
    conta = _conta(engine, "Conta Rô", titular="Rô")
    id_previsto = _prever(engine, conn, "2026-09", 480_000, "ATENDIMENTOS", pessoa="Rô")
    conn.commit()

    resumo = _importar(engine, conta, [
        Lancamento(data=date(2026, 9, 1 + i), descricao=f"PIX RECEBIDO PACIENTE {i}",
                   valor_centavos=30_000)
        for i in range(16)
    ], pessoa_padrao="Rô")

    assert resumo["previsoes_realizadas"] == 0
    assert _ativo(engine, id_previsto) is True
    assert len(resumo["previsoes_a_conferir"]) == 1, (
        "a soma dos PIX bate com o total previsto: isso tem de virar pergunta"
    )
    # e enquanto ninguém decide, os dois valem — o que a tela agora denuncia
    assert _receitas(engine, "2026-09") == 960_000


# ===========================================================================
# 3. `importar`: `substituir`, `marcar_usado` e o id temporário negativo
# ===========================================================================


def test_duas_entradas_no_mes_nao_consomem_a_mesma_previsao(engine, conn):
    """HIPÓTESE (esperada FALSA): `marcar_usado` não é chamado.

    Ele É chamado — core/repo.py:377-378, para qualquer decisão com
    `existente_id`, inclusive `realiza_previsao`. Duas entradas parecidas no
    mesmo mês não podem aposentar a mesma previsão duas vezes.
    """
    conta = _conta(engine, "Conta Salário")
    id_previsto = _prever(engine, conn, "2026-09", 2_059_621)
    conn.commit()

    resumo = _importar(engine, conta, [
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG",
                   valor_centavos=2_059_621),
        Lancamento(data=date(2026, 9, 20), descricao="TED PRO LABORE TAG 2",
                   valor_centavos=2_059_621),
    ])

    assert resumo["previsoes_realizadas"] == 1
    assert _ativo(engine, id_previsto) is False
    # as duas do extrato ficam, a previsão sai: 2 x 20.596,21
    assert _receitas(engine, "2026-09") == 2 * 2_059_621

def test_substituir_traduz_id_provisorio_do_proprio_lote(engine, conn):
    """A linha da planilha aposentada pelo extrato, dentro do mesmo arquivo.

    `duplicatas` sempre traduziu o id provisório negativo pelo id real gravado;
    `substituir` não traduzia, e o `UPDATE ... WHERE id IN (-1)` não acertava
    nada: as duas linhas ficavam ativas e o resumo do upload dizia que uma
    tinha sido conferida. A assimetria entre os dois é o que tornava isso fácil
    de acionar sem perceber.
    """
    conta = _conta(engine, "Conta Salário")

    resumo = _importar(engine, conta, [
        Lancamento(data=date(2026, 9, 5), descricao="SALARIO",
                   valor_centavos=2_059_621, origem="planilha"),
        Lancamento(data=date(2026, 9, 5), descricao="SALARIO",
                   valor_centavos=2_059_621, origem="extrato"),
    ])

    assert resumo["conferidos_planilha"] == 1
    assert _receitas(engine, "2026-09") == 2_059_621, (
        "o que o upload diz ter conferido tem de estar realmente desligado"
    )


# ===========================================================================
# 4. Planilha de receitas x extrato
# ===========================================================================


def test_planilha_e_extrato_no_mesmo_dia_conferem(engine, conn):
    """Controle do teste seguinte: com o dia exato, o pareamento funciona."""
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    corrente = _conta(engine, "Conta Salário")

    _importar(engine, planilha, [
        Lancamento(data=date(2026, 9, 5), descricao="SALARIO",
                   valor_centavos=2_059_621, origem="planilha"),
    ], origem="planilha", arquivo="planilha.xlsx")

    resumo = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_059_621),
    ])

    assert resumo["conferidos_planilha"] == 1
    assert _receitas(engine, "2026-09") == 2_059_621

def test_planilha_e_extrato_com_um_dia_de_diferenca_conferem(engine, conn):
    """O caso mais comum de todos, e o que mais escapava.

    A planilha anota o dia do contracheque; o extrato traz o dia em que o
    dinheiro caiu. Um dia de diferença. A regra planilha × extrato exigia o dia
    exato, e a regra de proximidade de três dias exige a mesma conta — mas a
    planilha mora numa conta só dela. A mesma receita contava duas vezes no vão
    entre as duas regras, sem que nada avisasse.
    """
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    corrente = _conta(engine, "Conta Salário")

    _importar(engine, planilha, [
        Lancamento(data=date(2026, 9, 5), descricao="SALARIO",
                   valor_centavos=2_059_621, origem="planilha"),
    ], origem="planilha", arquivo="planilha.xlsx")

    resumo = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 6), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_059_621),
    ])

    assert resumo["conferidos_planilha"] == 1
    assert _receitas(engine, "2026-09") == 2_059_621, (
        f"a mesma receita somou {_receitas(engine, '2026-09')} centavos"
    )


def test_lancamentos_distantes_demais_continuam_separados(engine, conn):
    """A janela é de três dias, não do mês: duas receitas iguais existem."""
    planilha = _conta(engine, "Planilha da casa", titular="Casal")
    corrente = _conta(engine, "Conta Salário")

    _importar(engine, planilha, [
        Lancamento(data=date(2026, 9, 5), descricao="AULA PARTICULAR",
                   valor_centavos=30_000, origem="planilha"),
    ], origem="planilha", arquivo="planilha.xlsx")

    resumo = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 20), descricao="PIX AULA PARTICULAR",
                   valor_centavos=30_000),
    ])

    assert resumo["conferidos_planilha"] == 0
    assert _receitas(engine, "2026-09") == 60_000


# ===========================================================================
# 5. Competência x data
# ===========================================================================

def test_credito_na_fatura_pareia_pela_competencia_nao_pela_data(engine, conn):
    """O pareamento olha a COMPETÊNCIA, igual ao relatório.

    Numa fatura de cartão a data e o mês em que a linha conta são coisas
    diferentes, e são sempre diferentes: a compra de 20/08 está na fatura de
    setembro. Casando por data, a previsão de AGOSTO morria para um lançamento
    que entrava como receita de SETEMBRO — agosto encolhia e setembro inchava,
    os dois pelo mesmo valor, sem uma linha a mais no banco. Era o desenho
    exato do badge que a casa viu.

    Hoje há duas travas em série: o cartão não realiza previsão nenhuma, e o
    pareamento, quando acontece, é por competência.
    """
    cartao = _conta(engine, "Cartão Nubank", tipo="cartao")
    id_agosto = _prever(engine, conn, "2026-08", 480_000, "ATENDIMENTOS", pessoa="Rô")
    conn.commit()

    resumo = _importar(engine, cartao, [
        Lancamento(data=date(2026, 8, 20), descricao="ESTORNO ANUIDADE",
                   valor_centavos=470_000, competencia="2026-09"),
    ], competencia="2026-09", arquivo="fatura.csv")

    assert resumo["previsoes_realizadas"] == 0
    assert _ativo(engine, id_agosto) is True
    assert _receitas(engine, "2026-08") == 480_000, "agosto não perde o que é dele"
    # o crédito de cartão é abatimento de despesa, não renda
    assert _receitas(engine, "2026-09") == 0


def test_previsao_so_casa_com_lancamento_da_mesma_competencia(engine, conn):
    """A regra, isolada do cartão: competência manda, data não."""
    previsao = {
        "id": 1, "origem": "manual", "ativo": True, "valor_centavos": 480_000,
        "data": date(2026, 8, 28), "competencia": "2026-08", "pessoa": "Rô",
        "descricao": "ATENDIMENTOS",
    }
    # mesma data de agosto, mas contando em setembro: não é a previsão de agosto
    assert dedup.previsao_equivalente(
        [previsao], dia=date(2026, 8, 20), valor_centavos=470_000,
        pessoa="Rô", competencia="2026-09",
    ) is None
    assert dedup.previsao_equivalente(
        [previsao], dia=date(2026, 8, 20), valor_centavos=470_000,
        pessoa="Rô", competencia="2026-08",
    ) is not None


# ===========================================================================
# 6. Desfazer o upload errado: o caminho que sobrou depois do commit aa24185
# ===========================================================================

def test_cartao_nunca_realiza_uma_receita_prevista(engine, conn):
    """A primeira das duas travas, e a que fecha a sequência inteira.

    A sequência que a casa viveu: a fatura entrou com as compras positivas, uma
    delas "realizou" a receita prevista de setembro e a desligou; o extrato do
    salário chegou depois e não encontrou com quem parear, porque o índice de
    duplicidade só lê linhas ativas — nem virou pergunta, porque
    `_previsoes_por_conferir` também filtra por ativo; e desfazer a fatura
    devolveu a previsão para o lado do salário real.

    Num cartão não existe receita. Nada que venha dele pode realizar previsão
    nenhuma, e a sequência não começa.
    """
    cartao = _conta(engine, "Cartão Nubank", tipo="cartao")
    corrente = _conta(engine, "Conta Salário")
    id_previsto = _prever(engine, conn, "2026-09", 2_059_621)
    conn.commit()

    fatura = _importar(engine, cartao, [
        Lancamento(data=date(2026, 9, 12), descricao="MOVEIS PLANEJADOS",
                   valor_centavos=1_900_000, competencia="2026-09"),
    ], competencia="2026-09", arquivo="fatura.csv")
    assert fatura["previsoes_realizadas"] == 0
    assert _ativo(engine, id_previsto) is True, "a previsão do mês continua de pé"

    # e o salário de verdade, chegando depois, encontra a previsão para parear
    extrato = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_059_621),
    ], arquivo="extrato.csv")
    assert extrato["previsoes_realizadas"] == 1
    assert _receitas(engine, "2026-09") == 2_059_621


def test_desfazer_nao_ressuscita_previsao_cujo_dinheiro_ja_voltou(engine, conn):
    """A segunda trava: o desfazer também tem de olhar antes de religar.

    Mesmo com o cartão fora do caminho, a ordem "upload A aposenta, upload B
    traz o dinheiro, desfaz A" continua possível — e religar às cegas soma os
    dois. Quem foi retido continua desligado e a tela diz por quê.
    """
    corrente = _conta(engine, "Conta Salário")
    outra = _conta(engine, "Conta Conjunta", titular="Casal")
    id_previsto = _prever(engine, conn, "2026-09", 2_059_621)
    conn.commit()

    primeiro = _importar(engine, corrente, [
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_059_621),
    ], arquivo="extrato-1.csv")
    assert primeiro["previsoes_realizadas"] == 1

    # o mesmo dinheiro entra de novo por outra conta, sem ver a previsão
    _importar(engine, outra, [
        Lancamento(data=date(2026, 9, 5), descricao="CREDITO PRO LABORE",
                   valor_centavos=2_059_621),
    ], arquivo="extrato-2.csv")

    apagadas, devolvidas, retidas = repo.apagar_upload(engine, primeiro["upload_id"])
    assert (apagadas, devolvidas, retidas) == (1, 0, 1)
    assert _ativo(engine, id_previsto) is False, (
        "o dinheiro já está no mês por outro arquivo: religar a previsão "
        "dobraria a renda"
    )
    assert _receitas(engine, "2026-09") == 2_059_621


# ===========================================================================
# 7. O badge "+107.2% vs mês anterior"
# ===========================================================================

def test_o_badge_nomeia_o_mes_com_que_compara(engine, conn):
    """"vs mês anterior" podia ser um mês qualquer atrás.

    `serie_mensal` só devolve competências com lançamento, e o dashboard pegava
    `serie[posicao - 1]` — o mês anterior *que tem dados*. Com um buraco na
    série, setembro era comparado com julho sob o rótulo "mês anterior". E há
    um buraco garantido neste ano: agosto tem só a previsão lançada à mão.

    Agora o mês é o do calendário e vem nomeado no rótulo, que é o que torna a
    comparação conferível a olho.
    """
    conta = _conta(engine, "Conta Salário")
    _importar(engine, conta, [
        Lancamento(data=date(2026, 7, 5), descricao="TED PRO LABORE TAG",
                   valor_centavos=2_000_000),
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=4_000_000),
    ])

    with engine.connect() as leitura:
        serie = analytics.serie_mensal(leitura, 2026)

    assert analytics.mes_anterior("2026-09") == "2026-08"
    assert analytics.mes_anterior("2026-01") == "2025-12"
    # agosto não está na série: sem mês anterior de verdade, não há comparação
    assert not [m for m in serie if m["competencia"] == "2026-08"]
    anterior = next(
        (m for m in serie if m["competencia"] == analytics.mes_anterior("2026-09")), None
    )
    assert anterior is None, (
        "com agosto vazio, o badge tem de calar a boca em vez de comparar com julho"
    )


def test_agosto_so_de_previsao_faz_setembro_parecer_o_dobro_sem_nada_dobrado(engine, conn):
    """HIPÓTESE SÉRIA, e ela se sustenta: o badge pode enganar sozinho.

    Agosto é o primeiro mês em que ele começou a lançar previsão à mão e não
    tem extrato nenhum; setembro já tem o extrato real. Mesmo com a previsão
    de setembro corretamente aposentada — nada dobrado, nenhuma linha a mais —
    o badge mostra um salto de +107% porque está comparando um mês completo
    com um mês que só tem previsão.

    Este teste PASSA: ele mostra que o badge sozinho NÃO prova duplicação. O
    que decide entre as duas leituras é a composição de setembro (a consulta
    SQL do relatório), não o badge.
    """
    conta = _conta(engine, "Conta Salário")
    # agosto: só a previsão à mão, R$ 50.144,12
    _prever(engine, conn, "2026-08", 5_014_412, "PREVISAO AGOSTO")
    # setembro: previsão do mesmo tamanho, que o extrato vem realizar
    _prever(engine, conn, "2026-09", 5_014_412, "PREVISAO SETEMBRO")
    conn.commit()

    resumo = _importar(engine, conta, [
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=5_200_000),
        Lancamento(data=date(2026, 9, 8), descricao="PIX RECEBIDO ALUGUEL NUN",
                   valor_centavos=5_189_862),
    ])
    assert resumo["previsoes_realizadas"] == 1      # a de setembro saiu de cena

    with engine.connect() as leitura:
        serie = analytics.serie_mensal(leitura, 2026)

    # o mesmo caminho do dashboard, agora pelo mês do calendário
    passado = analytics.mes_anterior("2026-09")
    anterior = next(m for m in serie if m["competencia"] == passado)
    atual = next(m for m in serie if m["competencia"] == "2026-09")
    delta = (atual["receitas"] / anterior["receitas"] - 1) * 100

    assert passado == "2026-08"
    # os números exatos do print, sem nenhuma linha duplicada: setembro fica em
    # R$ 103.898,62 contra R$ 50.144,12 de agosto, e o badge escreve +107.2%
    assert f"{delta:+.1f}%" == "+107.2%"
    assert _receitas(engine, "2026-09") == 10_389_862      # R$ 103.898,62
    assert _receitas(engine, "2026-08") == 5_014_412       # R$ 50.144,12


# ===========================================================================
# 8. O acumulado do ano: a régua que o dono não usou
# ===========================================================================


def test_acumulado_do_ano_usa_a_mesma_agregacao_do_mes(engine, conn):
    """Controle: `resumo(ano=...)` é a soma dos meses, sem atalho.

    PASSA. Serve para dizer, no diagnóstico, que os R$ 1.958.830,82 do ano são
    a soma fiel do que está na base — se há duplicação, ela está nos dados de
    cada mês, não numa conta do acumulado.
    """
    conta = _conta(engine, "Conta Salário")
    _importar(engine, conta, [
        Lancamento(data=date(2026, 7, 5), descricao="TED PRO LABORE TAG",
                   valor_centavos=2_000_000),
        Lancamento(data=date(2026, 8, 5), descricao="TED PRO LABORE TAG",
                   valor_centavos=2_100_000),
        Lancamento(data=date(2026, 9, 5), descricao="TED PRO LABORE TAG LTDA",
                   valor_centavos=2_200_000),
    ])

    with engine.connect() as leitura:
        ano = analytics.resumo(leitura, ano=2026)
        serie = analytics.serie_mensal(leitura, 2026)

    assert ano["receitas"] == sum(m["receitas"] for m in serie)
    assert ano["receitas"] == 6_300_000
