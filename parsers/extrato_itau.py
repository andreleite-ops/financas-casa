"""Extrato mensal do Itaú em PDF.

O layout do Itaú não é uma tabela: é texto corrido em duas colunas, e o
extrator devolve as duas misturadas. Três coisas fogem do leitor genérico e
justificam um módulo próprio.

**A data não se repete.** Só a primeira linha de cada dia traz `dd/mm`; as
seguintes herdam a data da anterior. Num extrato de julho, a maioria das linhas
não tem data nenhuma — lê-las isoladamente perderia dois terços do mês.

**O sinal é um traço no fim do número.** `171,72-` é débito, `500,00` é
crédito. Não há coluna de D/C, e não há como adivinhar pelo texto: `PIX TRANSF`
aparece nos dois sentidos.

**Há dois números na mesma linha, às vezes.** `DA PMSP 5411 1.006,86- 853,60-`
traz o valor do lançamento e, em seguida, o saldo corrido. Ler o segundo faria
o extrato somar o saldo como se fosse gasto.

Conferência embutida: o próprio extrato imprime, no cabeçalho, o total de
entradas e o de saídas do mês. `conferir()` compara o que foi lido com esses
dois números, e é assim que se sabe que a leitura fechou — sem depender de
ninguém somar à mão.
"""

from __future__ import annotations

import re
from datetime import date

from core.money import para_centavos
from core.texto import sem_acento

from .base import ErroDeLeitura, Lancamento, ler_data
from .pdf import texto_do_pdf

# valor monetário brasileiro, com o traço de débito opcional no fim
_VALOR = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*(-?)")
_DATA_INICIO = re.compile(r"^(\d{2}/\d{2})\s+(.*)$")

# a movimentação começa depois do saldo anterior e termina no saldo final;
# fora dessa janela o PDF repete os mesmos valores em quadros de resumo, e
# somá-los contaria o mês duas vezes
_ABRE = re.compile(r"saldo anterior", re.IGNORECASE)
_FECHA = re.compile(r"saldo (em c/c|final)", re.IGNORECASE)

# A aplicação automática varre o saldo para o CDB todo dia e devolve quando
# falta. Isso não é gasto nem receita da casa — é dinheiro andando entre bolsos
# do mesmo dono —, e o próprio extrato diz numa nota que esses valores "não
# estão somados no resumo de movimentação de conta corrente". Entram aqui
# escritos de três jeitos: "Res Aplic Aut Mais", "Apl Aplic Aut Mais" e "SALDO
# APLIC AUT MAIS".
#
# "Rend Pago Aplic Aut Mais" é a exceção, e é exceção de verdade: o rendimento
# pago é crédito na conta e entra no total de entradas do mês.
_APLICACAO = re.compile(r"APLIC\s*AUT", re.IGNORECASE)
_RENDIMENTO = re.compile(r"REND\s*PAGO", re.IGNORECASE)

# busca em vez de casar do começo: a segunda coluna do PDF se mistura à
# primeira, e a linha chega como "P = poupança automática SALDO APLIC AUT MAIS
# 319,04" — o ruído está lá, só não está no início.
_RUIDO = re.compile(
    r"(SALDO ANTERIOR|SALDO EM |SALDO FINAL|SALDO DO DIA|TOTALIZADOR|SUBTOTAL)",
    re.IGNORECASE,
)


def _e_ruido(descricao: str) -> bool:
    limpa = sem_acento(descricao).upper()
    if _APLICACAO.search(limpa) and not _RENDIMENTO.search(limpa):
        return True
    return bool(_RUIDO.search(limpa))

_TOTAIS = re.compile(
    r"total\s*entradas.*?total\s*sa[ií]das.*?R\$\s*([\d.,]+)\s*R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)


def totais_declarados(texto: str) -> tuple[int, int] | None:
    """(entradas, saídas) em centavos, como o próprio extrato declara.

    É o gabarito da leitura: se o que foi lido não bate com isto, alguma linha
    ficou de fora ou entrou duas vezes.
    """
    achado = _TOTAIS.search(texto.replace("\n", " "))
    if not achado:
        return None
    try:
        return para_centavos(achado.group(1)), para_centavos(achado.group(2))
    except (ValueError, ArithmeticError):
        return None


def _limpar(descricao: str) -> str:
    return " ".join(descricao.split())


def extrair_linhas(
    texto: str, *, competencia: str | None = None, ano_referencia: int | None = None,
    origem: str = "extrato",
) -> tuple[list[Lancamento], list[str]]:
    ano_ref = ano_referencia or (int(competencia[:4]) if competencia else date.today().year)
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]

    inicio = next((i for i, linha in enumerate(linhas) if _ABRE.search(linha)), None)
    if inicio is None:
        raise ErroDeLeitura(
            "não encontrei a movimentação (a linha de saldo anterior) neste extrato"
        )
    fim = next(
        (i for i, linha in enumerate(linhas) if i > inicio and _FECHA.search(linha)),
        len(linhas),
    )

    lancamentos: list[Lancamento] = []
    ignoradas: list[str] = []
    dia_corrente: date | None = None

    for crua in linhas[inicio:fim]:
        resto = crua
        achado_data = _DATA_INICIO.match(crua)
        if achado_data:
            try:
                dia_corrente = ler_data(achado_data.group(1), ano_referencia=ano_ref)
            except ErroDeLeitura:
                pass
            resto = achado_data.group(2)

        valores = list(_VALOR.finditer(resto))
        if not valores:
            continue
        primeiro = valores[0]
        descricao = _limpar(resto[: primeiro.start()])
        if not descricao or len(descricao) < 3:
            ignoradas.append(crua)
            continue
        if _e_ruido(descricao):
            ignoradas.append(crua)
            continue
        if dia_corrente is None:
            ignoradas.append(crua)
            continue

        try:
            centavos = para_centavos(primeiro.group(1))
        except (ValueError, ArithmeticError):
            ignoradas.append(crua)
            continue
        # o traço no fim do número é o que diz débito; sem ele, é crédito
        valor = -centavos if primeiro.group(2) == "-" else centavos

        lancamentos.append(
            Lancamento(
                data=dia_corrente,
                descricao=descricao,
                valor_centavos=valor,
                competencia=competencia,
                origem=origem,
            )
        )
    return lancamentos, ignoradas


def conferir(texto: str, lancamentos: list[Lancamento]) -> dict:
    """Compara o lido com o total impresso no próprio extrato."""
    declarado = totais_declarados(texto)
    entradas = sum(l.valor_centavos for l in lancamentos if l.valor_centavos > 0)
    saidas = -sum(l.valor_centavos for l in lancamentos if l.valor_centavos < 0)
    if declarado is None:
        return {"confere": None, "entradas": entradas, "saidas": saidas}
    return {
        "confere": (entradas, saidas) == declarado,
        "entradas": entradas,
        "saidas": saidas,
        "entradas_declaradas": declarado[0],
        "saidas_declaradas": declarado[1],
    }


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    kwargs.pop("tudo_despesa", None)      # extrato de conta tem os dois sentidos
    kwargs.pop("inverter_sinal", None)
    senha = kwargs.pop("senha", None)
    texto = texto_do_pdf(conteudo, senha=senha)
    return extrair_linhas(texto, **kwargs)[0]
