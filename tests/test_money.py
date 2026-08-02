"""Testes de core.money: conversao para centavos e formatacao."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.money import fmt_brl, para_centavos


# --------------------------------------------------------------------------
# para_centavos
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "valor, esperado",
    [
        ("1.234,56", 123456),      # formato BR: ponto milhar, virgula decimal
        ("1,234.56", 123456),      # formato US: virgula milhar, ponto decimal
        ("89,90", 8990),
        ("-45", -4500),
        ("(1.200,00)", -120000),   # parenteses contabeis = negativo
        ("R$ 1.234,56", 123456),   # simbolo e espaco sao limpos
        ("1234.56", 123456),
        ("1234,56", 123456),
    ],
)
def test_para_centavos_texto(valor, esperado):
    assert para_centavos(valor) == esperado


def test_para_centavos_float():
    assert para_centavos(1234.56) == 123456
    assert para_centavos(0.1) == 10
    assert para_centavos(-45.5) == -4550


def test_para_centavos_int():
    assert para_centavos(1234) == 123400
    assert para_centavos(-45) == -4500
    assert para_centavos(0) is not None  # 0 e "falsy" mas nao deve ser tratado como vazio
    assert para_centavos(0) == 0


def test_para_centavos_decimal():
    assert para_centavos(Decimal("99.99")) == 9999
    assert para_centavos(Decimal("-1.005")) == -101  # ROUND_HALF_UP


def test_para_centavos_vazio_da_erro():
    with pytest.raises(ValueError):
        para_centavos(None)
    with pytest.raises(ValueError):
        para_centavos("")


# --------------------------------------------------------------------------
# fmt_brl
# --------------------------------------------------------------------------
def test_fmt_brl_positivo():
    assert fmt_brl(123456) == "R$ 1.234,56"
    assert fmt_brl(123456, sinal=True) == "+R$ 1.234,56"


def test_fmt_brl_negativo():
    assert fmt_brl(-123456) == "-R$ 1.234,56"
    # sinal=True nao adiciona nada ao negativo, o "-" ja esta la
    assert fmt_brl(-123456, sinal=True) == "-R$ 1.234,56"


def test_fmt_brl_zero():
    assert fmt_brl(0) == "R$ 0,00"
    assert fmt_brl(0, sinal=True) == "+R$ 0,00"


def test_fmt_brl_none():
    assert fmt_brl(None) == "—"


# --------------------------------------------------------------------------
# exatidao: soma de centavos nao sofre do drift de float
# --------------------------------------------------------------------------
def test_soma_de_centavos_e_exata():
    valores = ["0,10", "0,20"] * 5000
    total_centavos = sum(para_centavos(v) for v in valores)
    assert total_centavos == 5000 * (10 + 20)
    assert total_centavos == 150_000
    assert fmt_brl(total_centavos) == "R$ 1.500,00"


def test_soma_de_centavos_e_exata_com_valores_tipo_um_terco():
    # 1/3 nao tem representacao binaria exata; em centavos cada parcela e um
    # inteiro, entao a soma bate exatamente com o esperado.
    parcela = para_centavos("33,33")
    total = sum(parcela for _ in range(3))
    assert total == 9999
    # o mesmo calculo em float puro tende a nao fechar redondinho
    total_float = sum(33.33 for _ in range(3))
    assert round(total_float * 100) == total  # so bate depois de arredondar


def test_float_puro_pode_derivar_mas_centavos_nao():
    valores = [0.10, 0.20] * 10_000
    total_float = sum(valores)
    # a soma ingenua em float normalmente nao fecha em 3000.00 exatos
    assert total_float != 3000.0
    total_centavos = sum(para_centavos(v) for v in valores)
    assert total_centavos == 300_000
