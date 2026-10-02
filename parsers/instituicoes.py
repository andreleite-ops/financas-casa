"""Leitores por instituicao.

Cada leitor e uma camada fina sobre `tabular` (CSV/XLSX) ou `pdf`, com os
ajustes conhecidos de cada banco. Os detalhes marcados como CALIBRAR serao
confirmados quando chegarem os arquivos reais do Andre e da Ro - ate la o
caminho generico ja da conta de ler os arquivos.
"""

from __future__ import annotations

from .base import ErroDeLeitura, Lancamento, ajustar_ano_fatura
from . import pdf as leitor_pdf
from . import extrato_bradesco as leitor_bradesco
from . import extrato_itau as leitor_itau, fatura_nubank as leitor_nubank, tabular

# Nubank exporta CSV com colunas fixas: date, title, amount (fatura) ou
# Data, Valor, Identificador, Descricao (conta). Valor da fatura vem positivo.
MAPA_NUBANK_FATURA = {"data": "date", "descricao": "title", "valor": "amount"}
MAPA_NUBANK_CONTA = {"data": "Data", "descricao": "Descrição", "valor": "Valor"}


def _e_planilha(nome: str) -> bool:
    return nome.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm", ".xls"))


def _ler_tabular_ou_pdf(conteudo: bytes, nome: str, *, tudo_despesa: bool, **kw) -> list[Lancamento]:
    if _e_planilha(nome):
        # senha só existe para PDF; passar adiante quebraria o leitor tabular
        kw.pop("senha", None)
        df = tabular.carregar_tabela(conteudo, nome)
        mapa = kw.pop("mapa", None) or tabular.sugerir_mapeamento(df.columns, df)
        # "Tipo" no cabeçalho não quer dizer D/C: na fatura do cartão essa
        # coluna costuma trazer "à vista"/"parcelado"/"Forma de pagamento".
        # Confirmando pelo conteúdo — e apagando o papel quando o conteúdo não
        # confirma — a coluna deixa de mandar no sinal e deixa de desligar a
        # inversão da fatura, que era o caminho para um mês inteiro de compras
        # entrar como receita.
        col_tipo = mapa.get("tipo")
        if col_tipo is not None and (
            col_tipo not in df.columns
            or not tabular.coluna_diz_o_sinal(df[col_tipo])
        ):
            mapa = {**mapa, "tipo": None}
        # fatura de cartão exporta tudo positivo: o valor é o que se gastou, e
        # quem vem negativo é estorno ou o pagamento da própria fatura. Sem
        # inverter, um mês inteiro de compras entrava como receita. Só que a
        # inversão vale apenas quando o arquivo não diz o sinal por conta
        # própria — havendo coluna de tipo (D/C) ou colunas separadas de
        # entrada e saída, quem manda é o arquivo.
        arquivo_diz_o_sinal = bool(
            mapa.get("tipo") or mapa.get("entrada") or mapa.get("saida")
        )
        kw.setdefault("inverter_sinal", tudo_despesa and not arquivo_diz_o_sinal)
        return tabular.extrair(df, mapa, **kw)[0]
    kw.pop("inverter_sinal", None)
    return leitor_pdf.ler(conteudo, nome, tudo_despesa=tudo_despesa, **kw)


def nubank(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Cartao e conta do Nubank. No CSV da fatura o gasto vem positivo."""
    # este leitor reconhece a fatura pelas proprias colunas; o tipo da conta
    # nao acrescenta nada aqui, mas nao pode vazar para quem ele chama
    kw.pop("tudo_despesa", None)
    if _e_planilha(nome):
        kw.pop("senha", None)
        df = tabular.carregar_tabela(conteudo, nome)
        colunas = set(df.columns)
        if {"date", "title", "amount"} <= colunas:
            # fatura: amount positivo = gasto
            return tabular.extrair(df, MAPA_NUBANK_FATURA, inverter_sinal=True, **kw)[0]
        mapa = tabular.sugerir_mapeamento(df.columns)
        return tabular.extrair(df, mapa, **kw)[0]
    # a fatura em PDF tem leitor próprio: o Nubank imprime o sinal de menos com
    # o caractere matemático, divide as compras por portador e põe o final do
    # cartão em cada linha — nada disso o leitor genérico enxerga
    kw.pop("inverter_sinal", None)
    return leitor_nubank.ler(conteudo, nome, **kw)


def xp(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Fatura do cartao Visa XP. CALIBRAR com amostra real."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=kw.pop("tudo_despesa", True), **kw)


def btg(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Fatura do BTG Mastercard. CALIBRAR com amostra real."""
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=kw.pop("tudo_despesa", True), **kw)


def bradesco(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Bradesco: conta corrente tem os dois lados; o cartao, so um.

    O leitor e escolhido pela instituicao, mas ser cartao e propriedade da
    CONTA. Fixando `tudo_despesa=False` aqui, a fatura do cartao Bradesco era
    lida como extrato de conta corrente — as compras ficavam positivas e o mes
    inteiro entrava do lado errado, por mais que a conta estivesse cadastrada
    como cartao.

    O PDF do extrato de conta corrente tem leitor proprio: cada movimento ocupa
    tres linhas e o sinal so se tira da variacao do saldo — nada disso o
    leitor generico enxerga.
    """
    tudo_despesa = kw.pop("tudo_despesa", False)
    if not _e_planilha(nome) and not tudo_despesa:
        kw.pop("inverter_sinal", None)
        return leitor_bradesco.ler(conteudo, nome, **kw)
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=tudo_despesa, **kw)


def itau(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Extrato de conta corrente Itaú, incluindo a Conjunta.

    O PDF do Itaú tem leitor próprio: a data só aparece na primeira linha de
    cada dia, o sinal é um traço no fim do número e há saldo corrido na mesma
    linha do valor. O leitor genérico não daria conta de nenhuma das três.
    """
    tudo_despesa = kw.pop("tudo_despesa", False)
    if _e_planilha(nome):
        return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=tudo_despesa, **kw)
    kw.pop("inverter_sinal", None)
    return leitor_itau.ler(conteudo, nome, **kw)


