"""O extrato mensal do Itaú, com as armadilhas de verdade — em texto sintético.

Nenhum nome, número de conta ou valor aqui é real: o repositório é público. O
que é real é a *forma*: as linhas exatamente como o PDF as entrega, com a
coluna de legendas misturada à movimentação, o nome do pagador como ele
escreveu (com minúscula), a data colada no fim do nome, a aplicação
automática entrando e saindo todo dia, e o mesmo cabeçalho repetido a cada
página.

A régua de "leu perfeito" não é opinião: é o total de entradas e saídas que o
próprio extrato imprime, e o saldo antes e depois.
"""

from __future__ import annotations

import sqlalchemy as sa

from core import db, repo
from parsers import extrato_itau as it

# um extrato de conta corrente como o PDF chega, depois de extraído o texto.
# Entradas: 300 + 840 + 225 + 25 + 900 + 0,11 = 2.290,11. Saídas: 4,21 +
# 600 + 55,90 + 12,70 = 672,81. Saldo: 1.000,00 + 2.290,11 − 672,81 = 2.617,30.
EXTRATO = """extrato mensal ag 1234 cc 56789-0 ago 2026 001|002
FULANA DE TAL
RUA EXEMPLO 1
ago 2026 Minha conta56789-0Minha agência1234 - Sp Exemplo
saldo em 31/07/26 saldo em 31/08/26
01. Conta Corrente e Aplicações Automáticas
R$ 1.000,00 R$ 2.617,30
entradas (créditos)
Transferências, DOCs e TEDs 100% 2.290,00
Outras entradas 0% 0,11
total 2.290,11
saídas (débitos)
Débitos automáticos efetuados 8% 55,90
Outras saídas 92% 616,91
total 672,81
totalentradas totalsaídas
(créditos) (débitos)
R$ 2.290,11 R$ 672,81
Conta Corrente|Movimentação
A =agendamento data descrição entradas R$ saídas R$ saldo R$
B = ações movimentadas (créditos) (débitos)
pelaBolsa de Valores 31/07 Saldo anterior 1.000,00
C = crédito a compensar
D = débito a compensar
03/08 PIX TRANSF PACIENTE UM03/08 300,00
G = aplicação programada
P = poupança automática PIX TRANSF PACIENTE D 03/08 840,00
Para demais siglas, consulte as Notas 04/08 IOF 4,21-
Explicativas nofinal doextrato
Apl Aplic Aut Mais 2.000,00- 1,00
SALDO APLIC AUT MAIS 2.000,00
05/08 PIX TRANSF CLINICA05/08 600,00-
Res Aplic Aut Mais 500,00
Rend Pago Aplic Aut Mais 0,11 1,00
SALDO APLIC AUT MAIS 1.500,00
Este material está disponível na Internet > Menu Conta Corrente > Extrato Mensal 000000 B001A 03/09/2026
extrato mensal ag 1234 cc 56789-0 ago 2026 002|002
data descrição entradas R$ saídas R$ saldo R$
(créditos) (débitos)
10/08 PIX TRANSF Paciente Do10/08 225,00
PIX TRANSF Paciente Do10/08 25,00
Déb Autor ASS. JORNAL 55,90-
22/08 PIX TRANSF Renata 22/08 900,00
TAR PACOTE ITAU JUL/26 12,70- 1,00
Saldo em C/C 1,00
Saldo final 1,00
Conta Corrente | Débitos automáticos efetuados
data histórico valor R$
31/07/26 DA LUZ 12345 43,66
"""


def _lidos():
    return it.extrair_linhas(EXTRATO, competencia="2026-08")


def test_le_todas_as_entradas_e_saidas_que_o_extrato_declara():
    lancamentos, _ = _lidos()
    conferencia = it.conferir(EXTRATO, lancamentos)
    assert conferencia["entradas"] == 229_011
    assert conferencia["saidas"] == 67_281
    assert conferencia["saldo_fecha"] is True
    assert conferencia["confere"] is True


def test_legenda_misturada_nao_engole_o_pix():
    """"P = poupança automática PIX TRANSF ... 840,00": a legenda sai, o PIX fica."""
    lancamentos, ignoradas = _lidos()
    assert any(l.valor_centavos == 84_000 and l.descricao == "PIX TRANSF PACIENTE D"
               for l in lancamentos)
    assert not any("840,00" in linha for linha in ignoradas)


