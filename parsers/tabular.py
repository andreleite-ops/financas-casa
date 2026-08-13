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
    # "portador" e o nome que a fatura de cartao usa para dizer de quem foi a
    # compra — e num cartao adicional isso muda linha a linha
    "pessoa": ["pessoa", "responsavel", "quem", "titular", "de quem", "usuario",
               "portador", "nome do portador"],
    "tipo": ["tipo", "natureza", "d/c", "tipo lancamento", "operacao", "tipo de lancamento"],
    # mes de referencia, quando a planilha traz um separado da data do
    # lancamento — o INSS pago em 10/02 pode ser competencia de janeiro
    "competencia": ["mes ano", "mesano", "competencia", "mes referencia",
                    "referencia", "mes de referencia", "mes"],
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
    # duas linhas ja bastam: numa planilha curta, exigir tres fazia a coluna
    # "CATEGORIA" ser lida pelo nome e todo gasto entrar como receita
    if len(amostra) < 2:
        return False
    reconhecidos = sum(1 for v in amostra if marca_de_sinal(v) is not None)
    return reconhecidos / len(amostra) >= 0.8


def coluna_diz_o_sinal(valores) -> bool:
    """A coluna marca mesmo entrada/saída, ou só se chama "tipo"?

    Serve para quem precisa decidir se confia na coluna antes de usá-la: uma
    coluna "Tipo" cheia de "à vista" e "parcelado" tem o nome certo e não diz
    sinal nenhum.
    """
    return _parece_coluna_de_sinal(valores)


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


# cabecalho de coluna que e um mes: "jan/26", "jan-26", "01/2026", "jan/2026".
# Busca em vez de casar a string inteira, porque o cabecalho costuma trazer
# anotacao junto — "dez/25 - (Antecip.)" e um mes tanto quanto "dez/25", e
# perde-lo significaria perder a coluna inteira de valores.
_MES_COLUNA = re.compile(
    r"\b(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[/\-. ]?(\d{2,4})\b",
    re.IGNORECASE,
)
_MES_NUMERICO = re.compile(r"^\s*(0?[1-9]|1[0-2])[/\-](\d{2,4})\s*$")
_MESES_ABREV = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
                "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


# o Excel guarda "jan/26" como data de verdade; ao ler vira 2026-01-01 00:00:00
_MES_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-\d{2}")


def mes_da_coluna(nome) -> str | None:
    """Devolve 'AAAA-MM' se o cabecalho for um mes; None caso contrario.

    Aceita tanto o texto ("jan/26") quanto a data que o Excel guarda por tras
    dele — na planilha aparece "jan/26", mas o valor gravado e 01/01/2026, e e
    esse que chega aqui.
    """
    if hasattr(nome, "year") and hasattr(nome, "month"):
        return f"{nome.year:04d}-{nome.month:02d}"

    txt = sem_acento(str(nome)).strip()
    m = _MES_ISO.match(txt)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"

    m = _MES_COLUNA.search(txt)
    if m:
        mes = _MESES_ABREV[m.group(1).lower()]
        ano = int(m.group(2))
    else:
        m = _MES_NUMERICO.match(txt)
        if not m:
            return None
        mes, ano = int(m.group(1)), int(m.group(2))
    if ano < 100:
        ano += 2000
    return f"{ano:04d}-{mes:02d}"


def colunas_de_mes(colunas) -> dict[str, str]:
    """Mapeia as colunas que sao meses -> competencia, na ordem do arquivo."""
    return {c: mes for c in colunas if (mes := mes_da_coluna(c))}


