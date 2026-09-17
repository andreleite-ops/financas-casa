"""Extrato de conta corrente do Bradesco em PDF (o "Bradesco Celular").

Cada movimento ocupa três linhas, e nenhuma delas sozinha diz o que ele é:

    PIX RECEBIDO                          ← o histórico
    03/08/2026 1340478 11.944,57 13.609,49 ← [data] documento valor saldo
    REM: BANCO INTER SA 02/08             ← a contraparte

A data só vem na primeira linha do dia. O leitor genérico via só a linha do
meio: tomava o número do documento por descrição — e com "1340478" como
descrição nenhuma regra reconhece nada — e não tinha como saber se era
crédito ou débito, porque as duas colunas viram um número só no texto.

O sinal sai do **texto**: o histórico diz RECEBIDO ou ENVIADO, e a contraparte
vem como "REM:" (remetente, entrou) ou "DES:" (destinatário, saiu). A coluna
de saldo parecia a régua perfeita — saldo de agora menos saldo de antes é o
valor com o sinal —, mas neste PDF ela não vem em ordem dentro do dia: um PIX
enviado aparece com o saldo subindo. Confiar nela trocou o sinal de cinco
linhas e fez R$ 21 mil de PIX enviados virarem crédito. O saldo fica só como
desempate, para o histórico que o texto não decide.

O extrato imprime, no fim, *Total créditos / Total débitos / Saldo final* — a
régua da leitura, e o que provou qual dos dois critérios estava certo.

A página "Últimos Lançamentos", no fim, é de outro período (lançamentos
posteriores ao mês, até a data em que o extrato foi gerado) e fica de fora:
foi ela que fazia o mapa dizer que setembro estava carregado com agosto.
"""

from __future__ import annotations

import re
from datetime import date

from core.money import para_centavos

from .base import ErroDeLeitura, Lancamento, ler_data
from .pdf import CREDITOS, texto_do_pdf

_MOEDA = r"\d{1,3}(?:\.\d{3})*,\d{2}"

