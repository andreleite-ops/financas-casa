"""O extrato do Bradesco em PDF — três linhas por movimento, sinal pelo saldo.

Texto sintético com a forma exata do PDF: agência, conta, nomes e valores são
inventados, porque o repositório é público. A régua é o quadro de totais que
o próprio extrato imprime.
"""

from __future__ import annotations

from parsers import extrato_bradesco as br

# saldo anterior 1.000,00; créditos 12.000,00 + 500,00 = 12.500,00;
# débitos 300,00 + 91,57 + 2.500,00 + 85,00 + 7.000,00 = 9.976,57;
# saldo final 1.000,00 + 12.500,00 − 9.976,57 = 3.523,43
EXTRATO = """Bradesco Celular
Data: 17/09/2026 - 18h51
Nome: FULANO DE TAL
Extrato de: Agência: 1234 | Conta: 56789-0 | Movimentação entre: 01/08/2026 e 31/08/2026 Folha: 1/2
Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)
31/07/2026 COD. LANC. 0 0,00 1.000,00
PIX RECEBIDO
03/08/2026 1340478 12.000,00 13.000,00
REM: EMPRESA LTDA. 02/08
PIX ENVIADO
0803120 300,00 12.700,00
DES: BELTRANO 01/08
04/08/2026 IOF S/ UTILIZACAO LIMITE 0844193 91,57 12.608,43
PAGTO ELETRON COBRANCA
05/08/2026 0000945 2.500,00 10.108,43
BANCO XP S.A
TARIFA BANCARIA
0030826 85,00 10.023,43
CESTA ILIMITADA
Bradesco Celular
Data: 17/09/2026 - 18h51
Nome: FULANO DE TAL
Extrato de: Agência: 1234 | Conta: 56789-0 | Movimentação entre: 01/08/2026 e 31/08/2026 Folha: 2/2
Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)
PIX RECEBIDO
17/08/2026 1515302 500,00 10.523,43
REM: Fulano De Tal 15/08
PAGTO ELETRON COBRANCA
21/08/2026 0000947 7.000,00 3.523,43
NU PAGAMENTOS SA
Total 12.500,00 9.976,57 3.523,43
Bradesco Celular
Data: 17/09/2026 - 18h51
Nome: FULANO DE TAL
Extrato de: Agência: 1234 | Conta: 56789-0 | Últimos Lancamentos Folha: 2/2
Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)
15/09/2026 COD. LANC. 0 32.133,71
PAGTO ELETRON COBRANCA
16/09/2026 0000956 655,17 31.478,54
FUTURO SET.26
Total 655,17 31.478,54
"""


def _lidos():
    return br.extrair_linhas(EXTRATO, competencia="2026-08")


def test_fecha_com_o_total_que_o_extrato_imprime():
    lancamentos, ignoradas = _lidos()
    c = br.conferir(EXTRATO, lancamentos)
    assert (c["entradas"], c["saidas"]) == (1_250_000, 997_657)
    assert c["confere"] is True
    assert ignoradas == []


def test_o_sinal_vem_do_texto():
    lancamentos, _ = _lidos()
    por = {l.descricao: l.valor_centavos for l in lancamentos}
    assert por["PIX RECEBIDO REM: EMPRESA LTDA."] == 1_200_000
    assert por["PIX ENVIADO DES: BELTRANO"] == -30_000
    assert por["IOF S/ UTILIZACAO LIMITE"] == -9_157
    assert por["PAGTO ELETRON COBRANCA BANCO XP S.A"] == -250_000
    assert por["TARIFA BANCARIA CESTA ILIMITADA"] == -8_500


def test_a_descricao_e_o_historico_mais_a_contraparte_sem_o_documento():
    """Era o número do documento que virava descrição — e com "1340478" como
    descrição nenhuma regra reconhece nada. A contraparte é onde mora o emissor
    do cartão ("BANCO XP S.A", "NU PAGAMENTOS SA")."""
    lancamentos, _ = _lidos()
    assert not any(l.descricao.strip().isdigit() for l in lancamentos)
    assert any(l.descricao == "PAGTO ELETRON COBRANCA NU PAGAMENTOS SA" for l in lancamentos)
    assert all(l.extra.get("documento") for l in lancamentos)


def test_a_data_so_vem_na_primeira_linha_do_dia_e_atravessa_a_pagina():
    lancamentos, _ = _lidos()
    assert [l.data.day for l in lancamentos] == [3, 3, 4, 5, 5, 17, 21]
    assert all(l.data.month == 8 for l in lancamentos)


def test_ultimos_lancamentos_ficam_de_fora():
    """A página final é de outro período: era ela que fazia o mapa dizer que
    setembro estava carregado com o extrato de agosto."""
    lancamentos, _ = _lidos()
    assert not any(l.data.month == 9 for l in lancamentos)
    assert not any("FUTURO" in l.descricao for l in lancamentos)


def test_identificacao_pelo_cabecalho():
    assert br.identificacao(EXTRATO) == {"agencia": "1234", "conta": "56789-0", "competencia": "2026-08"}


def test_sem_saldo_anterior_o_texto_decide_o_sinal():
    texto = """Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)
PIX RECEBIDO
03/08/2026 1340478 12.000,00 13.000,00
REM: EMPRESA LTDA. 02/08
PIX ENVIADO
0803120 300,00 12.700,00
DES: BELTRANO 01/08
"""
    lancamentos, _ = br.extrair_linhas(texto, competencia="2026-08")
    assert [l.valor_centavos for l in lancamentos] == [1_200_000, -30_000]


def test_saldo_fora_de_ordem_nao_troca_o_sinal():
    """No PDF real, um PIX enviado aparece com o saldo subindo: a coluna de
    saldo não vem em ordem dentro do dia. Confiar nela trocou o sinal de cinco
    linhas. O texto manda; o total impresso é quem prova."""
    texto = """Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)
31/07/2026 COD. LANC. 0 0,00 1.144,75
PIX ENVIADO
05/08/2026 0616032 7.900,00 9.044,75
DES: FILHO UM 05/08
PIX ENVIADO
0644418 7.900,00 16.944,75
DES: FILHA DOIS 05/08
TED-TRANSF ELET DISPON
21/08/2026 8865507 34.000,00 33.387,41
REMET.FULANO DE TAL
Total 34.000,00 15.800,00 33.387,41
"""
    lancamentos, _ = br.extrair_linhas(texto, competencia="2026-08")
    assert [l.valor_centavos for l in lancamentos] == [-790_000, -790_000, 3_400_000]
    assert br.conferir(texto, lancamentos)["confere"] is True
