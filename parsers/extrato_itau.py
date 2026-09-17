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
_MOEDA = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_VALOR = re.compile(rf"({_MOEDA})\s*(-?)")

# O valor mora no FIM da linha, não no primeiro número que aparecer. Uma
# descrição com número decimal dentro ("COMPRA 12,50 UN") fazia o leitor pegar
# o 12,50 como valor do lançamento — e o sinal junto, o que trocava o lado.
# Aqui casam o valor e, quando existe, o saldo corrido logo depois dele.
_VALORES_NO_FIM = re.compile(
    rf"(?P<v1>{_MOEDA})\s*(?P<s1>-?)(?:\s+(?P<v2>{_MOEDA})\s*(?P<s2>-?))?\s*$"
)

_DATA_INICIO = re.compile(r"^(\d{2}/\d{2})\s+(.*)$")

# A coluna de legendas do PDF ("A = agendamento", "P = poupança automática",
# "Para demais siglas, consulte as Notas") se mistura à coluna da
# movimentação, e a linha chega como "P = poupança automática PIX TRANSF
# FULANO 03/08 840,00". A legenda é um conjunto fechado de frases, e é isso
# que se tira do começo da linha — nada mais.
#
# A versão anterior tentava adivinhar a legenda por "tem letra minúscula e
# nenhum dígito". Só que o nome do pagador num PIX vem como o pagador escreveu:
# "PIX TRANSF Yasmin 10/08 225,00" tem minúscula, e a regra engolia a
# descrição inteira e jogava a linha fora. Foram R$ 2.240,00 de pacientes que
# sumiram de um mês da Rô, sem aviso — a conferência pelo total do extrato foi
# o que acusou.
_LEGENDA = re.compile(
    r"^(?:"
    r"[A-Z]\s*=\s*(?:[a-zà-ÿ]+\s+)+"          # "A =agendamento ", "P = poupança automática "
    r"|pelaBolsa de Valores\s+"
    r"|Para demais siglas, consulte as Notas\s+"
    r"|Explicativas nofinal doextrato\s*"
    r")"
)

# No PIX o Itaú cola a data do pagamento no fim do nome, sem espaço: "PIX
# TRANSF FULANO03/08". Ela não é parte da descrição — e, deixada lá, cada mês
# viraria uma chave de memória diferente para o mesmo pagador.
_DATA_COLADA = re.compile(r"\s*\d{2}/\d{2}$")


def _sem_legenda(linha: str) -> str:
    return _LEGENDA.sub("", linha, count=1)

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

# o quadro de resumo do extrato repete o mês inteiro em linhas como
# "Transferências, DOCs e TEDs 34% 1.620,00" e "total 2.900,01". Elas têm
# descrição e valor e passariam por lançamento; somá-las contaria o mês duas
# vezes. A janela de movimentação já as deixa de fora — isto é a segunda rede.
_RESUMO = re.compile(r"(^TOTAL\b|%$)", re.IGNORECASE)


def _e_ruido(descricao: str) -> bool:
    limpa = sem_acento(descricao).upper().strip()
    if _APLICACAO.search(limpa) and not _RENDIMENTO.search(limpa):
        return True
    if _RESUMO.search(limpa):
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


_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
_CABECALHO = re.compile(
    r"extrato mensal\s+ag\s*(?P<agencia>\d+)\s+cc\s*(?P<conta>[\d.\-]+)\s+"
    r"(?P<mes>jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\s+(?P<ano>\d{4})",
    re.IGNORECASE,
)


def identificacao(texto: str) -> dict | None:
    """De que conta e de que mês é este extrato, pelo próprio cabeçalho.

    A Rô tem duas contas no mesmo banco, com o mesmo leitor e o mesmo layout.
    Na tela elas são duas opções num menu, e escolher a errada não dá erro
    nenhum: o extrato entra inteiro na conta vizinha. O cabeçalho do PDF diz a
    agência e a conta — é isso que a tela compara com o cadastro antes de
    gravar.
    """
    achado = _CABECALHO.search(texto)
    if not achado:
        return None
    return {
        "agencia": achado.group("agencia"),
        "conta": achado.group("conta"),
        "competencia": f"{achado.group('ano')}-{_MESES[achado.group('mes').lower()]:02d}",
    }


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def conta_bate(ident: dict | None, identificador: str | None) -> bool | None:
    """O PDF é da conta cadastrada? None quando não há como saber.

    O identificador da conta é o que a pessoa digitou no cadastro — a agência,
    ou agência e conta. Basta a agência bater; a conta, quando informada,
    também tem de bater.
    """
    if not ident or not identificador:
        return None
    digitos = _so_digitos(identificador)
    agencia, conta = _so_digitos(ident["agencia"]), _so_digitos(ident["conta"])
    if not digitos.startswith(agencia.lstrip("0")) and agencia not in digitos:
        return False
    resto = digitos[len(agencia):] if digitos.startswith(agencia) else ""
    if resto and conta not in resto and resto not in conta:
        return False
    return True


