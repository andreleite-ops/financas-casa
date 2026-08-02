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
MODELO_ANALISE = "claude-sonnet-5"
LOTE = 40


def _chave_api() -> str | None:
    chave = os.environ.get("ANTHROPIC_API_KEY")
    if chave:
        return chave
    try:
        import streamlit as st

        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return None


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
        dados = _extrair_json(resposta.content[0].text)
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


def analisar_mes(contexto: str, modelo: str = MODELO_ANALISE) -> str:
    """Texto da tela Analise IA a partir do resumo numerico ja calculado."""
    if not disponivel():
        return (
            "**Análise por IA não configurada.**\n\n"
            "Para ligar, adicione `ANTHROPIC_API_KEY` em `.streamlit/secrets.toml`. "
            "Todo o resto do sistema funciona sem ela — os números das outras telas "
            "não dependem da IA."
        )
    prompt = (
        "Você é o consultor financeiro de um casal brasileiro (André e Rô) que controla "
        "as contas da casa. Analise os números abaixo e escreva em português do Brasil, "
        "em no máximo 5 parágrafos curtos, com números concretos:\n"
        "1) onde o dinheiro está indo neste mês;\n"
        "2) o que fugiu do padrão em relação aos meses anteriores;\n"
        "3) 3 sugestões práticas e específicas de economia, com o valor que cada uma "
        "liberaria por mês;\n"
        "4) como está a poupança em relação à meta.\n"
        "Seja direto e evite conselhos genéricos. Não invente dados que não estão abaixo.\n\n"
        f"{contexto}"
    )
    try:
        resposta = _cliente().messages.create(
            model=modelo, max_tokens=1600, messages=[{"role": "user", "content": prompt}]
        )
        return resposta.content[0].text
    except Exception as exc:
        return f"Não consegui gerar a análise agora ({type(exc).__name__}). Tente novamente."
