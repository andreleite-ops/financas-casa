"""Popula a base com dados ficticios para ver o app funcionando.

Nao e teste automatizado - e o "modo demonstracao" para o Andre e a Ro
navegarem pelas telas antes de existir arquivo real. Use com um banco
descartavel:

    DATABASE_URL="sqlite:///demo.db" python scripts/gerar_demo.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db, repo, seed  # noqa: E402
from parsers.base import Lancamento  # noqa: E402

SEMENTE = 20260802

# (descricao, valor minimo, valor maximo, vezes por mes)
# Fatura de cartao: tudo aqui e gasto, entao o sinal vira negativo na geracao.
GASTOS_CARTAO = [
    ("IFOOD *RESTAURANTE {n}", 4500, 14000, 9),
    ("UBER *TRIP", 1800, 6500, 12),
    ("SUPERM PAO DE ACUCAR", 18000, 95000, 4),
    ("DROGARIA RAIA {n}", 3500, 22000, 3),
    ("NETFLIX.COM", 5590, 5590, 1),
    ("SPOTIFY", 3490, 3490, 1),
    ("POSTO IPIRANGA {n}", 15000, 32000, 2),
    ("CINEMARK", 4800, 12000, 1),
    ("RESTAURANTE FASANO", 18000, 52000, 1),
    ("AMAZON BR", 4900, 38000, 3),
    ("PETZ LOJA {n}", 8000, 26000, 1),
    ("SEPHORA BR", 12000, 45000, 1),
    ("ESTAPAR ESTACIONAMENTO", 1500, 4500, 4),
    ("PADARIA STELLA", 2200, 9800, 6),
    ("CLINICA VERTEX", 25000, 62000, 1),
    ("LATAM AIRLINES", 80000, 320000, 0),  # so em alguns meses
    ("PAG*ServicosGerais {n}", 5000, 30000, 2),  # cai na fila manual
]

CONTA_CORRENTE_ANDRE = [
    ("PRO LABORE EMPRESA A", 2700000, 2700000, 1),
    ("CONDOMINIO EDIFICIO", -420000, -420000, 1),
    ("ENEL DISTRIBUICAO", -38000, -72000, 1),
    ("SABESP", -12000, -22000, 1),
    ("VIVO FIBRA", -19990, -19990, 1),
    ("UNIMED MENSALIDADE", -280000, -280000, 1),
    ("APLICACAO CDB AUTOMATICA", -900000, -900000, 1),
    ("TARIFA PACOTE DE SERVICOS", -4900, -4900, 1),
]

CONTA_CORRENTE_RO = [
    ("SALARIO EMPRESA B", 1450000, 1450000, 1),
    ("NF SERVICO CONSULTORIA", 180000, 320000, 1),
    ("COLEGIO SANTA MARIA", -190000, -190000, 1),
    ("SMARTFIT ACADEMIA", -12990, -12990, 1),
    ("PIX TRANSF MARIA S", -25000, -80000, 2),  # cai na fila manual
]


def _lancamentos_do_mes(
    modelo, ano: int, mes: int, rng: random.Random, so_saida: bool = False
) -> list[Lancamento]:
    """so_saida=True para fatura de cartao, onde todo lancamento e gasto."""
    saida: list[Lancamento] = []
    ultimo_dia = (date(ano + (mes == 12), (mes % 12) + 1, 1) - timedelta(days=1)).day
    for descricao, minimo, maximo, vezes in modelo:
        repeticoes = vezes
        if descricao.startswith("LATAM"):
            repeticoes = 1 if mes in (4, 7, 12) else 0
        for i in range(repeticoes):
            valor = rng.randint(min(minimo, maximo), max(minimo, maximo))
            valor = -abs(valor) if (so_saida or minimo < 0) else abs(valor)
            dia = rng.randint(1, ultimo_dia)
            saida.append(
                Lancamento(
                    data=date(ano, mes, dia),
                    descricao=descricao.format(n=rng.randint(100, 999)),
                    valor_centavos=valor,
                )
            )
    return saida


def gerar(meses: int = 18) -> dict:
    rng = random.Random(SEMENTE)
    engine = db.get_engine()
    seed.semear(engine)

    with engine.connect() as conn:
        contas = {c["nome"]: c["id"] for c in repo.listar_contas(conn)}

    hoje = date.today()
    total = {"lidos": 0, "importados": 0, "auto": 0, "pendentes": 0}
    ano, mes = hoje.year, hoje.month

    periodos = []
    for _ in range(meses):
        periodos.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    periodos.reverse()

    for ano, mes in periodos:
        competencia = f"{ano:04d}-{mes:02d}"
        for nome_conta, modelo, cartao in (
            ("Visa XP", GASTOS_CARTAO, True),
            ("Nubank Mastercard", GASTOS_CARTAO[:8], True),
            ("Bradesco C/C", CONTA_CORRENTE_ANDRE, False),
            ("Itaú C/C", CONTA_CORRENTE_RO, False),
        ):
            conta_id = contas.get(nome_conta)
            if not conta_id:
                continue
            lancamentos = _lancamentos_do_mes(modelo, ano, mes, rng, so_saida=cartao)
            if not lancamentos:
                continue
            resumo = repo.importar(
                engine,
                conta_id=conta_id,
                lancamentos=lancamentos,
                arquivo=f"demo_{nome_conta.lower().replace(' ', '_')}_{competencia}.csv",
                usuario="André" if "Bradesco" in nome_conta or "Visa" in nome_conta else "Rô",
                competencia=competencia,
                usar_ia=False,
            )
            for chave in total:
                total[chave] += resumo.get(chave, 0)
    return total


if __name__ == "__main__":
    resultado = gerar()
    print(
        f"Demo gerada: {resultado['lidos']} lançamentos lidos, "
        f"{resultado['importados']} importados, {resultado['auto']} classificados "
        f"automaticamente, {resultado['pendentes']} na fila manual."
    )
    print(f"Banco: {db.url_do_banco()}")