_SALDOS = re.compile(
    r"saldo em\s+\d{2}/\d{2}/\d{2}\s+saldo em\s+\d{2}/\d{2}/\d{2}.*?"
    rf"R\$\s*({_MOEDA})\s*(-?)\s+R\$\s*({_MOEDA})\s*(-?)",
    re.IGNORECASE | re.DOTALL,
)


def saldos_declarados(texto: str) -> tuple[int, int] | None:
    """(saldo anterior, saldo final) em centavos, do quadro do cabeçalho.

    É a segunda régua, independente da primeira: saldo anterior + entradas −
    saídas tem de dar o saldo final. O quadro soma conta corrente e aplicação
    automática, e o leitor deixa a aplicação de fora de propósito — e a conta
    fecha mesmo assim, porque aplicar e resgatar não muda o saldo somado.
    """
    achado = _SALDOS.search(texto)
    if not achado:
        return None
    try:
        anterior = para_centavos(achado.group(1)) * (-1 if achado.group(2) == "-" else 1)
        final = para_centavos(achado.group(3)) * (-1 if achado.group(4) == "-" else 1)
    except (ValueError, ArithmeticError):
        return None
    return anterior, final


def _limpar(descricao: str) -> str:
    return " ".join(descricao.split())


def _tem_data(linha: str) -> bool:
    return bool(_DATA_INICIO.match(_sem_legenda(linha)))


def janela_da_movimentacao(linhas: list[str]) -> tuple[int, int]:
    """Onde começa e termina a movimentação, entre vários "saldo anterior".

    O extrato diz "saldo anterior" mais de uma vez: no quadro de resumo do
    cabeçalho e na primeira linha da movimentação. Pegar a primeira ocorrência
    fazia o quadro de resumo entrar junto e o mês ser contado duas vezes.
    Vale a última abertura que ainda tem dias lançados depois dela.

    O fim é o último "saldo em c/c"/"saldo final": um extrato de várias páginas
    imprime esse totalizador ao pé de cada página, e parar no primeiro deixava
    o resto do mês de fora — sem erro nenhum na tela.
    """
    aberturas = [i for i, linha in enumerate(linhas) if _ABRE.search(linha)]
    if not aberturas:
        raise ErroDeLeitura(
            "não encontrei a movimentação (a linha de saldo anterior) neste extrato"
        )
    fechamentos = [i for i, linha in enumerate(linhas) if _FECHA.search(linha)]

    melhor = None
    for inicio in aberturas:
        fim = max((f for f in fechamentos if f > inicio), default=len(linhas))
        dias = sum(1 for linha in linhas[inicio:fim] if _tem_data(linha))
        # mais dias ganha; empatando, ganha quem começa mais tarde — é a
        # abertura colada na movimentação, e não a citada no resumo
        if melhor is None or (dias, inicio) > melhor[0]:
            melhor = ((dias, inicio), (inicio, fim))
    return melhor[1]


def extrair_linhas(
    texto: str, *, competencia: str | None = None, ano_referencia: int | None = None,
    origem: str = "extrato",
) -> tuple[list[Lancamento], list[str]]:
    ano_ref = ano_referencia or (int(competencia[:4]) if competencia else date.today().year)
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    inicio, fim = janela_da_movimentacao(linhas)

    lancamentos: list[Lancamento] = []
    ignoradas: list[str] = []
    dia_corrente: date | None = None

    for crua in linhas[inicio:fim]:
        # a data só aparece na primeira linha de cada dia; as seguintes são
        # do mesmo dia, sem data no início
        resto = _sem_legenda(crua)
        no_inicio = _DATA_INICIO.match(resto)
        if no_inicio:
            resto = no_inicio.group(2)
            try:
                dia_corrente = ler_data(no_inicio.group(1), ano_referencia=ano_ref)
            except ErroDeLeitura:
                pass

        no_fim = _VALORES_NO_FIM.search(resto)
        if not no_fim:
            continue
        descricao = _DATA_COLADA.sub("", _limpar(resto[: no_fim.start()]))
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
            centavos = para_centavos(no_fim.group("v1"))
        except (ValueError, ArithmeticError):
            ignoradas.append(crua)
            continue
        # o traço no fim do número é o que diz débito; sem ele, é crédito
        valor = -centavos if no_fim.group("s1") == "-" else centavos

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
    saldos = saldos_declarados(texto)
    # a segunda régua: o saldo tem de fechar com o que foi lido
    saldo_fecha = None
    if saldos is not None:
        saldo_fecha = saldos[0] + entradas - saidas == saldos[1]
    if declarado is None:
        return {"confere": saldo_fecha, "entradas": entradas, "saidas": saidas,
                "saldo_fecha": saldo_fecha}
    return {
        "confere": (entradas, saidas) == declarado and saldo_fecha is not False,
        "entradas": entradas,
        "saidas": saidas,
        "entradas_declaradas": declarado[0],
        "saidas_declaradas": declarado[1],
        "saldo_fecha": saldo_fecha,
    }


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    kwargs.pop("tudo_despesa", None)      # extrato de conta tem os dois sentidos
    kwargs.pop("inverter_sinal", None)
    senha = kwargs.pop("senha", None)
    texto = texto_do_pdf(conteudo, senha=senha)
    return extrair_linhas(texto, **kwargs)[0]
