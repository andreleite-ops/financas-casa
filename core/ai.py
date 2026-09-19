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
# A leitura escrita acontece algumas vezes por mês e é o produto final da
# casa: vale o modelo mais forte. Trocar por um mais barato (claude-sonnet-5)
# é uma linha no segredo MODELO_ANALISE, sem mexer no código.
MODELO_PADRAO_ANALISE = "claude-opus-5"
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
    "- Toda afirmação carrega o número que a sustenta e a régua de comparação: "
    "'R$ 17.563 contra R$ 6.600 de média' e uma frase; 'a saúde pesou' não é nada.\n"
    "- Não descreva o que a tabela já mostra. O dono lê os números sozinho; o que ele "
    "não vê é a relação entre eles — o que explica o quê, o que é causa e o que é "
    "consequência, o que vai se repetir e o que não vai.\n"
    "- Separe compromisso que se repete de gasto avulso. Cortar assinatura vale o ano; "
    "cortar um jantar vale uma semana. Toda sugestão vem com o valor que libera POR MÊS "
    "e com o que ela custa em troca.\n"
    "- Quantifique o que sugerir: em vez de 'reduzir alimentação', diga qual "
    "subcategoria, quanto ela é hoje, e para quanto dá para ir com base no que já "
    "aconteceu em outro mês.\n"
    "- Nada de conselho genérico de manual de finanças, nada de elogio, nada de "
    "encerramento motivacional. Se um número for bom, diga por quê, com a comparação.\n"
    "- Markdown: títulos ### na ordem pedida, parágrafos curtos, listas quando houver "
    "itens paralelos. Pode usar tabela quando a comparação for de três colunas ou mais.\n"
    "- Português do Brasil, direto. Eles são André e Rô; o que não tem dono declarado "
    "é do Casal."
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


def _chamar(parametros: dict):
    """A chamada em streaming, com os desvios de SDK que já apareceram.

    Streaming não é detalhe de desempenho aqui: o SDK recusa de saída uma
    chamada sem streaming que possa demorar mais de dez minutos, e a leitura
    longa pede espaço grande de resposta. Foi exatamente essa recusa que
    apareceu na tela como "Streaming is required for operations that may take
    longer than 10 minutes" — a análise nunca chegou a ser pedida.

    Dois desvios: SDK sem `messages.stream` (cai no `create` de sempre) e SDK
    que não conhece `output_config` (a chamada vale sem ele).
    """
    cliente = _cliente()
    fluxo = getattr(cliente.messages, "stream", None)
    if fluxo is None:
        try:
            return cliente.messages.create(**parametros)
        except TypeError:
            return cliente.messages.create(
                **{k: v for k, v in parametros.items() if k != "output_config"}
            )
    try:
        with fluxo(**parametros) as corrente:
            return corrente.get_final_message()
    except Exception:
        # o SDK antigo pode recusar `output_config` de dois jeitos: erro de
        # argumento aqui, ou 400 vindo da API. Uma segunda tentativa sem ele
        # separa "esta versão não conhece o campo" de "a chamada falhou mesmo"
        if "output_config" not in parametros:
            raise
        sem_config = {k: v for k, v in parametros.items() if k != "output_config"}
        with fluxo(**sem_config) as corrente:
            return corrente.get_final_message()


