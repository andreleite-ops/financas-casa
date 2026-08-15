"""Fatura do cartão Nubank em PDF.

O PDF do Nubank não é uma tabela e tem três coisas que o leitor genérico erra —
uma delas por um fator de duas vezes e meia.

**O sinal de menos não é um hífen.** O Nubank imprime `−R$ 100,00` com o sinal
matemático U+2212, não com o `-` do teclado. Quem procura `-` não o encontra: o
pagamento da fatura anterior entrava como *gasto* e o mês fechava em duas vezes
e meia o que realmente foi. Estorno e IOF de volta trocavam de lado pelo mesmo
motivo.

**Cada linha traz o cartão que pagou.** `14 JUL •••• 1234 Padaria R$ 12,00` —
os quatro dígitos identificam o cartão, e ficam guardados à parte em vez de
sujar a descrição.

**A fatura é dividida por portador.** Um bloco por pessoa, cada um com o nome
e o total dela. É daí que sai o dono de cada compra: sem ler o bloco, o cartão
com adicional joga tudo na mesma pessoa.

Conferência embutida: a fatura declara o total de compras, o IOF, os outros
lançamentos e o total a pagar. `conferir()` compara o lido com esses números —
é assim que se sabe que a leitura fechou, sem depender de somar à mão.
"""

from __future__ import annotations

import re
from datetime import date

from core.money import para_centavos

from .base import ErroDeLeitura, Lancamento, ler_data

# O PDF usa sinal matemático (U+2212) e traços tipográficos. Trocá-los pelo
# hífen comum antes de qualquer coisa evita ter de lembrar disso em cada regex.
_SINAIS = {"−": "-", "–": "-", "—": "-", " ": " "}

_MOEDA = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_MES = r"JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ"

# 14 JUL •••• 1234 Padaria da Esquina - Parcela 3/4 R$ 211,38
_LANCAMENTO = re.compile(
    rf"^(?P<dia>\d{{1,2}})\s+(?P<mes>{_MES})\s+"
    r"(?:[••*.]{2,}\s*(?P<cartao>\d{4})\s+)?"
    rf"(?P<descricao>.+?)\s+(?P<sinal>-?)\s*R\$\s*(?P<valor>{_MOEDA})\s*$",
    re.IGNORECASE,
)

# "Primeiro Titular R$ 1.000,00" / "Compras de Segunda Titular R$ 500,00"
_PORTADOR = re.compile(
    rf"^(?:Compras de\s+)?(?P<nome>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.\s]{{4,60}}?)\s+R\$\s*{_MOEDA}\s*$"
)

# linhas do PDF que têm valor e não são lançamento
_RUIDO = re.compile(
    r"^(fatura anterior|pagamento recebido|total de compras|total a pagar|"
    r"pagamento mínimo|saldo em aberto|limite|valor de entrada|valor da parcela|"
    r"juros|iof r\$|cet |conversão|brl |usd |eur |total\b|fechamento)",
    re.IGNORECASE,
)

# o nome impresso no cabeçalho de toda página não é portador de bloco nenhum
_CABECALHO = re.compile(r"^(fatura \d{1,2} |transações de |\d+ de \d+$)", re.IGNORECASE)

_TOTAIS = {
    "compras": re.compile(rf"total de compras[^R]*R\$\s*({_MOEDA})", re.IGNORECASE),
    "iof": re.compile(rf"IOF de compras internacionais\s*R\$\s*({_MOEDA})", re.IGNORECASE),
    "outros": re.compile(rf"outros lançamentos\s*(-?)\s*R\$\s*({_MOEDA})", re.IGNORECASE),
    "total": re.compile(rf"total a pagar\s*R\$\s*({_MOEDA})", re.IGNORECASE),
}


def _limpar(texto: str) -> str:
    for de, para in _SINAIS.items():
        texto = texto.replace(de, para)
    return " ".join(texto.split())


_RESUMO = re.compile(r"RESUMO DA FATURA ATUAL", re.IGNORECASE)


def totais_declarados(texto: str) -> dict:
    """O que a própria fatura diz que soma. É o gabarito da leitura.

    A busca começa no quadro "RESUMO DA FATURA ATUAL" de propósito: antes dele
    vem a simulação de parcelamento, que também imprime um "Total a pagar" —
    o de parcelar em 3 vezes, com juros. Ler o primeiro que aparecesse fazia a
    conferência comparar o mês com uma dívida que ninguém contratou.
    """
    achado_resumo = _RESUMO.search(texto)
    if achado_resumo:
        texto = texto[achado_resumo.end():]
    limpo = _limpar(texto.replace("\n", " "))
    achados: dict[str, int] = {}
    for nome, padrao in _TOTAIS.items():
        m = padrao.search(limpo)
        if not m:
            continue
        try:
            if nome == "outros":
                valor = para_centavos(m.group(2))
                achados[nome] = -valor if m.group(1) == "-" else valor
            else:
                achados[nome] = para_centavos(m.group(1))
        except (ValueError, ArithmeticError):
            continue
    return achados


