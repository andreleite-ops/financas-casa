"""Dinheiro em centavos inteiros.

Todo valor monetario circula no sistema como int de centavos. Somas ficam
exatas em qualquer banco e nao ha risco de arredondamento acumulado nos
totais mensais.

Convencao de sinal: negativo = saida (despesa), positivo = entrada (receita).
Um estorno lancado numa categoria de despesa entra positivo e abate o total da
propria categoria, em vez de inflar as receitas.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

_LIMPEZA = re.compile(r"[^\d,.\-+]")
# agrupamento de milhar valido: 1.234, 12.345, 123.456, 1.234.567 — o primeiro
# grupo tem 1 a 3 digitos e os seguintes exatamente 3
_MILHAR_PONTO = re.compile(r"^[-+]?\d{1,3}(\.\d{3})+$")
_MILHAR_VIRGULA = re.compile(r"^[-+]?\d{1,3}(,\d{3})+$")


def para_centavos(valor) -> int:
    """Converte texto/numero em centavos. Entende 1.234,56 e 1,234.56."""
    if valor is None or valor == "":
        raise ValueError("valor vazio")
    if isinstance(valor, int):
        return valor * 100
    if isinstance(valor, float):
        return int(Decimal(str(valor)).scaleb(2).to_integral_value(ROUND_HALF_UP))
    if isinstance(valor, Decimal):
        return int(valor.scaleb(2).to_integral_value(ROUND_HALF_UP))

    txt = str(valor).strip()
    negativo = txt.startswith("(") and txt.endswith(")")
    txt = _LIMPEZA.sub("", txt)
    if not txt:
        raise ValueError(f"valor nao numerico: {valor!r}")

    if "," in txt and "." in txt:
        # o separador decimal e o ultimo a aparecer
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif "," in txt:
        # 1,234 é milhar; 12,34 é decimal
        txt = txt.replace(",", "") if _MILHAR_VIRGULA.match(txt) else txt.replace(",", ".")
    elif _MILHAR_PONTO.match(txt):
        # 1.234 sem centavos -> milhar. O agrupamento precisa ser válido de
        # ponta a ponta: "4936.087" tem quatro dígitos antes do ponto, o que
        # nunca ocorre num milhar brasileiro — ali o ponto é decimal, como o
        # Excel grava um número com três casas.
        txt = txt.replace(".", "")

    centavos = int(Decimal(txt).scaleb(2).to_integral_value(ROUND_HALF_UP))
    return -abs(centavos) if negativo else centavos


def fmt_brl(centavos: int, sinal: bool = False) -> str:
    """Formata centavos como R$ 1.234,56."""
    if centavos is None:
        return "—"
    negativo = centavos < 0
    inteiro, resto = divmod(abs(int(centavos)), 100)
    corpo = f"{inteiro:,}".replace(",", ".") + f",{resto:02d}"
    if negativo:
        return f"-R$ {corpo}"
    return f"+R$ {corpo}" if sinal else f"R$ {corpo}"


def fmt_mil(centavos: int) -> str:
    """Formata centavos como milhares com 1 casa: 33,4."""
    if centavos is None:
        return "—"
    return f"{centavos / 100_000:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
