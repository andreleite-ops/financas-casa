"""Camada 3 da classificacao e a analise mensal escrita, via Claude API.

Sem chave configurada o sistema inteiro continua funcionando: a classificacao
cai para regras + fila manual e a tela de analise explica o que falta. Nada
aqui pode derrubar o app.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODELO_CLASSIFICACAO = "claude-haiku-4-5-20251001"
# Sonnet dá conta da leitura do mês, que só interpreta números já apurados.
# Trocar por um modelo mais forte (claude-opus-5) é uma linha no segredo
# MODELO_ANALISE, sem mexer no código.
MODELO_PADRAO_ANALISE = "claude-sonnet-5"
LOTE = 40


def _chave_api() -> str | None:
    return _segredo("ANTHROPIC_API_KEY")


def _segredo(chave: str) -> str | None:
    valor = os.environ.get(chave)
    if valor:
        return valor
    try:
        import streamlit as st

        return st.secrets.get(chave)
    except Exception:
        return None


# lido uma vez, no boot do app: trocar de modelo é editar o segredo e dar
# Reboot, que é o mesmo gesto de qualquer outra mudança de configuração
MODELO_ANALISE = _segredo("MODELO_ANALISE") or MODELO_PADRAO_ANALISE


def disponivel() -> bool:
    if not _chave_api():
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _cliente():
    import anthropic

    return anthropic.Anthropic(api_key=_chave_api())


@dataclass
class SugestaoIA:
    indice: int
    categoria: str
    subcategoria: str | None
    confianca: float


def _extrair_json(texto: str):
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.split("```")[1]
        texto = texto[4:] if texto.startswith("json") else texto
    inicio, fim = texto.find("["), texto.rfind("]")
    if inicio == -1 or fim == -1:
        return []
    try:
        return json.loads(texto[inicio : fim + 1])
    except json.JSONDecodeError:
        return []


def sugerir_categorias(
    descricoes: list[tuple[int, str, int]],
    plano: dict[str, list[str]],
    modelo: str = MODELO_CLASSIFICACAO,
) -> list[SugestaoIA]:
    """Classifica um lote de (indice, descricao, valor_centavos).

    Devolve lista vazia se a IA nao estiver configurada ou a chamada falhar -
    quem chama trata isso como "vai para a fila manual".
    """
    if not descricoes or not disponivel():
        return []

    catalogo = "\n".join(
        f"- {cat}: {' | '.join(subs)}" for cat, subs in plano.items()
    )
    itens = "\n".join(
        f'{i}. "{desc}" ({"entrada" if valor > 0 else "saída"} de R$ {abs(valor) / 100:.2f})'
        for i, desc, valor in descricoes
    )
    prompt = (
        "Você classifica lançamentos financeiros de uma família brasileira.\n\n"
        f"Plano de contas (categoria: subcategorias):\n{catalogo}\n\n"
        f"Lançamentos:\n{itens}\n\n"
        "Responda APENAS um array JSON, um objeto por lançamento, no formato:\n"
        '[{"i": 0, "categoria": "Alimentação", "subcategoria": "Fora do Domicílio", '
        '"confianca": 0.9}]\n'
        "Use exatamente os nomes do plano de contas. confianca vai de 0 a 1 e deve ser "
        "baixa (< 0.7) quando a descrição for genérica, como PIX, transferência ou código "
        "sem nome de estabelecimento. Entradas de dinheiro só podem receber categorias de "
        "receita; saídas só categorias de despesa."
    )

    try:
        resposta = _cliente().messages.create(
            model=modelo,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        dados = _extrair_json(texto_da_resposta(resposta))
    except Exception:
        return []

    sugestoes: list[SugestaoIA] = []
    for item in dados:
        try:
            sugestoes.append(
                SugestaoIA(
                    indice=int(item["i"]),
                    categoria=str(item["categoria"]).strip(),
                    subcategoria=(str(item["subcategoria"]).strip() if item.get("subcategoria") else None),
                    confianca=float(item.get("confianca", 0.5)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sugestoes


SEM_CHAVE = (
    "**Análise por IA não configurada.**\n\n"
    "Para ligar, adicione `ANTHROPIC_API_KEY` em `.streamlit/secrets.toml` (ou em "
    "Settings › Secrets, no Streamlit Cloud). Todo o resto do sistema funciona sem "
    "ela — os números das outras telas não dependem da IA."
)

# As regras que valem para qualquer coisa escrita pela IA aqui. A primeira é a
# que mais importa: enquanto a fila de classificação não estiver vazia, os
# totais por categoria são parciais, e uma frase segura sobre um número parcial
# é pior do que nenhuma frase.
REGRAS = (
    "Regras que você não pode quebrar:\n"
    "- Use SOMENTE os números fornecidos. Não estime, não complete, não suponha "
    "valores que não estão escritos. Se algo não está nos dados, diga que não está.\n"
    "- Olhe a COBERTURA DA CLASSIFICAÇÃO antes de qualquer conclusão. Se menos de "
    "95% do gasto estiver classificado, diga isso na primeira linha e trate os "
    "totais por categoria como parciais — fale em 'do que já está classificado'.\n"
    "- Transferências entre contas do casal não são gasto nem receita; venda de bem "
    "não é renda do mês. Não some nem uma coisa nem outra ao orçamento.\n"
    "- Nunca escreva que alguém gastou demais sem citar o número e a média de "
    "comparação.\n"
    "- Português do Brasil, direto, sem conselho genérico de manual de finanças. "
    "Eles são André e Rô; o que não tem dono declarado é do Casal."
)


def texto_da_resposta(resposta) -> str:
    """O texto de uma resposta, ignorando blocos que não são texto.

    `content[0].text` parecia bastar e não basta: a resposta pode trazer outros
    tipos de bloco na frente, e pegar o primeiro cegamente estoura com
    AttributeError — um erro que não diz nada a quem está olhando a tela.
    """
    partes = [
        bloco.text for bloco in getattr(resposta, "content", [])
        if getattr(bloco, "type", "") == "text" and getattr(bloco, "text", "")
    ]
    if partes:
        return "\n\n".join(partes)
    primeiro = getattr(resposta, "content", [None])[0] if getattr(resposta, "content", None) else None
    return getattr(primeiro, "text", "") or ""


def diagnostico() -> dict:
    """O que a tela precisa mostrar quando a chamada falha."""
    try:
        import anthropic

        versao = getattr(anthropic, "__version__", "?")
    except ImportError:
        versao = "não instalado"
    chave = _chave_api() or ""
    return {
        "sdk": versao,
        "tem_chave": bool(chave),
        # só o formato, nunca a chave: serve para ver se colou o texto certo
        "formato_da_chave": f"{chave[:7]}…{len(chave)} caracteres" if chave else "—",
        "modelo_analise": MODELO_ANALISE,
        "modelo_classificacao": MODELO_CLASSIFICACAO,
    }


# Toda mensagem de falha começa assim, para a tela reconhecê-la e não gravar
# um erro no lugar da análise do mês.
MARCA_DE_FALHA = "**Não consegui"


def falhou(texto: str) -> bool:
    """A resposta é aviso de erro, não análise? Então não vale mostrar nem gravar."""
    return not texto.strip() or texto.lstrip().startswith(MARCA_DE_FALHA)


def _perguntar(prompt: str, modelo: str, max_tokens: int = 16000) -> str:
    """Uma pergunta, uma resposta — com espaço de sobra para o raciocínio.

    `max_tokens` limita o raciocínio **e** o texto final, somados. Os modelos
    atuais pensam antes de responder, e com 1.600 o pensamento consumia a cota
    inteira: a chamada voltava sem erro nenhum e sem texto nenhum, e a tela
    ficava em branco sem nada explicando o porquê. Aqui a folga é grande e o
    esforço é médio — a análise lê números já apurados, não precisa do
    raciocínio mais caro.
    """
    parametros = dict(
        model=modelo,
        max_tokens=max_tokens,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        try:
            resposta = _cliente().messages.create(**parametros)
        except TypeError:
            # SDK mais antigo não conhece output_config; a chamada vale sem ele
            parametros.pop("output_config", None)
            resposta = _cliente().messages.create(**parametros)
    except Exception as exc:
        # o nome da exceção sozinho não permite diagnóstico nenhum: "chave
        # inválida", "modelo inexistente" e "sem crédito" chegavam todos como
        # uma linha igual. A mensagem do erro é o que diz qual dos três é.
        detalhe = " ".join(str(exc).split())[:400] or type(exc).__name__
        return (
            "**Não consegui falar com a IA agora.**\n\n"
            f"`{type(exc).__name__}: {detalhe}`\n\n"
            "Se falar em *authentication*, a chave está errada ou não chegou ao app. "
            "Se falar em *credit* ou *billing*, falta saldo na organização. "
            "Se falar em *model*, o nome do modelo mudou e eu ajusto no código."
        )
    texto = texto_da_resposta(resposta)
    if texto:
        return texto

    # sem texto: dizer o motivo, que é o que permite corrigir
    motivo = getattr(resposta, "stop_reason", None)
    if motivo == "max_tokens":
        return (
            f"{MARCA_DE_FALHA} escrever a resposta inteira.**\n\n"
            "O modelo gastou todo o espaço raciocinando e não sobrou texto. "
            "Tente de novo; se repetir, o mês tem números demais para uma resposta só."
        )
    if motivo == "refusal":
        return f"{MARCA_DE_FALHA} — o modelo recusou responder a este pedido.**"
    return (
        f"{MARCA_DE_FALHA} uma resposta com texto.**\n\n"
        f"A IA devolveu blocos vazios (motivo: `{motivo}`). Tente de novo."
    )


def sugerir_subcategorias(
    itens: list[tuple[int, str, int, str, list[str]]],
    modelo: str = MODELO_CLASSIFICACAO,
) -> list[SugestaoIA]:
    """Só a subcategoria, com a categoria já decidida por gente.

    Cada item é (índice, descrição, valor, categoria escolhida, subcategorias
    possíveis). A categoria não está em jogo: quem a escolheu foi a Rô ou o
    André, e a IA não a revisa. A pergunta é mais estreita que a da camada 3 —
    "dentro de Saúde, isto é Farmácia ou Consulta?" — e por isso acerta mais.
    """
    if not itens or not disponivel():
        return []

    blocos = []
    for i, descricao, valor, categoria, opcoes in itens:
        blocos.append(
            f'{i}. "{descricao}" (R$ {abs(valor) / 100:.2f}) — categoria: {categoria}; '
            f"opções: {' | '.join(opcoes)}"
        )
    prompt = (
        "Cada lançamento abaixo já tem categoria escolhida por uma pessoa. Escolha "
        "apenas a SUBCATEGORIA, entre as opções listadas para aquele lançamento.\n\n"
        + "\n".join(blocos)
        + "\n\nResponda APENAS um array JSON: "
        '[{"i": 0, "subcategoria": "Farmácia", "confianca": 0.9}]\n'
        "Use exatamente um dos nomes listados como opção daquele item. Quando a "
        "descrição não permitir escolher (PIX, código sem nome, nome genérico), "
        "devolva confianca abaixo de 0.7 — é melhor deixar para a pessoa decidir do "
        "que chutar."
    )

    try:
        resposta = _cliente().messages.create(
            model=modelo, max_tokens=4000, messages=[{"role": "user", "content": prompt}]
        )
        dados = _extrair_json(texto_da_resposta(resposta))
    except Exception:
        return []

    sugestoes: list[SugestaoIA] = []
    for item in dados:
        try:
            if not item.get("subcategoria"):
                continue
            sugestoes.append(
                SugestaoIA(
                    indice=int(item["i"]),
                    categoria="",                       # a categoria não está em jogo
                    subcategoria=str(item["subcategoria"]).strip(),
                    confianca=float(item.get("confianca", 0.5)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sugestoes


def analisar_mes(contexto: str, modelo: str = MODELO_ANALISE) -> str:
    """Texto da tela Analise IA a partir do resumo numerico ja calculado."""
    if not disponivel():
        return SEM_CHAVE
    prompt = (
        "Você acompanha as contas de uma casa brasileira e escreve a leitura do mês "
        "para o casal que a mantém. Escreva no máximo 5 parágrafos curtos, com "
        "números concretos:\n"
        "1) onde o dinheiro foi neste mês;\n"
        "2) o que fugiu do padrão — use a seção 'Fora do padrão', que já traz a "
        "comparação com a média do ano;\n"
        "3) três sugestões de economia, cada uma com o valor que liberaria por mês. "
        "Prefira compromisso recorrente a gasto avulso: cortar assinatura vale o ano, "
        "cortar um jantar vale uma semana;\n"
        "4) como está a poupança e a sobra do mês;\n"
        "5) feche situando o mês no ano: use 'O ano até aqui' para dizer se este mês "
        "puxa a média para cima ou para baixo, e o que isso projeta para o ano se o "
        "ritmo continuar. Um mês sozinho não diz se foi caro — a média diz.\n\n"
        f"{REGRAS}\n\n{contexto}"
    )
    return _perguntar(prompt, modelo)


def analisar_ano(contexto: str, modelo: str = MODELO_ANALISE) -> str:
    """A leitura longa: padrão, sazonalidade e o que é piso do orçamento.

    Pergunta diferente da do mês, e por isso vale uma chamada própria. O mês
    responde "para onde foi o dinheiro"; só a série responde "isto acontece
    todo ano nesta época" — e é essa a diferença entre reagir ao mês e planejar
    o ano.
    """
    if not disponivel():
        return SEM_CHAVE
    prompt = (
        "Leia a série de meses abaixo e escreva a visão longa das contas desta casa, "
        "em no máximo 6 parágrafos curtos:\n"
        "1) o retrato do período: quanto entrou, quanto saiu, quanto ficou, e se a "
        "trajetória melhora ou piora ao longo dos meses;\n"
        "2) o que se repete — categorias estáveis mês a mês, que formam o piso do "
        "orçamento — e quanto esse piso custa;\n"
        "3) o que oscila, e em quais meses. Aponte concentração ('quase tudo de "
        "Lazer & Viagens está em dois meses') em vez de tratar como se fosse "
        "distribuído. Só chame de sazonalidade o que se repetir no mesmo mês em anos "
        "diferentes; havendo um ano só, diga que ainda é cedo para afirmar isso;\n"
        "4) as três categorias em que vale gastar atenção no próximo ano, com o "
        "valor anual de cada uma;\n"
        "5) o que a série sugere para as metas do ano que vem, em percentual da "
        "renda, a partir do que realmente aconteceu;\n"
        "6) o que ainda não dá para afirmar por falta de dado classificado ou de "
        "histórico.\n\n"
        f"{REGRAS}\n\n{contexto}"
    )
    return _perguntar(prompt, modelo, max_tokens=20000)


def responder_pergunta(contexto: str, pergunta: str, modelo: str = MODELO_ANALISE) -> str:
    """Pergunta livre sobre o mês, respondida só com os números do contexto.

    Vale mais que a análise pronta quando a dúvida é específica ("por que agosto
    ficou tão caro?"). A trava é a mesma: o que não está nos números não pode
    ser respondido, e dizer "isto não está nos dados" é uma resposta melhor do
    que uma frase plausível.
    """
    if not disponivel():
        return SEM_CHAVE
    prompt = (
        "Responda à pergunta do casal sobre as contas da casa, em no máximo 3 "
        "parágrafos curtos, usando apenas os números abaixo. Se a resposta não "
        "estiver neles, diga exatamente o que falta classificar ou importar para "
        "que ela possa ser respondida.\n\n"
        f"{REGRAS}\n\n"
        f"PERGUNTA: {pergunta.strip()}\n\n{contexto}"
    )
    return _perguntar(prompt, modelo, max_tokens=8000)
