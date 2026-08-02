"""Normalizacao das descricoes de extrato.

Extrato de banco e fatura de cartao escrevem o mesmo estabelecimento de varias
formas ("PAG*ClinicaVertex", "EC *CLINICA VERTEX RJ", "CLINICA VERTEX  0442").
Aqui reduzimos tudo a uma forma canonica para as regras casarem e para a
memoria de estabelecimentos reconhecer o mesmo lugar na proxima fatura.
"""

from __future__ import annotations

import re
import unicodedata

# prefixos de adquirente/subadquirente que nao dizem nada sobre o gasto
_PREFIXOS = re.compile(
    r"^(PAG\*|PAGSEGURO\s*\*?|MERCPAGO\s*\*?|MERCADOPAGO\s*\*?|EC\s*\*|PICPAY\s*\*?|"
    r"IUGU\s*\*?|STONE\s*\*?|CIELO\s*\*?|REDE\s*\*?|GETNET\s*\*?|SUMUP\s*\*?|"
    r"COMPRA\s+CARTAO\s+|DEBITO\s+AUTOMATICO\s+|COMPRA\s+DEBITO\s+|"
    r"PAGAMENTO\s+ELETRONICO\s+|TARIFA\s+BANCARIA\s+)",
    re.IGNORECASE,
)

# marcadores de parcela: PARC 03/12, 3/12, PARCELA 3 DE 12
_PARCELA = re.compile(r"\b(PARC(?:ELA)?\.?\s*)?(\d{1,2})\s*(?:/|\s+DE\s+)\s*(\d{1,2})\b", re.IGNORECASE)
_DATA = re.compile(r"\b\d{2}[/-]\d{2}(?:[/-]\d{2,4})?\b")
_CODIGO_LONGO = re.compile(r"\b[A-Z]*\d{4,}[A-Z]*\b")
_ESPACOS = re.compile(r"\s+")
_SIMBOLOS = re.compile(r"[^A-Z0-9&/ ]")


def sem_acento(txt: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", txt) if not unicodedata.combining(c))


def normalizar(descricao: str) -> str:
    """Forma canonica para casar regras: maiuscula, sem acento, sem ruido."""
    if not descricao:
        return ""
    txt = sem_acento(str(descricao)).upper()
    txt = _PREFIXOS.sub("", txt).strip()
    txt = _SIMBOLOS.sub(" ", txt)
    return _ESPACOS.sub(" ", txt).strip()


def chave_estabelecimento(descricao: str) -> str:
    """Chave da memoria: normalizada e sem as partes que mudam a cada compra.

    Tira parcela, data, codigo de terminal e cidade/UF no fim, para que
    "UBER *TRIP 12/07" e "UBER *TRIP 28/07" virem a mesma chave.
    """
    txt = normalizar(descricao)
    txt = _PARCELA.sub(" ", txt)
    txt = _DATA.sub(" ", txt)
    txt = _CODIGO_LONGO.sub(" ", txt)
    txt = re.sub(r"\b(RIO DE JANEIRO|SAO PAULO|BELO HORIZONTE|BRASILIA|CURITIBA)\b", " ", txt)
    txt = re.sub(r"\s+[A-Z]{2}$", " ", txt)  # UF solta no fim
    txt = _ESPACOS.sub(" ", txt).strip()
    # descricoes muito longas viram ruido na memoria; 6 palavras bastam
    return " ".join(txt.split()[:6])


def parcela_de(descricao: str) -> tuple[int, int] | None:
    """Extrai (parcela_atual, total) quando a descricao indica parcelamento."""
    m = _PARCELA.search(sem_acento(str(descricao)).upper())
    if not m:
        return None
    atual, total = int(m.group(2)), int(m.group(3))
    if total < 2 or atual > total or total > 48:
        return None
    return atual, total