def desempilhar(df: pd.DataFrame, coluna_descricao: str, dia: int = 1) -> pd.DataFrame:
    """Vira uma tabela cruzada (meses nas colunas) em uma linha por lancamento.

    Planilha de salario e bonus costuma vir assim: cada linha e um tipo de
    receita, cada coluna um mes. Sem desempilhar, o leitor so enxergaria uma
    linha por tipo e perderia a distribuicao ao longo do ano.

    Colunas de total sao descartadas — somar de novo o que ja esta nos meses
    dobraria tudo.
    """
    meses = colunas_de_mes(df.columns)
    if not meses:
        raise ErroDeLeitura("nenhuma coluna com nome de mês (jan/26, fev/26…)")

    import calendar

    linhas = []
    for _, linha in df.iterrows():
        descricao = str(linha.get(coluna_descricao, "") or "").strip()
        if not descricao or descricao.upper().startswith("TOTAL"):
            continue
        for coluna, competencia in meses.items():
            bruto = str(linha.get(coluna, "") or "").strip()
            if not bruto or bruto in ("-", "—", "0", "0,00"):
                continue
            try:
                centavos = para_centavos(bruto)
            except (ValueError, ArithmeticError):
                continue
            if centavos == 0:
                continue
            ano, mes = int(competencia[:4]), int(competencia[5:7])
            ultimo = calendar.monthrange(ano, mes)[1]
            linhas.append({
                "DATA": f"{min(dia, ultimo):02d}/{mes:02d}/{ano}",
                "DESCRICAO": descricao,
                "VALOR": bruto,
            })
    if not linhas:
        raise ErroDeLeitura("nenhum valor encontrado nas colunas de mês")
    return pd.DataFrame(linhas)


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
    # Extrato de banco costuma trazer rodapé com mais colunas que o cabeçalho —
    # o do Bradesco termina numa linha de total com um ponto e vírgula a mais, e
    # o leitor estrito abortava o arquivo inteiro por causa dela.
    #
    # Descartar toda linha torta, porém, é pior que abortar: quando a descrição
    # de um lançamento tem o próprio separador dentro ("PIX ENVIADO; REF 12"),
    # a linha sobra de campos e sumia calada — o arquivo abria, os totais
    # fechavam menos e ninguém ficava sabendo. Aqui a linha torta só é aceitada
    # quando o que sobra está vazio (é rodapé). O resto fica registrado em
    # `df.attrs["linhas_descartadas"]` para a tela do upload mostrar.
    largura = _largura_do_cabecalho(texto, sep)
    descartadas: list[str] = []

    def _linha_torta(campos: list[str]) -> list[str] | None:
        if largura and len(campos) > largura and not any(
            str(c).strip() for c in campos[largura:]
        ):
            return campos[:largura]
        descartadas.append(sep.join(str(c) for c in campos))
        return None

    df = pd.read_csv(
        io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False,
        engine="python", on_bad_lines=_linha_torta,
    )
    df.attrs["linhas_descartadas"] = descartadas
    return df


def _largura_do_cabecalho(texto: str, sep: str) -> int:
    """Quantos campos o pandas vai esperar por linha (a primeira linha manda)."""
    for linha in texto.splitlines():
        if linha.strip():
            return len(next(csv.reader([linha], delimiter=sep), []))
    return 0


def _achar_cabecalho(df: pd.DataFrame) -> pd.DataFrame:
    """Banco costuma jogar titulo e dados do cliente antes do cabecalho real."""
    if sugerir_mapeamento(df.columns).get("data") or len(colunas_de_mes(df.columns)) >= 3:
        return df
    limite = min(len(df), 25)
    for i in range(limite):
        bruta = df.iloc[i].tolist()
        linha = [str(v) for v in bruta]
        # cabecalho comum: tem uma coluna de data e outra de valor/descricao
        parece_cabecalho = sugerir_mapeamento(linha).get("data") and any(
            sugerir_mapeamento(linha).get(p) for p in ("valor", "saida", "entrada", "descricao")
        )
        # cabecalho de tabela cruzada: varias celulas sao meses. Aqui usamos os
        # valores originais, nao o texto, porque o Excel guarda "jan/26" como
        # data e converte-la para string perderia o reconhecimento
        if not parece_cabecalho:
            parece_cabecalho = len(colunas_de_mes(bruta)) >= 3
        if parece_cabecalho:
            novo = df.iloc[i + 1:].copy()
            novo.columns = [
                (v if hasattr(v, "year") else str(v).strip()) or f"col_{j}"
                for j, v in enumerate(bruta)
            ]
            return novo.reset_index(drop=True)
    return df


def listar_abas(conteudo: bytes) -> list[str]:
    """Nomes das abas de uma pasta de trabalho; lista vazia se nao for Excel."""
    try:
        return pd.ExcelFile(io.BytesIO(conteudo)).sheet_names
    except Exception:
        return []


# abas de resumo nunca sao fonte de lancamento: elas repetem, ja somados, os
# numeros que estao na aba de lancamento. Ler uma delas dobraria tudo.
_ABA_DE_RESUMO = re.compile(
    r"tabela\s*dinamica|dinamica|pivot|resumo|consolidad|totais|totalizad|grafico",
    re.IGNORECASE,
)


def aba_de_resumo(nome: str) -> bool:
    """True quando o nome da aba diz que ali mora um resumo, nao lancamentos."""
    return bool(_ABA_DE_RESUMO.search(sem_acento(str(nome))))


def _aba_com_lancamentos(conteudo: bytes, abas: list[str]) -> str:
    """Escolhe a aba que tem cara de lancamento, nao a de resumo.

    A pasta de trabalho da casa comeca pela tabela dinamica; ler a primeira aba
    por padrao significaria ler o resumo e nunca chegar aos lancamentos. Duas
    barreiras, nessa ordem: descarta pelo nome quem se anuncia como resumo e,
    entre as que sobram, fica com a primeira que tem data e valor.
    """
    candidatas = [aba for aba in abas if not aba_de_resumo(aba)] or list(abas)
    for aba in candidatas:
        try:
            df = pd.read_excel(io.BytesIO(conteudo), sheet_name=aba, dtype=str, header=0)
        except Exception:
            continue
        df = _achar_cabecalho(df)
        mapa = sugerir_mapeamento(df.columns, df)
        if mapa.get("data") and (mapa.get("valor") or mapa.get("entrada") or mapa.get("saida")):
            return aba
    return candidatas[0]