def _e_portador(linha: str, ja_comecou: bool) -> str | None:
    """O nome do bloco, quando a linha for o cabeçalho de um portador."""
    if _CABECALHO.match(linha) or _RUIDO.match(linha):
        return None
    achado = _PORTADOR.match(linha)
    if not achado:
        return None
    nome = achado.group("nome").strip()
    # o nome do titular vem em caixa alta no cabeçalho de cada página; o do
    # bloco vem capitalizado. Sem essa distinção, o cabeçalho viraria portador
    if nome.isupper() and ja_comecou:
        return None
    return nome


def extrair_linhas(
    texto: str, *, competencia: str | None = None, ano_referencia: int | None = None,
    origem: str = "extrato",
) -> tuple[list[Lancamento], list[str]]:
    ano_ref = ano_referencia or (int(competencia[:4]) if competencia else date.today().year)
    linhas = [_limpar(linha) for linha in texto.splitlines() if linha.strip()]

    lancamentos: list[Lancamento] = []
    ignoradas: list[str] = []
    portador: str | None = None
    comecou = False

    for crua in linhas:
        achado = _LANCAMENTO.match(crua)
        if not achado:
            nome = _e_portador(crua, comecou)
            if nome:
                portador = nome
            continue

        descricao = achado.group("descricao").strip(" -–")
        if not descricao or _RUIDO.match(descricao):
            ignoradas.append(crua)
            continue

        try:
            dia = ler_data(f"{achado.group('dia')} {achado.group('mes')}", ano_referencia=ano_ref)
            centavos = para_centavos(achado.group("valor"))
        except (ErroDeLeitura, ValueError, ArithmeticError):
            ignoradas.append(crua)
            continue

        comecou = True
        # na fatura o normal é gasto; o sinal de menos marca o que volta —
        # estorno, IOF devolvido e o pagamento da fatura anterior
        valor = centavos if achado.group("sinal") == "-" else -centavos
        extra = {}
        if achado.group("cartao"):
            extra["cartao_final"] = achado.group("cartao")
        if portador:
            extra["portador"] = portador

        lancamentos.append(
            Lancamento(
                data=dia,
                descricao=descricao,
                valor_centavos=valor,
                competencia=competencia,
                origem=origem,
                pessoa_hint=portador,
                extra=extra,
            )
        )
    if not lancamentos:
        raise ErroDeLeitura("não reconheci nenhum lançamento nesta fatura do Nubank")
    return lancamentos, ignoradas


def conferir(texto: str, lancamentos: list[Lancamento]) -> dict:
    """Compara o lido com os totais que a própria fatura imprime.

    O total de compras não inclui o pagamento da fatura anterior nem os
    estornos — por isso a comparação é com as saídas, e o total a pagar é
    conferido à parte, já com os créditos abatidos.
    """
    declarado = totais_declarados(texto)
    saidas = -sum(l.valor_centavos for l in lancamentos if l.valor_centavos < 0)
    entradas = sum(l.valor_centavos for l in lancamentos if l.valor_centavos > 0)
    # o pagamento da fatura anterior é dinheiro do mês passado: fica de fora da
    # conta do mês, como fica na própria fatura
    pagamento = sum(
        l.valor_centavos for l in lancamentos
        if l.valor_centavos > 0 and "pagamento" in l.descricao.casefold()
    )
    liquido = saidas - (entradas - pagamento)

    resultado = {
        "compras": saidas,
        "creditos": entradas - pagamento,
        "pagamento_da_anterior": pagamento,
        "liquido": liquido,
        **{f"{k}_declarado": v for k, v in declarado.items()},
    }
    esperado = declarado.get("total")
    if esperado is not None:
        # a fatura soma compras + IOF internacional − outros lançamentos; o IOF
        # já vem como linha própria entre os lançamentos, então o líquido lido
        # tem de bater com o total a pagar
        resultado["confere"] = liquido == esperado
        resultado["diferenca"] = liquido - esperado
    else:
        resultado["confere"] = None
    return resultado


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    from .pdf import texto_do_pdf

    kwargs.pop("tudo_despesa", None)
    kwargs.pop("inverter_sinal", None)
    kwargs.pop("mapa", None)
    senha = kwargs.pop("senha", None)
    texto = texto_do_pdf(conteudo, senha=senha)
    return extrair_linhas(texto, **kwargs)[0]
