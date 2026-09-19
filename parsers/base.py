"""Contrato comum a todos os leitores de extrato."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from dateutil import parser as dateparser

from core.money import para_centavos
from core.texto import corrigir_acentuacao, normalizar


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
        self.descricao = corrigir_acentuacao(" ".join(str(self.descricao).split()))

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


def ajustar_ano_fatura(lancamentos: list[Lancamento], competencia: str,
                       parcela_pela_data: bool = True) -> list[Lancamento]:
    """Corrige a virada de ano em fatura que so traz dia/mes.

    Fatura traz dia e mês; o ano é o da competência. Uma compra de dezembro na
    fatura de janeiro é do ano anterior — e isso vale para qualquer mês, não só
    dezembro: a fatura de janeiro carrega parcelas compradas em novembro, em
    setembro, em março. Ler "05/11" como novembro do ano da fatura jogava a
    parcela dez meses para a frente, para um mês que ainda nem existe.

    A régua é o mês da fatura: data em mês posterior a ele é do ano passado
    (`ano_da_compra`). Depois disso cada linha ganha o mês em que conta
    (`competencia_da_compra`), a partir do ciclo tirado do próprio arquivo.
    """
    if not competencia:
        return lancamentos
    for lan in lancamentos:
        lan.data = ano_da_compra(lan.data, competencia)
    ciclo = ciclo_da_fatura([lan.data for lan in lancamentos if lan.data and lan.valor_centavos < 0],
                            competencia)
    for lan in lancamentos:
        lan.competencia = competencia_da_compra(lan.data, competencia, ciclo=ciclo)
    return lancamentos


def ano_da_compra(dia: date, competencia_da_fatura: str) -> date:
    """O ano de uma data "dd/mm" da fatura.

    Nenhuma compra de uma fatura e posterior ao mes em que ela vence: a de
    setembro (paga em setembro) fecha com compras de agosto ou de setembro,
    nunca de outubro. Uma data em mes posterior ao da fatura e do ano passado
    — a compra de 28/12 na fatura de janeiro, e a parcela de outubro do ano
    passado que o XP imprime como "05/10" na fatura de setembro. A versao que
    dava um mes de folga mandava essa parcela para outubro do ano corrente,
    um mes que ainda nem chegou.
    """
    ano, mes = int(competencia_da_fatura[:4]), int(competencia_da_fatura[5:7])
    if (dia.year, dia.month) > (ano, mes):
        return _trocar_ano(dia, dia.year - 1)
    return dia


# um ciclo de fatura dura um mes: 32 dias cobrem qualquer fechamento
DURACAO_MAXIMA_DO_CICLO = timedelta(days=32)


def _mes_anterior(ano: int, mes: int) -> tuple[int, int]:
    return (ano - 1, 12) if mes == 1 else (ano, mes - 1)


def _fim_do_mes(ano: int, mes: int) -> date:
    proximo = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return date(*proximo, 1) - timedelta(days=1)


def ciclo_da_fatura(datas: list[date], competencia_da_fatura: str) -> tuple[date, date]:
    """(inicio, fim) do ciclo da fatura, tirado das datas das compras.

    A fatura que vence em setembro fecha em setembro (Nubank, dia 14) ou no
    fim de agosto (XP): o ciclo termina no mes da fatura ou no anterior, e
    dura ate 32 dias. Dentro dessa regra, o ciclo e a janela que mais compras
    contem — as compras a vista se concentram nela; a parcela que o XP
    imprime com a data da compra original (meses atras, ou "10/09" do ano
    passado) fica de fora, sozinha.

    Janela que nao contem nem metade das compras nao e ciclo: e a fatura que
    e so de parcelas antigas, cada uma datada de um mes diferente, e uma ou
    outra por acaso datada do mes da fatura ("10/09" do ano passado). Nesse
    caso — e quando nenhuma compra e datada desses dois meses — o ciclo
    presumido e o mes anterior ao da fatura: a fatura de setembro cobra as
    compras de agosto.
    """
    ano, mes = int(competencia_da_fatura[:4]), int(competencia_da_fatura[5:7])
    anterior = _mes_anterior(ano, mes)
    presumido = (date(*anterior, 1), _fim_do_mes(*anterior))
    candidatos = sorted({d for d in datas if presumido[0] <= d <= _fim_do_mes(ano, mes)})
    if not candidatos:
        return presumido
    melhor_fim, melhor_total = None, -1
    for fim in candidatos:
        total = sum(1 for d in datas if fim - DURACAO_MAXIMA_DO_CICLO <= d <= fim)
        # empate: o ciclo termina na compra mais recente
        if total >= melhor_total:
            melhor_fim, melhor_total = fim, total
    if melhor_total * 2 < len(datas):
        return presumido
    inicio = min(d for d in datas if melhor_fim - DURACAO_MAXIMA_DO_CICLO <= d <= melhor_fim)
    return inicio, melhor_fim


def e_parcela(descricao: str | None) -> bool:
    return bool(descricao) and bool(_PARCELA.search(descricao))


# "Parcela 3/6", "PARC 03/06", "3/6": a linha e uma parcela, e a data que a
# fatura imprime e a da compra original — meses atras
_PARCELA = re.compile(r"(?i)\bparc(?:ela|\.)?\s*\d{1,2}\s*/\s*\d{1,2}\b|\b\d{1,2}/\d{1,2}\b(?!/)")


def competencia_da_compra(dia: date, competencia_da_fatura: str, descricao: str = "",
                          ciclo: tuple[date, date] | None = None) -> str:
    """A compra conta no mes em que foi feita, nao no mes da fatura.

    E como a casa sempre anotou: o gasto de julho e de julho, mesmo que a
    fatura que o cobra feche em agosto. Contar pela fatura punha as compras
    de 17 a 31 de julho em agosto — e julho ja as tinha, item a item, na
    planilha — e as de 14 a 31 de agosto em setembro.

    Parcela conta no ciclo da fatura que a cobra, em qualquer cartao — e
    isso vale para toda linha datada de fora do ciclo, tenha ou nao a
    palavra "parcela" (o XP nem a escreve). O que muda entre cartoes e so a
    data impressa: o Nubank imprime o dia em que a parcela entrou no ciclo
    (14/08, dentro dele) e ai a data serve; o XP imprime a compra original
    (10/06, antes do ciclo) e ai vale o mes em que o ciclo comeca. O ciclo
    sai do proprio arquivo (`ciclo_da_fatura`). Sem ele, a data do mes da
    fatura ou do anterior vale por si; a de fora vai para o mes anterior.
    """
    if ciclo is None:
        ano, mes = int(competencia_da_fatura[:4]), int(competencia_da_fatura[5:7])
        ciclo = (date(*_mes_anterior(ano, mes), 1), _fim_do_mes(ano, mes))
    inicio, fim = ciclo
    if inicio <= dia <= fim:
        return f"{dia.year:04d}-{dia.month:02d}"
    return f"{inicio.year:04d}-{inicio.month:02d}"


# Numa fatura de cartao a compra e a regra e o credito e a excecao: dezenas de
# compras contra o pagamento da fatura anterior e um estorno ou outro. Sete em
# cada dez linhas positivas identifica o arquivo que chama gasto de positivo.
# Contar pelo valor nao serviria: o pagamento da fatura anterior sozinho
# empata com o total das compras.
PROPORCAO_DE_GASTO = 0.7
MINIMO_PARA_DECIDIR = 3


def fatura_invertida(lancamentos: list[Lancamento]) -> bool:
    """Este lote de cartao chegou com a compra positiva?

    A pergunta e sobre o lote, nao sobre o arquivo, o leitor, a coluna ou a
    caixa de marcar — e e por isso que ela vale onde as outras falharam. A
    mesma fatura passou tres vezes com o sinal trocado, cada vez por um caminho
    que uma protecao anterior nao olhava: a caixa desmarcada, a conta corrente,
    o leitor do banco que fixava o sinal, a coluna "Tipo" com "a vista" dentro.
    O que entra num cartao e o unico lugar por onde todas passam.
    """
    com_valor = [lan for lan in lancamentos if lan.valor_centavos]
    # uma ou duas linhas nao dizem qual e a convencao do arquivo: um estorno
    # avulso lancado sozinho e positivo e esta certo assim. A fatura de
    # verdade tem dezenas de linhas, e e sobre ela que a regra fala
    if len(com_valor) < MINIMO_PARA_DECIDIR:
        return False
    positivos = sum(1 for lan in com_valor if lan.valor_centavos > 0)
    return positivos / len(com_valor) >= PROPORCAO_DE_GASTO


def endireitar(lancamentos: list[Lancamento]) -> list[Lancamento]:
    """A mesma fatura, com o sinal do sistema: compra negativa, credito positivo.

    Devolve copias — o lote original e de quem chamou. So o valor vira: a
    natureza, quando declarada, diz de que lado a linha esta, e isso nao muda
    porque o numero veio com o sinal trocado. Num cartao ela e sempre
    "despesa", e o gravador a preenche logo depois.
    """
    return [replace(lan, valor_centavos=-lan.valor_centavos) for lan in lancamentos]


def competencia_predominante(lancamentos: list[Lancamento]) -> str | None:
    """O mes em que a maioria do lote conta — o mes de verdade do arquivo.

    Para conta corrente e isto que vai no registro do upload, nao o menu. O
    menu abria no mes de hoje, e um extrato de agosto enviado em setembro
    ficava registrado como setembro: o mapa "o que falta carregar" dizia que
    setembro tinha sido carregado com o mes ainda nem fechado.
    """
    contagem: dict[str, int] = {}
    for lan in lancamentos:
        mes = lan.competencia or f"{lan.data.year:04d}-{lan.data.month:02d}"
        contagem[mes] = contagem.get(mes, 0) + 1
    if not contagem:
        return None
    return max(sorted(contagem), key=lambda m: contagem[m])
