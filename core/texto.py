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


# "crÃ©dito" no lugar de "crédito": o arquivo foi gravado em UTF-8 e lido como
# se fosse latin-1. A exportação do Nubank vem assim — nenhum acento dela chega
# inteiro. Desfazer o engano é reencodar em latin-1 e decodificar em UTF-8;
# quando o problema é outro, a conta falha e o texto original fica de pé.
_SUSPEITA_DE_MOJIBAKE = re.compile(r"[ÃÂ][\x80-\xbf -ÿ]")


def corrigir_acentuacao(txt: str) -> str:
    """Conserta o acento que veio embaralhado do arquivo de origem.

    Vale consertar na entrada, e não só na tela: a memória de estabelecimentos
    guarda a descrição, e "crÃ©dito" e "crédito" viram chaves diferentes para a
    mesma coisa.
    """
    if not txt or not _SUSPEITA_DE_MOJIBAKE.search(txt):
        return txt
    try:
        return txt.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return txt


def normalizar(descricao: str) -> str:
    """Forma canonica para casar regras: maiuscula, sem acento, sem ruido."""
    if not descricao:
        return ""
    txt = sem_acento(str(descricao)).upper()
    txt = _PREFIXOS.sub("", txt).strip()
    txt = _SIMBOLOS.sub(" ", txt)
    return _ESPACOS.sub(" ", txt).strip()


# siglas de estado que aparecem no fim da descricao de cartao ("PADARIA X RJ").
# "RO" ficou de fora de proposito: nesta casa "RO" e a Ro, nao Rondonia, e
# "ALMOCO RO" precisa continuar diferente de "ALMOCO ANDRE" — sao 92 lancamentos
# em que a marca da pessoa esta justamente ali no fim.
_UF = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "SC", "SP", "SE", "TO",
}


def _tirar_uf_do_fim(txt: str) -> str:
    """Remove a sigla de estado no fim, quando ela e mesmo contexto de cidade.

    Duas guardas. A sigla precisa ser de um estado de verdade — antes qualquer
    par de letras caia, e era assim que "RO" sumia. E precisa sobrar nome:
    numa descricao de duas palavras, a segunda quase sempre e parte do nome,
    nao a praca onde a compra foi feita.
    """
    partes = txt.split()
    if len(partes) >= 3 and partes[-1] in _UF:
        return " ".join(partes[:-1])
    return txt


# a Ro escreve de quem e o gasto no fim da descricao: "ALMOCO ANDRE",
# "CONSULTA RO", "INSS ANDRE". Sao 233 lancamentos so na carga inicial, e a
# conta em que o dinheiro saiu nao sabe disso — a planilha mistura todas.
_PESSOA_NO_FIM = re.compile(r"\b(ANDRE|RO|ROSANA|CASAL|NOS|NOSSO|NOSSA)$")
_DONO = {"ANDRE": "André", "RO": "Rô", "ROSANA": "Rô",
         "CASAL": "Casal", "NOS": "Casal", "NOSSO": "Casal", "NOSSA": "Casal"}


def pessoa_na_descricao(descricao: str) -> str | None:
    """De quem e o gasto, quando a propria descricao diz. None quando nao diz."""
    achado = _PESSOA_NO_FIM.search(normalizar(descricao))
    return _DONO.get(achado.group(1)) if achado else None


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
    txt = _ESPACOS.sub(" ", txt).strip()
    txt = _tirar_uf_do_fim(txt)
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


# Caracteres que o Streamlit lê como formatação quando um texto nosso vira
# mensagem na tela.
_MARCACAO = "*_`~[]$"


def sem_marcacao(texto: str) -> str:
    """Descricao de extrato pronta para entrar num recado da tela.

    "PCART*TAB*SAO PAULO" virava "PCART TAB SAO PAULO" com TAB em italico: os
    asteriscos do estabelecimento sao marcacao de markdown, e a tela comia
    parte do nome justamente na hora de dizer o que foi salvo. Cifrao entra na
    lista pelo mesmo motivo — um par deles vira formula.
    """
    return "".join("\\" + c if c in _MARCACAO else c for c in texto)
