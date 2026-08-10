"""Leitura de faturas e extratos em PDF.

Estrategia: extrair o texto linha a linha e reconhecer as linhas que tem cara
de lancamento (data + descricao + valor no fim). Funciona para a maioria das
faturas brasileiras; cada instituicao ajusta detalhes no seu proprio modulo.

Faturas de cartao trazem o valor sem sinal - tudo e gasto. Por isso
`credito_por_padrao=False` faz todo valor virar saida, e as poucas linhas de
estorno/pagamento sao reconhecidas por palavra-chave.
"""

from __future__ import annotations

import io
import re
from datetime import date

from core.money import para_centavos
from core.texto import sem_acento

from .base import ErroDeLeitura, Lancamento, ler_data

_MOEDA = r"\(?\d{1,3}(?:\.\d{3})*,\d{2}\)?|\(?\d+,\d{2}\)?"

# data + descricao + valor [+ marca D/C] [+ saldo].
# A ultima parte e opcional porque extrato de conta corrente imprime o saldo
# corrido depois do valor, enquanto fatura de cartao traz so o valor. Sem
# tratar isso, o saldo seria lido como se fosse o valor do lancamento.
LINHA_LANCAMENTO = re.compile(
    r"^\s*(?P<data>\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}\s+[A-Za-z]{3}\.?)\s+"
    r"(?P<desc>.+?)\s+"
    rf"(?P<sinal>[-+]?)\s*(?:R\$\s*)?(?P<valor>{_MOEDA})"
    r"(?:\s*(?P<marca>[DC])\b)?"
    rf"(?:\s+(?:R\$\s*)?(?P<saldo>{_MOEDA})\s*(?:[DC]\b)?)?"
    r"\s*$"
)

# linhas que reduzem a fatura em vez de somar (entram como valor positivo)
ESTORNOS = re.compile(
    r"\b(ESTORNO|CREDITO DE|DEVOLUCAO|CANCELAMENTO|PAGAMENTO EFETUADO|PAGTO EFETUADO|"
    r"PAGAMENTO RECEBIDO|SALDO ANTERIOR|PAGAMENTO DE FATURA|CASHBACK|REEMBOLSO)\b"
)

# linhas de cabecalho/rodape que casam o padrao mas nao sao lancamento
RUIDO = re.compile(
    r"\b(TOTAL DA FATURA|TOTAL A PAGAR|SALDO ANTERIOR|LIMITE|VENCIMENTO|SUBTOTAL|"
    r"TOTAL DE COMPRAS|SALDO DO DIA|SALDO EM|SALDO ATUAL|TOTAL GERAL|VALOR TOTAL|"
    r"PROXIMA FATURA|MELHOR DIA)\b"
)


def texto_do_pdf(conteudo: bytes, senha: str | None = None) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ErroDeLeitura("pdfplumber nao instalado") from exc

    partes: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(conteudo), password=senha) as pdf:
            for pagina in pdf.pages:
                partes.append(pagina.extract_text() or "")
    except Exception as exc:  # pdf protegido, corrompido ou so imagem
        raise ErroDeLeitura(f"nao consegui ler o PDF: {exc}") from exc

    texto = "\n".join(partes)
    if not texto.strip():
        raise ErroDeLeitura(
            "PDF sem texto selecionavel (provavelmente digitalizado). "
            "Baixe a versao CSV/Excel do extrato."
        )
    return texto


def extrair_linhas(
    texto: str,
    *,
    competencia: str | None = None,
    ano_referencia: int | None = None,
    tudo_despesa: bool = True,
    origem: str = "extrato",
) -> tuple[list[Lancamento], list[str]]:
    """Reconhece os lancamentos no texto ja extraido do PDF."""
    ano_ref = ano_referencia or (int(competencia[:4]) if competencia else date.today().year)
    lancamentos: list[Lancamento] = []
    ignoradas: list[str] = []

    for linha in texto.splitlines():
        crua = linha.strip()
        if not crua or len(crua) < 8:
            continue
        limpa = sem_acento(crua).upper()
        m = LINHA_LANCAMENTO.match(crua)
        if not m:
            continue
        if RUIDO.search(limpa):
            ignoradas.append(crua)
            continue

        descricao = " ".join(m.group("desc").split())
        if len(descricao) < 2:
            ignoradas.append(crua)
            continue
        try:
            dia = ler_data(m.group("data"), ano_referencia=ano_ref)
            centavos = abs(para_centavos(m.group("valor")))
        except (ErroDeLeitura, ValueError, ArithmeticError):
            ignoradas.append(crua)
            continue

        marca = (m.group("marca") or "").upper()
        # a marca D/C do proprio banco manda mais que qualquer heuristica
        if marca == "C":
            valor = centavos
        elif marca == "D":
            valor = -centavos
        elif m.group("sinal") == "+" or ESTORNOS.search(limpa):
            valor = centavos
        else:
            valor = -centavos

        lancamentos.append(
            Lancamento(
                data=dia,
                descricao=descricao,
                valor_centavos=valor,
                competencia=competencia,
                origem=origem,
            )
        )
    return lancamentos, ignoradas


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    senha = kwargs.pop("senha", None)
    texto = texto_do_pdf(conteudo, senha=senha)
    return extrair_linhas(texto, **kwargs)[0]


# uma linha que tem data no começo e valor no fim é candidata a lançamento,
# mesmo que o formato exato não case — serve para o diagnóstico apontar
# "isto parecia lançamento e eu deixei passar"
_PARECE_LANCAMENTO = re.compile(
    rf"^\s*\d{{1,2}}[/-]\d{{1,2}}.*({_MOEDA})\s*[DC]?\s*$"
)


def diagnosticar(conteudo: bytes, senha: str | None = None, **kwargs) -> dict:
    """O que eu li deste PDF, linha por linha — para quando o layout é novo.

    Um leitor que devolve zero lançamentos e nada mais não deixa ninguém
    avançar: não dá para saber se o PDF é imagem, se a senha está errada, se o
    layout mudou ou se o banco escreve a data de outro jeito. Aqui sai o texto
    cru, o que foi reconhecido e o que passou perto e ficou de fora — que é
    exatamente o que preciso ver para ensinar o leitor a ler o banco novo.
    """
    texto = texto_do_pdf(conteudo, senha=senha)
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    lancamentos, ignoradas = extrair_linhas(texto, **kwargs)

    reconhecidas = {  # as linhas que viraram lançamento, para saber o resto
        f"{lan.data:%d/%m}|{lan.descricao[:20]}" for lan in lancamentos
    }
    quase = [
        linha for linha in linhas
        if _PARECE_LANCAMENTO.match(linha)
        and not LINHA_LANCAMENTO.match(linha)
    ]
    return {
        "linhas_no_pdf": len(linhas),
        "lancamentos": lancamentos,
        "ignoradas": ignoradas,
        "quase": quase,
        "amostra": linhas[:40],
        "reconhecidas": len(reconhecidas),
    }