def _perguntar(prompt: str, modelo: str, max_tokens: int = 16000,
               esforco: str = "medium") -> str:
    """Uma pergunta, uma resposta — com espaço de sobra para o raciocínio.

    `max_tokens` limita o raciocínio **e** o texto final, somados. Os modelos
    atuais pensam antes de responder, e com 1.600 o pensamento consumia a cota
    inteira: a chamada voltava sem erro nenhum e sem texto nenhum, e a tela
    ficava em branco sem nada explicando o porquê. Aqui a folga é grande, e a
    resposta vem em streaming: com espaço grande, o SDK recusa a chamada que
    não é em streaming.
    """
    parametros = dict(
        model=modelo,
        max_tokens=max_tokens,
        output_config={"effort": esforco},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        resposta = _chamar(parametros)
    except Exception as exc:
        return _recado_de_erro(exc)
    return texto_da_resposta(resposta) or _recado_sem_texto(resposta)


def _recado_de_erro(exc: Exception) -> str:
    """O nome da exceção sozinho não permite diagnóstico nenhum: "chave
    inválida", "modelo inexistente" e "sem crédito" chegavam todos como uma
    linha igual. A mensagem do erro é o que diz qual dos três é."""
    detalhe = " ".join(str(exc).split())[:400] or type(exc).__name__
    return (
        "**Não consegui falar com a IA agora.**\n\n"
        f"`{type(exc).__name__}: {detalhe}`\n\n"
        "Se falar em *authentication*, a chave está errada ou não chegou ao app. "
        "Se falar em *credit* ou *billing*, falta saldo na organização. "
        "Se falar em *model*, o nome do modelo mudou e eu ajusto no código."
    )


def _recado_sem_texto(resposta) -> str:
    """Voltou sem texto: dizer o motivo, que é o que permite corrigir."""
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


def em_fluxo(prompt: str, modelo: str, max_tokens: int, esforco: str = "medium"):
    """A mesma resposta, em pedaços, conforme o modelo escreve.

    Esperar calado por uma análise longa é ruim de duas maneiras: parece
    travado, e enquanto o script fica parado o Streamlit mantém na tela o
    esqueleto apagado da página anterior. Devolvendo pedaço a pedaço, o texto
    aparece enquanto é escrito e a tela se refaz no primeiro deles.

    Devolve sempre pelo menos um pedaço: em caso de erro, o recado do erro —
    que `falhou()` reconhece do mesmo jeito.
    """
    if not disponivel():
        yield SEM_CHAVE
        return
    parametros = dict(
        model=modelo,
        max_tokens=max_tokens,
        output_config={"effort": esforco},
        messages=[{"role": "user", "content": prompt}],
    )
    cliente = _cliente()
    abrir = getattr(cliente.messages, "stream", None)
    if abrir is None:
        # SDK antigo: uma resposta só, sem pedaços
        yield _perguntar(prompt, modelo, max_tokens, esforco)
        return
    try:
        try:
            corrente = abrir(**parametros)
        except Exception:
            corrente = abrir(**{k: v for k, v in parametros.items() if k != "output_config"})
        with corrente as fluxo:
            vazio = True
            for pedaco in fluxo.text_stream:
                if pedaco:
                    vazio = False
                    yield pedaco
            if vazio:
                yield _recado_sem_texto(fluxo.get_final_message())
    except Exception as exc:
        yield _recado_de_erro(exc)


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


def _prompt_do_mes(contexto: str) -> str:
    """A leitura do mês, escrita a partir do resumo numérico já apurado."""
    return (
        "Você é o analista que acompanha as contas desta casa brasileira e escreve a "
        "leitura do mês para o casal que a mantém. Eles já viram os totais na tela: o "
        "que esperam de você é o que os totais não dizem.\n\n"
        "Escreva em markdown, com estes títulos, nesta ordem:\n"
        "### O mês em três linhas\n"
        "O veredito: mês caro ou barato contra a média, por causa de quê, e o que sobrou. "
        "Três frases, cada uma com número.\n"
        "### Para onde foi o dinheiro\n"
        "As categorias que explicam o mês, da maior para a menor, com a subcategoria que "
        "puxou cada uma e a comparação com o mês anterior. Pare quando as seguintes "
        "virarem ruído.\n"
        "### O que fugiu do padrão\n"
        "Use a seção 'Fora do padrão' e a comparação com o mês anterior. Para cada desvio, "
        "diga se foi um lançamento só ou um comportamento — a lista das maiores saídas e a "
        "de gasto novo respondem isso. Um desvio explicado por uma compra única não é "
        "tendência, e dizer isso vale mais do que o alarme.\n"
        "### Piso e escolha\n"
        "Quanto do mês era compromisso já assumido e quanto era decisão do mês. É o que "
        "separa o que dá para mudar amanhã do que só muda cancelando algo.\n"
        "### Três decisões que valem dinheiro\n"
        "Ranqueadas pelo que liberam POR MÊS, com o valor de cada uma e o que custa em "
        "troca. Prefira compromisso recorrente a gasto avulso. Se alguma exigir uma "
        "informação que não está nos números, diga qual.\n"
        "### O mês dentro do ano\n"
        "Onde este mês fica na série, o que ele faz com a média e o que o ritmo atual "
        "projeta se continuar.\n"
        "### O que ainda não dá para afirmar\n"
        "O que falta classificar ou importar para as conclusões acima ficarem firmes. Se "
        "não faltar nada, diga isso em uma linha.\n\n"
        f"{REGRAS}\n\n{contexto}"
    )


def _prompt_longo(contexto: str, rotulo: str) -> str:
    """A leitura da série: o que é piso, o que é escolha, o que decidir."""
    return (
        f"Você é o analista que acompanha as contas desta casa brasileira. Leia a série "
        f"de meses abaixo e escreva a leitura de {rotulo} para o casal que a mantém. "
        "Eles já viram os totais: o que esperam de você é a leitura da série — o que se "
        "repete, o que oscila, o que mudou de patamar e o que isso obriga a decidir.\n\n"
        "Escreva em markdown, com estes títulos, nesta ordem:\n"
        "### O retrato do período\n"
        "Quanto entrou, quanto saiu, quanto sobrou, e se a trajetória melhora ou piora ao "
        "longo dos meses. Diga a proporção entre gasto e renda.\n"
        "### A trajetória mês a mês\n"
        "Os meses que destoam e por quê, usando a matriz de categorias. Distinga o mês "
        "caro por um evento único do mês caro por patamar novo — a diferença decide se "
        "vale reagir.\n"
        "### O piso do orçamento\n"
        "O que se repete e quanto custa por mês. Este é o número que o casal precisa "
        "cobrir todo mês aconteça o que acontecer; diga que porcentagem da renda ele "
        "consome, e quais compromissos subiram dentro do período.\n"
        "### O que oscila, e quando\n"
        "Aponte concentração com o número: 'quase tudo de Lazer & Viagens está em dois "
        "meses'. Só chame de sazonalidade o que se repetir no mesmo mês em anos "
        "diferentes; havendo um ano só, diga que ainda é cedo.\n"
        "### Onde o dinheiro foi parar\n"
        "Use a lista por estabelecimento. Categoria é tipo de gasto; estabelecimento é "
        "decisão — e é onde a conversa sobre cortar acontece.\n"
        "### Quem trouxe o quê, e de onde\n"
        "A composição da receita, por pessoa e por fonte, e o que ela tem de frágil: "
        "concentração numa fonte só, entrada única tratada como renda, previsão "
        "convivendo com extrato.\n"
        "### Cinco decisões para os próximos doze meses\n"
        "Ranqueadas pelo que liberam por ano, cada uma com o valor e o que custa em "
        "troca. Prefira o que se repete ao que é avulso.\n"
        "### Metas sugeridas\n"
        "Em percentual da renda, categoria por categoria, a partir do que realmente "
        "aconteceu — e não de um ideal. Diga onde a meta sugerida é mais apertada que o "
        "histórico e quanto isso exige por mês.\n"
        "### O que ainda não dá para afirmar\n"
        "Falta de dado classificado, de histórico ou de mês fechado.\n\n"
        f"{REGRAS}\n\n{contexto}"
    )


# espaço de resposta. É o raciocínio que consome a maior parte dele; o texto
# final tem uns três mil. Com o esforço médio a leitura sai em pouco mais de um
# minuto, e é o contexto — não o esforço — que trouxe a granularidade que
# faltava: a versão anterior tinha metade dos números e esforço alto.
ESPACO_DO_MES = 16000
ESPACO_LONGO = 20000
ESFORCO = "medium"


def analisar_mes(contexto: str, modelo: str = MODELO_ANALISE) -> str:
    if not disponivel():
        return SEM_CHAVE
    return _perguntar(_prompt_do_mes(contexto), modelo, ESPACO_DO_MES, ESFORCO)


def analisar_mes_em_fluxo(contexto: str, modelo: str = MODELO_ANALISE):
    return em_fluxo(_prompt_do_mes(contexto), modelo, ESPACO_DO_MES, ESFORCO)


def analisar_ano(contexto: str, modelo: str = MODELO_ANALISE, rotulo: str = "o período") -> str:
    """A leitura longa: padrão, piso do orçamento e o que decide o próximo ano.

    Pergunta diferente da do mês, e por isso vale uma chamada própria. O mês
    responde "para onde foi o dinheiro"; só a série responde "isto se repete",
    e é dela que sai meta — não do último mês.
    """
    if not disponivel():
        return SEM_CHAVE
    return _perguntar(_prompt_longo(contexto, rotulo), modelo, ESPACO_LONGO, ESFORCO)


def analisar_ano_em_fluxo(contexto: str, modelo: str = MODELO_ANALISE,
                          rotulo: str = "o período"):
    return em_fluxo(_prompt_longo(contexto, rotulo), modelo, ESPACO_LONGO, ESFORCO)


def _prompt_da_pergunta(contexto: str, pergunta: str, rotulo: str) -> str:
    return (
        f"Responda à pergunta do casal sobre as contas da casa, olhando {rotulo}, "
        "usando apenas os números abaixo. Vá direto ao ponto: comece pela resposta, "
        "com o número que a sustenta, e só depois explique. Se a pergunta pedir uma "
        "conta que dá para fazer com os números fornecidos, faça e mostre as parcelas. "
        "Se a resposta não estiver neles, diga exatamente o que falta classificar ou "
        "importar para que ela possa ser respondida — e não invente nada no lugar.\n\n"
        f"{REGRAS}\n\n"
        f"PERGUNTA: {pergunta.strip()}\n\n{contexto}"
    )


def responder_pergunta(contexto: str, pergunta: str, modelo: str = MODELO_ANALISE,
                       rotulo: str = "este mês") -> str:
    """Pergunta livre sobre o período, respondida só com os números do contexto.

    Vale mais que a análise pronta quando a dúvida é específica ("por que
    agosto ficou tão caro?", "quanto a casa gasta de fixo por ano?"). A janela
    é escolhida na tela: mês, ano civil ou últimos doze meses — a mesma
    pergunta tem respostas diferentes em cada uma, e é o dono quem sabe qual
    quer. A trava é a mesma: o que não está nos números não pode ser
    respondido, e dizer "isto não está nos dados" é uma resposta melhor do que
    uma frase plausível.
    """
    if not disponivel():
        return SEM_CHAVE
    return _perguntar(_prompt_da_pergunta(contexto, pergunta, rotulo), modelo, 12000, ESFORCO)


def responder_pergunta_em_fluxo(contexto: str, pergunta: str,
                                modelo: str = MODELO_ANALISE, rotulo: str = "este mês"):
    return em_fluxo(_prompt_da_pergunta(contexto, pergunta, rotulo), modelo, 12000, ESFORCO)