def test_nome_com_minuscula_em_linha_de_continuacao_e_lido():
    """"PIX TRANSF Paciente Do10/08 225,00" sem data no início: é do mesmo dia.

    Era a regra que adivinhava legenda por "tem minúscula": engolia a
    descrição e jogava a linha fora. R$ 2.240,00 de um mês sumiram assim.
    """
    lancamentos, _ = _lidos()
    pix_do_dia_10 = [l for l in lancamentos if l.data.day == 10 and l.descricao.startswith("PIX")]
    assert sorted(l.valor_centavos for l in pix_do_dia_10) == [2_500, 22_500]
    assert {l.descricao for l in pix_do_dia_10} == {"PIX TRANSF Paciente Do"}
    renata = next(l for l in lancamentos if l.valor_centavos == 90_000)
    assert renata.data.day == 22 and renata.descricao == "PIX TRANSF Renata"


def test_data_colada_no_fim_do_nome_sai_da_descricao():
    """Cada mês viraria uma chave de memória diferente para o mesmo pagador."""
    lancamentos, _ = _lidos()
    assert not any(l.descricao.endswith(("03/08", "05/08", "10/08", "22/08"))
                   for l in lancamentos)
    assert any(l.descricao == "PIX TRANSF PACIENTE UM" for l in lancamentos)
    # e a data que não está colada em nome nenhum continua onde está
    assert any(l.descricao == "TAR PACOTE ITAU JUL/26" for l in lancamentos)


def test_aplicacao_automatica_fica_de_fora_e_o_rendimento_entra():
    lancamentos, ignoradas = _lidos()
    assert not any("Aplic Aut" in l.descricao and "Rend" not in l.descricao
                   for l in lancamentos)
    assert any(l.valor_centavos == 11 and "Rend Pago" in l.descricao for l in lancamentos)
    assert sum(1 for linha in ignoradas if "APLIC AUT" in linha.upper()) >= 4


def test_o_quadro_de_debitos_automaticos_da_segunda_pagina_nao_entra():
    """"31/07/26 DA LUZ 43,66" está fora da movimentação: é o resumo, repetido."""
    lancamentos, _ = _lidos()
    assert not any(l.valor_centavos == -4_366 for l in lancamentos)
    assert all(l.data.month == 8 for l in lancamentos)


def test_identificacao_pelo_cabecalho():
    assert it.identificacao(EXTRATO) == {
        "agencia": "1234", "conta": "56789-0", "competencia": "2026-08",
    }
    assert it.identificacao("um texto qualquer sem cabeçalho") is None


def test_conta_bate_com_o_cadastro():
    ident = it.identificacao(EXTRATO)
    assert it.conta_bate(ident, "1234") is True
    assert it.conta_bate(ident, "1234/56789-0") is True
    assert it.conta_bate(ident, "ag 1234 cc 56789-0") is True
    assert it.conta_bate(ident, "9876") is False, "outra agência"
    assert it.conta_bate(ident, "1234/11111-1") is False, "mesma agência, outra conta"
    assert it.conta_bate(ident, None) is None, "sem cadastro não há como conferir"
    assert it.conta_bate(None, "1234") is None


def test_saldos_declarados():
    assert it.saldos_declarados(EXTRATO) == (100_000, 261_730)
    negativo = EXTRATO.replace("R$ 1.000,00 R$ 2.617,30", "R$ 1.000,00- R$ 2.617,30-")
    assert it.saldos_declarados(negativo) == (-100_000, -261_730)


def test_cadastro_guarda_a_identificacao_e_acha_a_conta_do_pdf(engine):
    uma = repo.salvar_conta(engine, nome="Itaú Rô 1", tipo="corrente", titular="Rô",
                            instituicao="Itaú", parser="itau", identificador="1234")
    outra = repo.salvar_conta(engine, nome="Itaú Rô 2", tipo="corrente", titular="Rô",
                              instituicao="Itaú", parser="itau", identificador="9876")
    ident = it.identificacao(EXTRATO)
    with engine.connect() as conn:
        dona = repo.conta_pelo_identificador(conn, ident)
        assert dona["id"] == uma
        assert repo.conta_por_id(conn, outra)["identificador"] == "9876"

    repo.identificar_conta(engine, outra, "1234/56789-0")
    repo.identificar_conta(engine, uma, "")
    with engine.connect() as conn:
        assert repo.conta_pelo_identificador(conn, ident)["id"] == outra
        assert repo.conta_por_id(conn, uma)["identificador"] is None
