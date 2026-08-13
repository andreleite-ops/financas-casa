"""Contrato comum a todos os leitores de extrato."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from dateutil import parser as dateparser

from core.money import para_centavos
from core.texto import normalizar


@dataclass
class Lancamento:
    """Um lancamento ja normalizado, pronto para entrar no banco.

    valor_centavos e sinalizado: negativo = saida, positivo = entrada.
    Os campos *_hint vem preenchidos quando a origem ja traz classificacao
    (o caso da planilha da Ro).
    """

    data: date
    descricao: str
    valor_centavos: int
    competencia: str | None = None
    origem: str = "extrato"
    categoria_hint: str | None = None
    subcategoria_hint: str | None = None
    pessoa_hint: str | None = None
    # "despesa"/"receita" quando a origem diz isso explicitamente (a coluna
    # DESP/REC da planilha da casa). Manda mais que o sinal: um estorno dentro
    # de DESP vem positivo e continua sendo despesa — abate o gasto do mes em
    # vez de entrar como receita, que e como a tabela dinamica da planilha soma.
    natureza_hint: str | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.data, datetime):
            self.data = self.data.date()
        if not self.competencia:
            self.competencia = self.data.strftime("%Y-%m")
        self.descricao = " ".join(str(self.descricao).split())

    @property
    def descricao_norm(self) -> str:
        return normalizar(self.descricao)


class ErroDeLeitura(Exception):
    """O arquivo nao bate com o layout esperado por este leitor."""


_DATA_BR = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s*$")
# A hora no fim vem do Excel: uma coluna de data lida como texto sai
# "2026-01-05 00:00:00". Sem aceitar esse rabicho, a linha caia no dateparser
# generico, que com dayfirst=True le "2026-01-05" como 1o de maio — e o mes do
# relatorio inteiro sai trocado sempre que dia e mes sao ambos <= 12.
_DATA_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?\s*$")
_MESES = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}


def ler_data(valor, ano_referencia: int | None = None) -> date:
    """Le data em qualquer formato comum de extrato brasileiro.

    Faturas de cartao costumam trazer so dia/mes; nesse caso usamos o ano de
    referencia da competencia, virando o ano quando a compra e de dezembro e a
    fatura e de janeiro.
    """
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    txt = str(valor).strip()
    if not txt:
        raise ErroDeLeitura("data vazia")

    # 12 JUL / 12 JUL 2026
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})\.?\s*(\d{2,4})?$", txt)
    if m:
        dia, mes_txt, ano_txt = m.groups()
        mes = _MESES.get(mes_txt.upper())
        if mes:
            ano = int(ano_txt) if ano_txt else (ano_referencia or date.today().year)
            if ano < 100:
                ano += 2000
            return date(ano, mes, int(dia))

    # 2026-07-12: formato ISO, ano na frente - sem ambiguidade de dia/mes.
    # Precisa ser reconhecido antes do dateparser generico, porque o
    # dayfirst=True usado abaixo faz o dateutil inverter dia e mes mesmo
    # quando o ano ja veio explicito e sem ambiguidade nenhuma.
    m = _DATA_ISO.match(txt)
    if m:
        ano, mes, dia = m.groups()
        return date(int(ano), int(mes), int(dia))

    m = _DATA_BR.match(txt)
    if m:
        dia, mes, ano_txt = m.groups()
        if ano_txt:
            ano = int(ano_txt)
            if ano < 100:
                ano += 2000
        else:
            ano = ano_referencia or date.today().year
        return date(ano, int(mes), int(dia))

    try:
        return dateparser.parse(txt, dayfirst=True).date()
    except (ValueError, OverflowError) as exc:
        raise ErroDeLeitura(f"data nao reconhecida: {valor!r}") from exc


def ler_valor(valor, credito: bool = False) -> int:
    """Converte para centavos sinalizados. credito=True forca entrada."""
    centavos = para_centavos(valor)
    return abs(centavos) if credito else centavos


def _trocar_ano(dia: date, ano: int) -> date:
    """29/02 de um ano bissexto vira 28/02 no ano que não é."""
    try:
        return dia.replace(year=ano)
    except ValueError:
        return dia.replace(year=ano, day=28)


def ajustar_ano_fatura(lancamentos: list[Lancamento], competencia: str) -> list[Lancamento]:
    """Corrige a virada de ano em fatura que so traz dia/mes.

    Fatura traz dia e mês; o ano é o da competência. Uma compra de dezembro na
    fatura de janeiro é do ano anterior — e isso vale para qualquer mês, não só
    dezembro: a fatura de janeiro carrega parcelas compradas em novembro, em
    setembro, em março. Ler "05/11" como novembro do ano da fatura jogava a
    parcela dez meses para a frente, para um mês que ainda nem existe.

    A régua é a distância até a competência: mês muito à frente é do ano
    passado; mês muito atrás, do ano que vem. Um mês de folga fica de pé, que é
    a compra feita depois do fechamento e cobrada na fatura seguinte.
    """
    if not competencia:
        return lancamentos
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    for lan in lancamentos:
        distancia = (lan.data.year - ano) * 12 + lan.data.month - mes
        if distancia > 1:
            lan.data = _trocar_ano(lan.data, lan.data.year - 1)
        elif distancia < -10:
            lan.data = _trocar_ano(lan.data, lan.data.year + 1)
        lan.competencia = competencia
    return lancamentos
