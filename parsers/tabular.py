"""Leitor generico de CSV/XLSX com deteccao e mapeamento de colunas.

E o motor por tras de tres coisas: contas de instituicoes que ainda nao tem
leitor proprio, o import da planilha da Ro e os proprios leitores por banco,
que sao camadas finas em cima daqui.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

import pandas as pd

from core.money import para_centavos
from core.texto import sem_acento

from .base import ErroDeLeitura, Lancamento, ler_data

# sinonimos aceitos em cada papel de coluna, ja sem acento e em minuscula
SINONIMOS = {
    "data": ["data", "data lancamento", "data da compra", "data compra", "data mov",
             "data movimento", "dt", "data transacao", "data de lancamento", "date",
             "data pagamento", "data efetivacao", "dia"],
    "descricao": ["descricao", "historico", "lancamento", "estabelecimento", "titulo",
                  "beneficiario", "favorecido", "detalhe", "movimentacao",
                  "descricao lancamento", "memo", "title", "description", "local",
                  "onde", "item", "transacao"],
    "valor": ["valor", "valor r", "valor brl", "montante", "amount", "vlr", "valor lancamento",
              "valor da compra", "quantia", "total", "valor (r$)", "preco"],
    "entrada": ["entrada", "credito", "receita", "recebimento", "entradas", "credit",
                "valor credito", "deposito"],
    "saida": ["saida", "debito", "despesa", "pagamento", "saidas", "debit", "valor debito",
              "gasto"],
    "categoria": ["categoria", "classificacao", "grupo", "tipo de gasto", "category", "conta"],
    "subcategoria": ["subcategoria", "sub categoria", "sub-categoria", "detalhamento",
                     "subgrupo", "subclassificacao"],
    "pessoa": ["pessoa", "responsavel", "quem", "titular", "de quem", "usuario"],
    "tipo": ["tipo", "natureza", "d/c", "tipo lancamento", "operacao", "tipo de lancamento"],
}


def _chave(texto) -> str:
    txt = sem_acento(str(texto)).lower().strip()
    txt = re.sub(r"[^a-z0-9 ]", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _parece_coluna_de_sinal(valores) -> bool:
    """A coluna guarda DESP/REC (ou D/C) em vez de nomes de categoria?

    Planilha caseira costuma ter uma coluna "CATEGORIA" que na verdade diz se
    a linha é despesa ou receita. Pelo nome ela seria lida como categoria, e aí
    todo gasto entraria como receita — por isso vale olhar o conteúdo.
    """
    amostra = [str(v).strip() for v in valores if str(v).strip()][:60]
    if len(amostra) < 3:
        return False
    reconhecidos = sum(1 for v in amostra if marca_de_sinal(v) is not None)
    return reconhecidos / len(amostra) >= 0.8


def sugerir_mapeamento(colunas, amostra=None) -> dict[str, str | None]:
    """Casa cada papel com a coluna mais parecida do arquivo.

    Recebendo `amostra` (o próprio DataFrame), também olha o conteúdo para
    corrigir o caso da coluna de categoria que guarda DESP/REC.
    """
    if amostra is not None:
        colunas_de_sinal = [
            c for c in colunas
            if c in getattr(amostra, "columns", []) and _parece_coluna_de_sinal(amostra[c])
        ]
        if colunas_de_sinal:
            restantes = [c for c in colunas if c not in colunas_de_sinal]
            mapa = sugerir_mapeamento(restantes)
            mapa["tipo"] = colunas_de_sinal[0]
            return mapa

    normalizadas = {c: _chave(c) for c in colunas}
    mapa: dict[str, str | None] = {}
    usadas: set[str] = set()
    for papel, opcoes in SINONIMOS.items():
        achou = None
        for exato in opcoes:  # casamento exato tem preferencia
            for col, norm in normalizadas.items():
                if col in usadas:
                    continue
                if norm == exato:
                    achou = col
                    break
            if achou:
                break
        if not achou:  # depois, casamento por conter
            for col, norm in normalizadas.items():
                if col in usadas or not norm:
                    continue
                if any(norm.startswith(o) or o in norm for o in opcoes):
                    achou = col
                    break
        if achou:
            usadas.add(achou)
        mapa[papel] = achou
    return mapa


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            texto = conteudo.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ErroDeLeitura("nao consegui decodificar o arquivo")

    amostra = "\n".join(texto.splitlines()[:30])
    try:
        sep = csv.Sniffer().sniff(amostra, delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ";" if amostra.count(";") > amostra.count(",") else ","
    return pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False)


def _achar_cabecalho(df: pd.DataFrame) -> pd.DataFrame:
    """Banco costuma jogar titulo e dados do cliente antes do cabecalho real."""
    if sugerir_mapeamento(df.columns).get("data"):
        return df
    limite = min(len(df), 25)
    for i in range(limite):
        linha = [str(v) for v in df.iloc[i].tolist()]
        if sugerir_mapeamento(linha).get("data") and any(
            sugerir_mapeamento(linha).get(p) for p in ("valor", "saida", "entrada", "descricao")
        ):
            novo = df.iloc[i + 1:].copy()
            novo.columns = [str(v).strip() or f"col_{j}" for j, v in enumerate(linha)]
            return novo.reset_index(drop=True)
    return df


def carregar_tabela(conteudo: bytes, nome_arquivo: str = "") -> pd.DataFrame:
    """Le CSV ou XLSX e devolve o DataFrame ja posicionado no cabecalho."""
    nome = (nome_arquivo or "").lower()
    if nome.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(conteudo), dtype=str, header=0)
    elif nome.endswith((".csv", ".txt", "")):
        df = _ler_csv(conteudo)
    else:
        try:
            df = _ler_csv(conteudo)
        except ErroDeLeitura:
            df = pd.read_excel(io.BytesIO(conteudo), dtype=str, header=0)
    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return _achar_cabecalho(df)


# marcas de entrada e saida usadas por bancos e por planilhas caseiras.
# "DESP"/"REC" sao o jeito mais comum de abreviar em planilha de familia.
MARCAS_ENTRADA = {"C", "CREDITO", "ENTRADA", "RECEITA", "REC", "R", "RECEITAS",
                  "RECEBIMENTO", "RECEB", "+"}
MARCAS_SAIDA = {"D", "DEBITO", "DEB", "SAIDA", "DESPESA", "DESP", "DESPESAS",
                "GASTO", "PAGAMENTO", "PGTO", "-"}


def marca_de_sinal(valor) -> int | None:
    """+1 para entrada, -1 para saida, None quando a marca nao diz nada."""
    marca = sem_acento(str(valor)).upper().strip().rstrip(".")
    if marca in MARCAS_ENTRADA:
        return 1
    if marca in MARCAS_SAIDA:
        return -1
    return None


def _sinal_da_linha(linha, mapa, centavos: int) -> int:
    """Decide entrada/saida quando o valor vem sem sinal."""
    col_tipo = mapa.get("tipo")
    if col_tipo and str(linha.get(col_tipo, "")).strip():
        sinal = marca_de_sinal(linha[col_tipo])
        if sinal is not None:
            return sinal * abs(centavos)
    return centavos


def extrair(
    df: pd.DataFrame,
    mapa: dict[str, str | None],
    *,
    origem: str = "extrato",
    competencia: str | None = None,
    ano_referencia: int | None = None,
    inverter_sinal: bool = False,
) -> tuple[list[Lancamento], list[str]]:
    """Transforma o DataFrame em Lancamentos. Devolve (lancamentos, avisos)."""
    if not mapa.get("data"):
        raise ErroDeLeitura("nao encontrei a coluna de data")
    if not (mapa.get("valor") or mapa.get("entrada") or mapa.get("saida")):
        raise ErroDeLeitura("nao encontrei a coluna de valor")

    ano_ref = ano_referencia or (int(competencia[:4]) if competencia else date.today().year)
    lancamentos: list[Lancamento] = []
    avisos: list[str] = []

    for pos, linha in df.iterrows():
        bruto_data = linha.get(mapa["data"], "")
        if bruto_data is None or not str(bruto_data).strip():
            continue
        try:
            dia = ler_data(bruto_data, ano_referencia=ano_ref)
        except ErroDeLeitura:
            continue  # linha de total/rodape

        centavos = None
        if mapa.get("valor") and str(linha.get(mapa["valor"], "")).strip():
            try:
                centavos = _sinal_da_linha(linha, mapa, para_centavos(linha[mapa["valor"]]))
            except (ValueError, ArithmeticError):
                centavos = None
        if centavos is None and (mapa.get("entrada") or mapa.get("saida")):
            entrada = saida = 0
            if mapa.get("entrada") and str(linha.get(mapa["entrada"], "")).strip():
                try:
                    entrada = abs(para_centavos(linha[mapa["entrada"]]))
                except (ValueError, ArithmeticError):
                    entrada = 0
            if mapa.get("saida") and str(linha.get(mapa["saida"], "")).strip():
                try:
                    saida = abs(para_centavos(linha[mapa["saida"]]))
                except (ValueError, ArithmeticError):
                    saida = 0
            if entrada or saida:
                centavos = entrada - saida
        if not centavos:
            continue

        descricao = ""
        if mapa.get("descricao"):
            descricao = str(linha.get(mapa["descricao"], "") or "").strip()
        if not descricao:
            descricao = "SEM DESCRICAO"
            avisos.append(f"linha {pos + 2}: sem descricao")

        if inverter_sinal:
            centavos = -centavos

        lancamentos.append(
            Lancamento(
                data=dia,
                descricao=descricao,
                valor_centavos=centavos,
                competencia=competencia,
                origem=origem,
                categoria_hint=(str(linha[mapa["categoria"]]).strip()
                                if mapa.get("categoria") and str(linha.get(mapa["categoria"], "")).strip()
                                else None),
                subcategoria_hint=(str(linha[mapa["subcategoria"]]).strip()
                                   if mapa.get("subcategoria")
                                   and str(linha.get(mapa["subcategoria"], "")).strip()
                                   else None),
                pessoa_hint=(str(linha[mapa["pessoa"]]).strip()
                             if mapa.get("pessoa") and str(linha.get(mapa["pessoa"], "")).strip()
                             else None),
            )
        )
    return lancamentos, avisos


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    """Caminho automatico: detecta colunas e extrai."""
    df = carregar_tabela(conteudo, nome_arquivo)
    mapa = kwargs.pop("mapa", None) or sugerir_mapeamento(df.columns, df)
    return extrair(df, mapa, **kwargs)[0]
