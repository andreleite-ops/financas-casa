"""Leitura dos extratos e faturas de verdade, um teste por instituição.

As amostras em `tests/amostras/` vieram dos arquivos que o André e a Rô usam,
com os dados já fictícios na origem e ainda assim reduzidas e anonimizadas,
porque este repositório é público. Saíram agência, conta, nome completo, os
nomes de quem recebeu ou mandou PIX e os números de instalação das contas de
luz, água, gás e telefone — esses últimos não são "só um número": identificam
o endereço da casa tão bem quanto o endereço escrito. O extrato do Itaú entrou
como texto, e não como PDF, pelo mesmo motivo: o PDF original traz o endereço
residencial na primeira página.

Cada teste confere contra um número que o próprio arquivo declara. É o que
diferencia "o leitor rodou" de "o leitor leu certo":

- Bradesco: entradas − saídas tem de reproduzir a variação do saldo
- Itaú: o extrato imprime o total de entradas e de saídas do mês
- XP: a fatura traz a linha de total
- Nubank: as compras somam a fatura, e o pagamento vem separado
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.money import fmt_brl, para_centavos
from parsers import extrato_itau, instituicoes

AMOSTRAS = Path(__file__).parent / "amostras"


def _ler(arquivo: str, parser: str, tipo_conta: str, competencia: str):
    caminho = AMOSTRAS / arquivo
    if not caminho.exists():                     # pragma: no cover
        pytest.skip(f"amostra {arquivo} não está no repositório")
    return instituicoes.ler_arquivo(
        parser, caminho.read_bytes(), arquivo,
        competencia=competencia, tipo_conta=tipo_conta,
    )


def _somas(lancamentos) -> tuple[int, int]:
    entradas = sum(l.valor_centavos for l in lancamentos if l.valor_centavos > 0)
    saidas = -sum(l.valor_centavos for l in lancamentos if l.valor_centavos < 0)
    return entradas, saidas


# ---------------------------------------------------------------------------
# Bradesco — CSV com colunas separadas de crédito e débito
# ---------------------------------------------------------------------------
def test_bradesco_fecha_com_a_variacao_do_saldo():
    """A prova de que nada ficou de fora nem entrou duas vezes."""
    lancamentos = _ler("bradesco_extrato.csv", "bradesco", "corrente", "2026-08")
    entradas, saidas = _somas(lancamentos)

    # o extrato abre em 13.313,38 (03/08) e fecha em 11.616,98 (12/08)
    variacao = para_centavos("11.616,98") - para_centavos("13.313,38")
    assert entradas - saidas == variacao, (
        f"lido {fmt_brl(entradas - saidas)}, saldo variou {fmt_brl(variacao)}"
    )
    assert len(lancamentos) == 24


def test_bradesco_le_o_bloco_de_ultimos_lancamentos_no_rodape():
    """O arquivo tem duas tabelas empilhadas, com cabeçalho repetido no meio.

    Parar na primeira perderia os lançamentos mais recentes — justamente os que
    ainda não estão em lugar nenhum.
    """
    lancamentos = _ler("bradesco_extrato.csv", "bradesco", "corrente", "2026-08")
    de_doze = [l for l in lancamentos if l.data.day == 12]
    assert len(de_doze) == 2
    assert -sum(l.valor_centavos for l in de_doze) == para_centavos("184,09")


def test_bradesco_nao_confunde_saldo_com_valor():
    """A coluna de saldo fica ao lado da de valor e é maior — trocá-las passaria
    despercebido na contagem de linhas e destruiria os totais."""
    lancamentos = _ler("bradesco_extrato.csv", "bradesco", "corrente", "2026-08")
    primeiro = min(lancamentos, key=lambda l: (l.data, l.descricao))
    assert primeiro.descricao == "IOF S/ UTILIZACAO LIMITE"
    assert primeiro.valor_centavos == -para_centavos("91,57")   # e não o saldo 13.221,81


def test_bradesco_ignora_o_rodape_de_texto_e_a_linha_torta():
    """O arquivo termina com filtro, aviso e uma linha de total com uma coluna
    a mais — que antes abortava a leitura inteira."""
    lancamentos = _ler("bradesco_extrato.csv", "bradesco", "corrente", "2026-08")
    descricoes = {l.descricao for l in lancamentos}
    assert not any("Filtro de resultados" in d for d in descricoes)
    assert not any("COD. LANC." in d for d in descricoes)   # linha de saldo, sem valor


# ---------------------------------------------------------------------------
# Itaú — PDF em texto corrido, data que não se repete, sinal no fim do número
# ---------------------------------------------------------------------------
def _texto_itau() -> str:
    caminho = AMOSTRAS / "itau_extrato.txt"
    if not caminho.exists():                     # pragma: no cover
        pytest.skip("amostra do Itaú não está no repositório")
    return caminho.read_text(encoding="utf-8")


def test_itau_bate_com_o_total_que_o_proprio_extrato_declara():
    texto = _texto_itau()
    lancamentos, _ = extrato_itau.extrair_linhas(texto, competencia="2026-07")
    conferencia = extrato_itau.conferir(texto, lancamentos)

    assert conferencia["confere"], (
        f"entradas {fmt_brl(conferencia['entradas'])} vs "
        f"{fmt_brl(conferencia['entradas_declaradas'])}, "
        f"saídas {fmt_brl(conferencia['saidas'])} vs "
        f"{fmt_brl(conferencia['saidas_declaradas'])}"
    )


def test_itau_herda_a_data_da_linha_anterior():
    """Só a primeira linha do dia traz dd/mm; as outras herdam.

    Em 06/07 são seis lançamentos e apenas o primeiro tem data escrita — a
    herança atravessa inclusive a quebra de página, onde o cabeçalho do PDF se
    intromete no meio da sequência.
    """
    lancamentos, _ = extrato_itau.extrair_linhas(_texto_itau(), competencia="2026-07")
    de_seis = [l for l in lancamentos if l.data.day == 6]
    assert len(de_seis) == 6
    assert {l.data.month for l in de_seis} == {7}
    # o primeiro é o único que trazia a data escrita na linha
    assert de_seis[0].descricao == "PIX TRANSF FULANA06/07"


def test_itau_le_o_traco_no_fim_como_debito():
    lancamentos, _ = extrato_itau.extrair_linhas(_texto_itau(), competencia="2026-07")
    # a mesma descrição aparece em dias diferentes: comparar por data e valor
    eletropaulo = sorted(
        (l.data.day, l.valor_centavos)
        for l in lancamentos if l.descricao == "DA ELETROPAULO 10000001"
    )
    assert eletropaulo == [(1, -para_centavos("171,72")), (31, -para_centavos("43,66"))]
    entrada = next(l for l in lancamentos if l.descricao == "PIX TRANSF SICRANA06/07")
    assert entrada.valor_centavos == para_centavos("500,00")


def test_itau_ignora_o_saldo_corrido_na_mesma_linha():
    """"DA PMSP 300000000000001 1.006,86- 853,60-" tem valor e, depois, saldo."""
    lancamentos, _ = extrato_itau.extrair_linhas(_texto_itau(), competencia="2026-07")
    pmsp = [l for l in lancamentos if l.descricao == "DA PMSP 300000000000001"]
    assert len(pmsp) == 1
    assert pmsp[0].valor_centavos == -para_centavos("1.006,86")


def test_itau_descarta_a_aplicacao_automatica_mas_mantem_o_rendimento():
    """A varredura para o CDB não é gasto nem receita — o próprio extrato diz
    que ela fica fora do resumo. Já o rendimento pago é crédito de verdade."""
    lancamentos, _ = extrato_itau.extrair_linhas(_texto_itau(), competencia="2026-07")
    descricoes = [l.descricao for l in lancamentos]
    assert not any("Res Aplic Aut" in d or "SALDO APLIC" in d for d in descricoes)
    rendimento = [l for l in lancamentos if "Rend Pago" in l.descricao]
    assert len(rendimento) == 1
    assert rendimento[0].valor_centavos == para_centavos("0,01")


def test_itau_recusa_um_pdf_que_nao_e_extrato():
    with pytest.raises(Exception):
        extrato_itau.extrair_linhas("qualquer texto sem movimentação")


def test_itau_pega_o_valor_do_fim_e_nao_o_numero_da_descricao():
    """Descrição com decimal dentro roubava o valor — e o sinal junto.

    "COMPRA 12,50 UNID" seguido do valor de verdade fazia o leitor lançar
    12,50 como crédito, porque o traço de débito estava no outro número.
    """
    texto = (
        "29/06 Saldo anterior 100,00\n"
        "01/07 COMPRA 12,50 UNID PADARIA 340,00-\n"
        "Saldo em C/C 240,00-\n"
    )
    lancamentos, _ = extrato_itau.extrair_linhas(texto, competencia="2026-07")
    assert len(lancamentos) == 1
    assert lancamentos[0].valor_centavos == -para_centavos("340,00")
    assert lancamentos[0].descricao == "COMPRA 12,50 UNID PADARIA"


def test_itau_nao_conta_o_quadro_de_resumo_como_movimentacao():
    """"saldo anterior" aparece duas vezes: no resumo e na movimentação.

    Abrindo a janela na primeira, as linhas do resumo — que repetem o mês
    inteiro — entram como lançamento e o mês é contado duas vezes.
    """
    texto = (
        "01. Conta Corrente (saldo anterior em 29/06/26)\n"
        "Transferências, DOCs e TEDs 34% 1.620,00\n"
        "total 2.900,01\n"
        "Conta Corrente|Movimentação\n"
        "29/06 Saldo anterior 491,76\n"
        "01/07 DA ELETROPAULO 10000001 171,72-\n"
        "Saldo em C/C 663,48-\n"
    )
    lancamentos, _ = extrato_itau.extrair_linhas(texto, competencia="2026-07")
    assert [l.descricao for l in lancamentos] == ["DA ELETROPAULO 10000001"]


def test_itau_nao_para_no_saldo_do_pe_de_cada_pagina():
    """Extrato de várias páginas imprime "Saldo em C/C" em todas elas.

    Parando na primeira, metade do mês sumia — sem erro nenhum na tela.
    """
    texto = (
        "29/06 Saldo anterior 100,00\n"
        "01/07 DA LUZ 10000001 100,00-\n"
        "Saldo em C/C 0,00\n"
        "extrato mensal jul 2026 002|002\n"
        "20/07 DA GAS 40000001 50,00-\n"
        "Saldo final 50,00-\n"
    )
    lancamentos, _ = extrato_itau.extrair_linhas(texto, competencia="2026-07")
    assert [l.data.day for l in lancamentos] == [1, 20]


def test_itau_acha_a_data_empurrada_pela_legenda_da_segunda_coluna():
    """A legenda em minúsculas se mistura à linha e empurra o dd/mm.

    Sem reconhecer isso, a linha herdava a data anterior — na virada do mês,
    ia parar no mês errado. E o inverso tem de continuar valendo: um dd/mm no
    meio de uma descrição em maiúsculas não é a data do lançamento.
    """
    texto = (
        "29/06 Saldo anterior 100,00\n"
        "01/07 DA LUZ 10000001 10,00-\n"
        "P = poupança automática 05/08 DA AGUA 40000001 20,00-\n"
        "PIX TRANSF FULANO 16/07 30,00-\n"
        "Saldo em C/C 60,00-\n"
    )
    lancamentos, _ = extrato_itau.extrair_linhas(texto, competencia="2026-07")
    por_descricao = {l.descricao: l.data for l in lancamentos}
    assert por_descricao["DA AGUA 40000001"].month == 8
    assert por_descricao["DA AGUA 40000001"].day == 5
    # a data dentro da descrição em maiúsculas não manda: a linha é do dia 5
    assert por_descricao["PIX TRANSF FULANO 16/07"].day == 5


# ---------------------------------------------------------------------------
# XP — fatura de cartão com coluna de portador
# ---------------------------------------------------------------------------
def test_xp_soma_o_total_impresso_na_fatura():
    lancamentos = _ler("xp_fatura.xlsx", "xp", "cartao", "2026-05")
    entradas, saidas = _somas(lancamentos)
    assert saidas - entradas == para_centavos("6.542,05")


def test_xp_compra_e_despesa_nao_receita():
    """A fatura exporta tudo positivo. Sem inverter, um mês de compras entrava
    como receita — foi o que aconteceu na primeira leitura."""
    lancamentos = _ler("xp_fatura.xlsx", "xp", "cartao", "2026-05")
    assert all(l.valor_centavos < 0 for l in lancamentos)


def test_xp_o_portador_diz_de_quem_e_a_compra():
    """Num cartão com adicional, a pessoa muda linha a linha."""
    from core.repo import _pessoa_valida

    lancamentos = _ler("xp_fatura.xlsx", "xp", "cartao", "2026-05")
    donos = {_pessoa_valida(l.pessoa_hint, "Casal") for l in lancamentos}
    assert donos == {"André", "Rô"}
    hortifruti = next(l for l in lancamentos if "HORTIFRUTI" in l.descricao)
    assert _pessoa_valida(hortifruti.pessoa_hint, "Casal") == "André"


def test_xp_ignora_a_linha_de_total_sem_data():
    """Ela vale metade da fatura: lê-la dobraria o mês."""
    lancamentos = _ler("xp_fatura.xlsx", "xp", "cartao", "2026-05")
    assert len(lancamentos) == 10


# ---------------------------------------------------------------------------
# Nubank — fatura com valor positivo para compra
# ---------------------------------------------------------------------------
def test_nubank_compra_positiva_vira_despesa_e_pagamento_vira_credito():
    lancamentos = _ler("nubank_fatura.xlsx", "nubank", "cartao", "2026-03")
    por_descricao = {l.descricao: l.valor_centavos for l in lancamentos}

    assert por_descricao["Dm*Spotify"] == -para_centavos("40,90")
    pagamento = next(v for k, v in por_descricao.items() if "Pagamento recebido" in k)
    assert pagamento > 0
    estorno = next(v for k, v in por_descricao.items() if "Estorno" in k)
    assert estorno > 0


def test_nubank_reconhece_o_layout_pelas_colunas_e_nao_pelo_nome_do_arquivo():
    """O arquivo chega com qualquer nome; o que identifica é date/title/amount."""
    caminho = AMOSTRAS / "nubank_fatura.xlsx"
    if not caminho.exists():                     # pragma: no cover
        pytest.skip("amostra do Nubank não está no repositório")
    lancamentos = instituicoes.ler_arquivo(
        "nubank", caminho.read_bytes(), "qualquer_nome_2026.xlsx",
        competencia="2026-03", tipo_conta="cartao",
    )
    assert lancamentos
    assert all(l.descricao for l in lancamentos)


# ---------------------------------------------------------------------------
# armadilhas que o leitor precisa continuar evitando
# ---------------------------------------------------------------------------
def test_coluna_tipo_que_nao_e_debito_credito_nao_desliga_a_inversao():
    """"Tipo" na fatura costuma ser "à vista"/"parcelado", não D/C.

    A inversão da fatura era desligada só por existir uma coluna chamada
    "Tipo" — e aí o mês inteiro de compras entrava como receita.
    """
    csv = (
        "Data;Estabelecimento;Tipo;Valor\n"
        "01/04/2026;PADARIA;à vista;90,30\n"
        "02/04/2026;FARMACIA;parcelado;120,00\n"
    ).encode("utf-8")
    lancamentos = instituicoes.ler_arquivo(
        "xp", csv, "fatura.csv", competencia="2026-04", tipo_conta="cartao"
    )
    assert len(lancamentos) == 2
    assert all(l.valor_centavos < 0 for l in lancamentos)


def test_coluna_tipo_com_debito_credito_de_verdade_continua_mandando():
    """A outra metade: quando a coluna diz mesmo D/C, quem manda é o arquivo."""
    csv = (
        "Data;Historico;Tipo;Valor\n"
        "01/04/2026;COMPRA;D;90,30\n"
        "02/04/2026;ESTORNO;C;120,00\n"
    ).encode("utf-8")
    lancamentos = instituicoes.ler_arquivo(
        "xp", csv, "fatura.csv", competencia="2026-04", tipo_conta="cartao"
    )
    por_descricao = {l.descricao: l.valor_centavos for l in lancamentos}
    assert por_descricao["COMPRA"] < 0
    assert por_descricao["ESTORNO"] > 0


def test_extrato_em_pdf_nao_transforma_credito_em_despesa():
    """Extrato de conta tem os dois sentidos; fatura, um só.

    O leitor genérico recebia essa diferença e não a usava: num extrato, o
    salário recebido entrava como gasto.
    """
    from parsers import pdf as leitor_pdf

    texto = (
        "05/09/2026 PIX RECEBIDO SALARIO 12.000,00\n"
        "06/09/2026 SUPERMERCADO 350,00\n"
        "07/09/2026 TARIFA PACOTE 29,90-\n"
    )
    lancamentos, _ = leitor_pdf.extrair_linhas(texto, competencia="2026-09", tudo_despesa=False)
    por_descricao = {l.descricao: l.valor_centavos for l in lancamentos}
    assert por_descricao["PIX RECEBIDO SALARIO"] == para_centavos("12.000,00")
    assert por_descricao["SUPERMERCADO"] == -para_centavos("350,00")
    assert por_descricao["TARIFA PACOTE"] == -para_centavos("29,90")


def test_fatura_em_pdf_continua_lendo_tudo_como_gasto():
    from parsers import pdf as leitor_pdf

    texto = (
        "05/09 RESTAURANTE 120,00\n"
        "06/09 PAGAMENTO RECEBIDO 3.000,00\n"
    )
    lancamentos, _ = leitor_pdf.extrair_linhas(texto, competencia="2026-09", tudo_despesa=True)
    por_descricao = {l.descricao: l.valor_centavos for l in lancamentos}
    assert por_descricao["RESTAURANTE"] < 0
    assert por_descricao["PAGAMENTO RECEBIDO"] > 0


def test_linha_de_csv_com_separador_na_descricao_vira_aviso_e_nao_silencio():
    """Perder um lançamento calado é pior que recusar o arquivo.

    A descrição com ponto e vírgula dentro sobra de colunas. A linha não entra
    (não dá para saber onde termina a descrição), mas fica registrada.
    """
    from parsers import tabular

    csv = (
        "Data;Historico;Valor\n"
        "01/04/2026;PADARIA;90,30\n"
        "02/04/2026;PIX ENVIADO; REF 12;120,00\n"
    ).encode("utf-8")
    df = tabular.carregar_tabela(csv, "extrato.csv")
    lancamentos, avisos = tabular.extrair(
        df, tabular.sugerir_mapeamento(df.columns, df), competencia="2026-04"
    )
    assert len(lancamentos) == 1
    assert any("não importada" in a for a in avisos)


def test_rodape_com_coluna_a_mais_continua_passando_sem_aviso():
    """A outra metade: campo sobrando e vazio é rodapé, e some sem barulho."""
    from parsers import tabular

    csv = (
        "Data;Historico;Valor\n"
        "01/04/2026;PADARIA;90,30\n"
        ";;;\n"
    ).encode("utf-8")
    df = tabular.carregar_tabela(csv, "extrato.csv")
    assert df.attrs.get("linhas_descartadas") == []


def test_parcela_antiga_na_fatura_de_janeiro_nao_vai_para_o_futuro():
    """Fatura de janeiro carrega parcela comprada em qualquer mês do ano
    passado — e não só em dezembro, que era o único caso tratado."""
    from datetime import date as _date

    from parsers.base import Lancamento, ajustar_ano_fatura

    lancamentos = ajustar_ano_fatura(
        [
            Lancamento(_date(2026, 11, 5), "COMPRA PARCELADA 3 de 6", -10_000),
            Lancamento(_date(2026, 12, 20), "COMPRA DE DEZEMBRO", -20_000),
            Lancamento(_date(2026, 1, 3), "COMPRA DO MES", -30_000),
        ],
        "2026-01",
    )
    assert [l.data.year for l in lancamentos] == [2025, 2025, 2026]
    assert [l.data.month for l in lancamentos] == [11, 12, 1]


def test_compra_depois_do_fechamento_continua_no_mes_seguinte():
    """Um mês de folga tem de ficar de pé: é a compra feita depois do
    fechamento, que cai na fatura seguinte."""
    from datetime import date as _date

    from parsers.base import Lancamento, ajustar_ano_fatura

    lancamentos = ajustar_ano_fatura(
        [Lancamento(_date(2026, 6, 2), "COMPRA APOS FECHAMENTO", -10_000)], "2026-05"
    )
    assert lancamentos[0].data == _date(2026, 6, 2)


def test_nome_parecido_de_terceiro_nao_vira_dono_do_gasto():
    """Comparar só o começo do nome dava o gasto de estranhos à casa:
    "ROBERTO" e "RODRIGO" viravam Rô, "ANDREA" virava André."""
    from core.repo import _pessoa_valida

    assert _pessoa_valida("ROBERTO", "Casal") == "Casal"
    assert _pessoa_valida("RODRIGO SILVA", "Casal") == "Casal"
    assert _pessoa_valida("ANDREA", "Casal") == "Casal"
    assert _pessoa_valida("ANDRE TITULAR", "Casal") == "André"
    assert _pessoa_valida("RO TITULAR", "Casal") == "Rô"
    assert _pessoa_valida("", "Casal") == "Casal"


def test_nome_completo_do_portador_vem_do_segredo_e_nao_do_codigo(monkeypatch):
    """O repositório é público: nome completo de ninguém entra no código.

    A fatura imprime o portador por extenso, então o de-para mora no segredo
    APELIDOS_PESSOA, que só existe na instalação da casa.
    """
    from core.repo import _pessoa_valida

    monkeypatch.setenv("APELIDOS_PESSOA", "fulana de tal souza=Rô;beltrano prado=André")
    assert _pessoa_valida("FULANA DE TAL SOUZA", "Casal") == "Rô"
    assert _pessoa_valida("BELTRANO PRADO", "Casal") == "André"
    assert _pessoa_valida("SICRANO QUALQUER", "Casal") == "Casal"


# ---------------------------------------------------------------------------
# cartão e conta corrente convivendo: o pagamento da fatura não é gasto
# ---------------------------------------------------------------------------
def test_pagar_a_fatura_nao_dobra_a_despesa_do_mes(engine):
    """O cenário que começa quando cartão e conta corrente entram juntos.

    As compras do mês estão na fatura. O pagamento dela aparece duas vezes: como
    crédito na própria fatura ("Pagamento recebido") e como débito na conta
    corrente. Somando os dois sem cuidado, a despesa do mês dobra e nasce uma
    receita que nunca existiu.
    """
    from datetime import date as _date

    from core import analytics, repo
    from parsers.base import Lancamento

    with engine.connect() as conn:
        cartao = next(c for c in repo.listar_contas(conn) if c["tipo"] == "cartao")
        conta = next(c for c in repo.listar_contas(conn) if c["tipo"] == "corrente")

    # a fatura: duas compras e o pagamento recebido
    repo.importar(
        engine, conta_id=cartao["id"], arquivo="fatura.csv", origem="extrato",
        usuario="André", usar_ia=False,
        lancamentos=[
            Lancamento(_date(2026, 9, 5), "SUPERMERCADO PAO DE ACUCAR", -30_000),
            Lancamento(_date(2026, 9, 8), "DROGARIA SAO PAULO", -20_000),
            Lancamento(_date(2026, 9, 10), "Pagamento recebido", 50_000),
        ],
    )
    # a conta corrente: o pagamento da mesma fatura
    repo.importar(
        engine, conta_id=conta["id"], arquivo="extrato.csv", origem="extrato",
        usuario="André", usar_ia=False,
        lancamentos=[Lancamento(_date(2026, 9, 10), "PAGAMENTO DE FATURA CARTAO", -50_000)],
    )

    with engine.connect() as conn:
        resumo = analytics.resumo(conn, competencia="2026-09")

    # o gasto do mês são as compras, e só elas
    assert resumo["despesas"] == 50_000
    assert resumo["receitas"] == 0
    # o dinheiro que só mudou de bolso continua visível, fora dos dois totais
    assert resumo["transferencias"] == 0      # os dois lados se anulam