def carregar_tabela(conteudo: bytes, nome_arquivo: str = "", aba: str | None = None) -> pd.DataFrame:
    """Le CSV ou XLSX e devolve o DataFrame ja posicionado no cabecalho."""
    nome = (nome_arquivo or "").lower()
    if nome.endswith((".xlsx", ".xlsm", ".xls")):
        abas = listar_abas(conteudo)
        if aba is None and len(abas) > 1:
            aba = _aba_com_lancamentos(conteudo, abas)
        df = pd.read_excel(
            io.BytesIO(conteudo), sheet_name=aba or 0, dtype=str, header=0
        )
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


def _vazio(valor) -> bool:
    """Celula sem conteudo, inclusive o "nan" que o pandas devolve por vazio.

    Lendo a planilha como texto, uma celula em branco chega como a string
    "nan" — que tem letra e seria apontada como valor mal digitado. Uma linha
    em branco no meio do arquivo e normal; avisar sobre ela so esconderia o
    aviso que importa no meio de dezenas de falsos.
    """
    if valor is None:
        return True
    texto = str(valor).strip()
    return not texto or texto.lower() in ("nan", "none", "nat", "-", "—")


def _natureza_da_linha(linha, mapa) -> str | None:
    """"despesa"/"receita" quando a coluna de tipo diz; None quando nao diz."""
    col_tipo = mapa.get("tipo")
    if not col_tipo or not str(linha.get(col_tipo, "")).strip():
        return None
    sinal = marca_de_sinal(linha[col_tipo])
    return None if sinal is None else ("receita" if sinal > 0 else "despesa")


def _sinal_da_linha(linha, mapa, centavos: int) -> int:
    """Aplica a marca de despesa/receita ao valor, preservando o sinal dele.

    Multiplica em vez de forcar o modulo: um valor negativo dentro de DESP e um
    estorno, que abate a despesa, e dentro de REC e um ajuste, que reduz a
    receita. Forcar o modulo transformava "-1.769,60" em "+1.769,60" e o total
    do mes deixava de fechar com a planilha.
    """
    col_tipo = mapa.get("tipo")
    if col_tipo and str(linha.get(col_tipo, "")).strip():
        sinal = marca_de_sinal(linha[col_tipo])
        if sinal is not None:
            return sinal * centavos
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
    # linha que o CSV trouxe torta e não deu para recuperar: some do arquivo,
    # mas não do aviso — pode ser um lançamento de verdade
    for crua in df.attrs.get("linhas_descartadas", []):
        avisos.append(f"linha fora do formato, não importada: {str(crua)[:90]}")

    for pos, linha in df.iterrows():
        bruto_data = linha.get(mapa["data"], "")
        if bruto_data is None or not str(bruto_data).strip():
            continue
        try:
            dia = ler_data(bruto_data, ano_referencia=ano_ref)
        except ErroDeLeitura:
            continue  # linha de total/rodape

        centavos = None
        if mapa.get("valor") and not _vazio(linha.get(mapa["valor"])):
            bruto = str(linha[mapa["valor"]])
            # letra no meio do numero e erro de digitacao ("Z195,82"). Limpar e
            # somar seria inventar um valor; melhor apontar a linha
            if re.search(r"[A-Za-z]", bruto):
                avisos.append(f"linha {pos + 2}: valor não numérico {bruto!r} — ignorado")
                continue
            try:
                centavos = _sinal_da_linha(linha, mapa, para_centavos(bruto))
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

        natureza = _natureza_da_linha(linha, mapa)
        if inverter_sinal:
            centavos = -centavos
            if natureza:
                natureza = "despesa" if natureza == "receita" else "receita"

        # a competencia manda no mes do relatorio; a data continua sendo o dia
        # em que o dinheiro andou
        competencia_linha = competencia
        if mapa.get("competencia") and str(linha.get(mapa["competencia"], "")).strip():
            competencia_linha = (
                mes_da_coluna(linha[mapa["competencia"]]) or competencia
            )

        lancamentos.append(
            Lancamento(
                data=dia,
                descricao=descricao,
                valor_centavos=centavos,
                competencia=competencia_linha,
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
                natureza_hint=natureza,
            )
        )
    return lancamentos, avisos


def ler(conteudo: bytes, nome_arquivo: str = "", **kwargs) -> list[Lancamento]:
    """Caminho automatico: detecta colunas e extrai."""
    df = carregar_tabela(conteudo, nome_arquivo)
    mapa = kwargs.pop("mapa", None) or sugerir_mapeamento(df.columns, df)
    return extrair(df, mapa, **kwargs)[0]