# [data] [histórico] documento valor saldo
_MOVIMENTO = re.compile(
    rf"^(?:(?P<data>\d{{2}}/\d{{2}}/\d{{4}})\s+)?"
    rf"(?:(?P<hist>[A-Za-zÀ-ÿ].*?)\s+)?"
    rf"(?P<docto>\d{{5,10}})\s+(?P<valor>{_MOEDA})\s+(?P<saldo>-?{_MOEDA}-?)\s*$"
)
# "31/07/2026 COD. LANC. 0 0,00 1.664,92": o saldo anterior, sem movimento
_SALDO = re.compile(
    rf"^(?P<data>\d{{2}}/\d{{2}}/\d{{4}})\s+COD\.\s*LANC\.\s+\d+\s+"
    rf"(?:{_MOEDA}\s+)?(?P<saldo>-?{_MOEDA}-?)\s*$"
)
# "Total 100.347,32 99.447,34 2.564,90": créditos, débitos e saldo final
_TOTAL = re.compile(rf"^Total\s+(?P<cred>{_MOEDA})\s+(?P<deb>{_MOEDA})\s+(?P<saldo>-?{_MOEDA}-?)\s*$")
_CABECALHO = re.compile(
    r"^(Bradesco Celular|Data:|Nome:|Extrato de:|Data\s+Hist[oó]rico)", re.IGNORECASE
)
_PERIODO = re.compile(
    r"Ag[êe]ncia:\s*(?P<agencia>\d+)\s*\|\s*Conta:\s*(?P<conta>[\d.\-]+)"
    r".*?Movimenta[çc][ãa]o entre:\s*(?P<ini>\d{2}/\d{2}/\d{4})\s+e\s+(?P<fim>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)
_ULTIMOS = re.compile(r"[ÚU]ltimos\s+Lan[çc]amentos", re.IGNORECASE)
# a data do PIX que o banco cola no fim da contraparte: "REM: FULANO 02/08"
_DATA_NO_FIM = re.compile(r"\s+\d{2}/\d{2}$")

# o que o texto diz sobre o lado do movimento. A contraparte decide primeiro
# ("REM:" remetente = entrou; "DES:" destinatário = saiu); depois o histórico
_ENTRADA = re.compile(
    r"\b(RECEBID[OA]|CREDITO|DEPOSITO|RESGATE|RENDIMENTO|ESTORNO|DEVOLUCAO|REEMBOLSO)\b"
)
_SAIDA = re.compile(
    r"\b(ENVIAD[OA]|PAGTO|PAGAMENTO|DEBITO|TARIFA|IOF|TRIBUTO|COBRANCA|SAQUE|COMPRA|"
    r"QR CODE)\b"
)


def _lado_pelo_texto(historico: str, contraparte: str) -> int | None:
    """+1 entrou, -1 saiu, None quando o texto não diz."""
    parte = contraparte.upper()
    if parte.startswith(("REM:", "REMET.", "REMET:")):
        return 1
    if parte.startswith(("DES:", "DEST.", "DEST:")):
        return -1
    hist = historico.upper()
    if _ENTRADA.search(hist):
        return 1
    if _SAIDA.search(hist):
        return -1
    return None


def _centavos_com_sinal(bruto: str) -> int:
    negativo = bruto.startswith("-") or bruto.endswith("-")
    valor = para_centavos(bruto.strip("-"))
    return -valor if negativo else valor


def identificacao(texto: str) -> dict | None:
    achado = _PERIODO.search(texto)
    if not achado:
        return None
    inicio = achado.group("ini")
    return {
        "agencia": achado.group("agencia"),
        "conta": achado.group("conta"),
        "competencia": f"{inicio[6:10]}-{inicio[3:5]}",
    }


def totais_declarados(texto: str) -> dict | None:
    """(créditos, débitos, saldo final) do quadro de total do mês.

    O primeiro "Total" com três números é o do mês; o da página de últimos
    lançamentos tem dois e fica de fora.
    """
    for linha in texto.splitlines():
        m = _TOTAL.match(linha.strip())
        if m:
            return {
                "entradas": para_centavos(m.group("cred")),
                "saidas": para_centavos(m.group("deb")),
                "saldo_final": _centavos_com_sinal(m.group("saldo")),
            }
    return None


def _classificar(linhas: list[str]) -> list[tuple[str, object]]:
    """Cada linha vira (tipo, conteúdo): cabeçalho, saldo, movimento ou texto."""
    saida = []
    fora = False
    for crua in linhas:
        if _CABECALHO.match(crua):
            if _ULTIMOS.search(crua):
                fora = True            # daqui em diante é outro período
            continue
        if fora:
            continue
        if _TOTAL.match(crua):
            continue
        m = _SALDO.match(crua)
        if m:
            saida.append(("saldo", m))
            continue
        m = _MOVIMENTO.match(crua)
        if m:
            saida.append(("movimento", m))
            continue
        saida.append(("texto", crua))
    return saida


def extrair_linhas(
    texto: str, *, competencia: str | None = None, ano_referencia: int | None = None,
    origem: str = "extrato",
) -> tuple[list[Lancamento], list[str]]:
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    itens = _classificar(linhas)
    if not any(tipo == "movimento" for tipo, _ in itens):
        raise ErroDeLeitura("não reconheci nenhum lançamento neste extrato do Bradesco")

    # Um texto imediatamente antes de um movimento sem histórico na própria
    # linha é o histórico dele; qualquer outro texto é a contraparte do
    # movimento anterior. É o que separa "PIX RECEBIDO" (histórico do próximo)
    # de "REM: BANCO INTER SA 02/08" (contraparte do anterior) sem olhar o
    # conteúdo — e é o que atravessa a quebra de página sem se perder.
    lancamentos: list[Lancamento] = []
    ignoradas: list[str] = []
    saldo_anterior: int | None = None
    dia_corrente: date | None = None
    for i, (tipo, conteudo) in enumerate(itens):
        if tipo == "saldo":
            saldo_anterior = _centavos_com_sinal(conteudo.group("saldo"))
            continue
        if tipo == "texto":
            continue
        m = conteudo
        if m.group("data"):
            try:
                dia_corrente = ler_data(m.group("data"), ano_referencia=ano_referencia)
            except ErroDeLeitura:
                pass
        if dia_corrente is None:
            ignoradas.append(m.group(0))
            continue

        historico = (m.group("hist") or "").strip()
        if not historico and i > 0 and itens[i - 1][0] == "texto":
            historico = itens[i - 1][1]
        contraparte = ""
        if i + 1 < len(itens) and itens[i + 1][0] == "texto":
            proximo = itens[i + 2] if i + 2 < len(itens) else None
            # o texto seguinte só é contraparte se não for o histórico do
            # próximo movimento (movimento sem histórico na própria linha)
            e_historico_do_proximo = (
                proximo is not None and proximo[0] == "movimento" and not proximo[1].group("hist")
            )
            if not e_historico_do_proximo:
                contraparte = itens[i + 1][1]
        contraparte = _DATA_NO_FIM.sub("", contraparte)
        descricao = " ".join(f"{historico} {contraparte}".split()) or m.group("docto")

        valor = para_centavos(m.group("valor"))
        saldo = _centavos_com_sinal(m.group("saldo"))
        sinal = _lado_pelo_texto(historico, contraparte)
        if sinal is None:
            # o texto não decidiu: o saldo desempata, se fechar com o valor
            if saldo_anterior is not None and abs(saldo - saldo_anterior) == valor:
                sinal = 1 if saldo > saldo_anterior else -1
            else:
                sinal = 1 if CREDITOS.search(descricao.upper()) else -1
        saldo_anterior = saldo

        lancamentos.append(Lancamento(
            data=dia_corrente, descricao=descricao, valor_centavos=sinal * valor,
            competencia=competencia, origem=origem,
            extra={"documento": m.group("docto")},
        ))
    return lancamentos, ignoradas


def conferir(texto: str, lancamentos: list[Lancamento]) -> dict:
    """Compara o lido com o total que o próprio extrato imprime."""
    entradas = sum(l.valor_centavos for l in lancamentos if l.valor_centavos > 0)
    saidas = -sum(l.valor_centavos for l in lancamentos if l.valor_centavos < 0)
    declarado = totais_declarados(texto)
    if declarado is None:
        return {"confere": None, "entradas": entradas, "saidas": saidas, "saldo_fecha": None}
    return {
        "confere": (entradas, saidas) == (declarado["entradas"], declarado["saidas"]),
        "entradas": entradas,
        "saidas": saidas,
        "entradas_declaradas": declarado["entradas"],
        "saidas_declaradas": declarado["saidas"],
        "saldo_fecha": None,
    }


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    kwargs.pop("tudo_despesa", None)
    kwargs.pop("inverter_sinal", None)
    senha = kwargs.pop("senha", None)
    texto = texto_do_pdf(conteudo, senha=senha)
    return extrair_linhas(texto, **kwargs)[0]