def generico(conteudo: bytes, nome: str = "", **kw) -> list[Lancamento]:
    """Qualquer instituicao nova, ate ganhar leitor proprio.

    Sem leitor proprio, o unico que sabe se o arquivo e de gastos e o tipo da
    conta escolhida na tela — por isso ele manda aqui.
    """
    return _ler_tabular_ou_pdf(conteudo, nome, tudo_despesa=kw.pop("tudo_despesa", False), **kw)


LEITORES = {
    "nubank": nubank,
    "xp": xp,
    "btg": btg,
    "bradesco": bradesco,
    "itau": itau,
    "generico": generico,
}

ROTULOS = {
    "nubank": "Nubank (cartão e conta)",
    "xp": "XP / Visa XP",
    "btg": "BTG",
    "bradesco": "Bradesco",
    "itau": "Itaú (inclui Conjunta)",
    "generico": "Genérico — CSV/XLSX com mapeamento de colunas",
}


# quem sabe conferir o que foi lido contra o saldo impresso no proprio extrato
ARBITROS = {"itau": leitor_itau, "bradesco": leitor_bradesco}


def _fecha_pelo_saldo(modulo, texto: str, lancamentos: list[Lancamento]) -> bool | None:
    """saldo inicial + entradas - saidas da o saldo final? None = nao da para saber."""
    if modulo is None or not hasattr(modulo, "saldos_declarados"):
        return None
    saldos = modulo.saldos_declarados(texto)
    if not saldos or not lancamentos:
        return None
    entradas = sum(l.valor_centavos for l in lancamentos if l.valor_centavos > 0)
    saidas = -sum(l.valor_centavos for l in lancamentos if l.valor_centavos < 0)
    return saldos[0] + entradas - saidas == saldos[1]


def _leitura_de_emergencia(parser: str, conteudo: bytes, nome: str,
                           competencia: str | None, kw: dict) -> list[Lancamento]:
    """Quando o leitor do banco nao reconhece o arquivo, o leitor de todos tenta.

    E o saldo do proprio extrato arbitra. O leitor generico acerta a data e o
    valor de quase qualquer layout, mas erra o SENTIDO quando o banco marca so
    o debito e deixa o credito limpo: todo PIX recebido viraria gasto. Entao
    ele le das duas maneiras e fica com a que fecha com o saldo impresso.

    Nenhuma fechando, nao importa nada: numero com o sinal trocado e pior do
    que numero nenhum — some como gasto no mes e ninguem procura o que nao
    sabe que existe. Sem saldo para conferir (fatura de cartao, por exemplo),
    vale a leitura padrao, que e o que havia antes de existir leitor do banco.
    """
    senha = kw.get("senha")
    try:
        texto = leitor_pdf.texto_do_pdf(conteudo, senha=senha)
    except ErroDeLeitura:
        texto = ""
    tentativas = []
    for convencao in ("despesa", "receita"):
        try:
            lidos = generico(conteudo, nome, competencia=competencia,
                             sem_sinal=convencao, **kw)
        except (ErroDeLeitura, TypeError):
            continue
        tentativas.append(lidos)
    if not tentativas:
        return []
    arbitro = ARBITROS.get(parser)
    conferidas = [(lidos, _fecha_pelo_saldo(arbitro, texto, lidos)) for lidos in tentativas]
    for lidos, fecha in conferidas:
        if fecha:
            return lidos
    if any(fecha is False for _, fecha in conferidas):
        return []
    return tentativas[0]


def ler_arquivo(
    parser: str,
    conteudo: bytes,
    nome_arquivo: str,
    *,
    competencia: str | None = None,
    tipo_conta: str = "corrente",
    **kw,
) -> list[Lancamento]:
    """Ponto de entrada unico do upload.

    Quem sabe se o arquivo e de gastos e a CONTA, nao a instituicao. O leitor
    sai do banco (o formato do arquivo e dele); "isto e um cartao" sai do
    cadastro da conta e manda sobre o padrao do leitor. Sem esta linha, a
    fatura de um cartao cujo banco tambem tem conta corrente — Bradesco, ou
    qualquer conta de leitor generico — era lida como extrato: as compras
    entravam positivas e o mes inteiro caia do lado errado.
    """
    leitor = LEITORES.get(parser or "generico", generico)
    kw.setdefault("tudo_despesa", tipo_conta == "cartao")
    lancamentos = leitor(conteudo, nome_arquivo, competencia=competencia, **kw)
    # O leitor do banco conhece o layout que ele já viu. Quando o banco muda o
    # formato — e eles mudam, sem avisar —, o leitor específico não reconhece
    # nada e o arquivo inteiro vira "não encontrei nenhum lançamento", com o
    # PDF cheio deles na tela. O leitor genérico não sabe das manhas daquele
    # banco, mas sabe a forma de uma linha de extrato, e salva o dia: é melhor
    # importar com o leitor de todos do que não importar.
    if not lancamentos and leitor is not generico:
        lancamentos = _leitura_de_emergencia(parser, conteudo, nome_arquivo, competencia, kw)
    if tipo_conta == "cartao" and competencia:
        lancamentos = ajustar_ano_fatura(lancamentos, competencia)
    elif competencia:
        for lan in lancamentos:
            lan.competencia = lan.data.strftime("%Y-%m")
    return lancamentos
