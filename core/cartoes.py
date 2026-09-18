"""O pagamento da fatura, reconhecido pelo que o sistema ja sabe.

As compras do cartao ja sao despesa na fatura. O debito que as paga, na conta
corrente, e so o dinheiro mudando de bolso — somado como despesa, o mes paga o
cartao duas vezes. Regra de texto nao resolve isso em definitivo: cada banco
escreve o pagamento de um jeito, e cada redacao nova e um furo.

O que resolve sao duas coisas que o cadastro e o historico ja tem:

1. **Quais cartoes existem.** Um debito na conta corrente que cita o emissor
   de um cartao cadastrado (Nubank, XP, BTG) e o pagamento dele.
2. **Quanto cada fatura deu.** Um debito cujo valor bate com o total de uma
   fatura ja importada e o pagamento dela — seja qual for o texto.

E, como terceira rede, as redacoes genericas de pagamento de cartao.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import sqlalchemy as sa

from . import db
from .analytics import CATEGORIA_TRANSFERENCIA
from .texto import normalizar

# como o emissor aparece no extrato de outro banco, alem do proprio nome
ALIASES = {
    "NUBANK": ("NU PAGAMENTOS", "NU FINANCEIRA"),
    "XP": ("XP INVESTIMENTOS", "XP VISA", "VISA XP", "BANCO XP"),
    "BTG": ("BTG PACTUAL", "BANCO BTG"),
    "ITAU": ("ITAUCARD",),
    "BRADESCO": ("BRADESCARD",),
}

# nomes curtos demais para serem procurados soltos numa descricao
_MINIMO_DE_LETRAS = 4

_REDACAO = re.compile(
    r"(PAG|PGTO|PAGTO|PAGAMENTO)\S*\s.*(CART|FATURA)|FATURA.*CART|CARTAO DE CREDITO",
)

# quanto o debito pode diferir do total da fatura e ainda ser o pagamento dela:
# um real, ou meio por cento — juros de um dia de atraso, arredondamento
FOLGA_CENTAVOS = 100
FOLGA_RELATIVA = 0.005


def emissores(conn) -> list[dict]:
    """Os cartoes cadastrados, com os nomes pelos quais aparecem no extrato."""
    saida = []
    for c in conn.execute(
        sa.select(db.contas.c.id, db.contas.c.nome, db.contas.c.instituicao)
        .where(db.contas.c.tipo == "cartao")
    ):
        base = normalizar(c.instituicao).strip()
        nomes = {n for n in (base, *ALIASES.get(base, ())) if len(n) >= _MINIMO_DE_LETRAS}
        nomes |= set(ALIASES.get(base, ()))
        saida.append({"id": c.id, "nome": c.nome, "tokens": sorted(nomes)})
    return saida


def totais_de_fatura(conn) -> dict[tuple[int, str], int]:
    """Quanto cada fatura importada deu: (cartao, competencia) -> centavos a pagar.

    O total e o que esta na fatura fora das transferencias — as compras menos
    os estornos. O proprio pagamento da fatura anterior, quando aparece nela,
    e transferencia e fica de fora.
    """
    # a fatura e o arquivo: as compras dela contam cada uma no proprio mes,
    # entao o total sai do upload, e o mes da fatura e o que o upload guardou
    mes_da_fatura = sa.func.coalesce(db.uploads.c.competencia, db.transacoes.c.competencia)
    consulta = (
        sa.select(
            db.transacoes.c.conta_id, mes_da_fatura.label("competencia"),
            sa.func.sum(db.transacoes.c.valor_centavos).label("total"),
        )
        .select_from(
            db.transacoes
            .join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
            .outerjoin(db.uploads, db.transacoes.c.upload_id == db.uploads.c.id)
        )
        .where(
            db.contas.c.tipo == "cartao",
            db.transacoes.c.ativo == sa.true(),
            sa.or_(db.categorias.c.nome.is_(None),
                   db.categorias.c.nome != CATEGORIA_TRANSFERENCIA),
        )
        .group_by(db.transacoes.c.conta_id, mes_da_fatura)
    )
    totais: dict[tuple[int, str], int] = {}
    for l in conn.execute(consulta):
        chave = (l.conta_id, l.competencia)
        totais[chave] = totais.get(chave, 0) - int(l.total or 0)
    return totais


# quantos dias entre o debito na conta corrente e o "pagamento recebido" que
# a fatura imprime: o boleto pago hoje aparece no cartao hoje ou amanha
JANELA_DO_PAGAMENTO = timedelta(days=5)


def pagamentos_recebidos(conn) -> list[dict]:
    """Os creditos de pagamento que as faturas ja importadas trazem.

    A fatura de agosto imprime "Pagamento recebido" com o valor que pagou a de
    julho. E o dado que a fatura de julho daria — sem precisar da fatura de
    julho. E o que fecha a transicao da planilha para os extratos: o mes cujas
    compras estao na planilha e cujo pagamento esta no extrato do banco.
    """
    consulta = (
        sa.select(db.transacoes.c.conta_id, db.contas.c.nome, db.transacoes.c.data,
                  db.transacoes.c.valor_centavos)
        .select_from(
            db.transacoes
            .join(db.contas, db.transacoes.c.conta_id == db.contas.c.id)
            .outerjoin(db.categorias, db.transacoes.c.categoria_id == db.categorias.c.id)
        )
        .where(
            db.contas.c.tipo == "cartao",
            db.transacoes.c.ativo == sa.true(),
            db.transacoes.c.valor_centavos > 0,
            sa.or_(db.categorias.c.nome == CATEGORIA_TRANSFERENCIA,
                   db.transacoes.c.descricao_norm.like("%PAGAMENTO%")),
        )
    )
    return [
        {"conta_id": l.conta_id, "nome": l.nome, "data": l.data, "valor": int(l.valor_centavos)}
        for l in conn.execute(consulta)
    ]


def _meses_vizinhos(competencia: str) -> list[str]:
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    saida = []
    for passo in (-1, 0, 1):
        m = mes + passo
        a = ano
        if m == 0:
            a, m = ano - 1, 12
        elif m == 13:
            a, m = ano + 1, 1
        saida.append(f"{a:04d}-{m:02d}")
    return saida


def reconhecer(
    descricao: str, valor_centavos: int, competencia: str, *,
    emissores_cadastrados: list[dict], totais: dict[tuple[int, str], int],
    data: date | None = None, recebidos: list[dict] | None = None,
) -> str | None:
    """Por que este debito e o pagamento de um cartao — ou None.

    So olha debito (valor negativo). Devolve o motivo em texto, para ficar na
    observacao do lancamento: e o que permite conferir depois por que ele nao
    esta nas despesas.
    """
    if valor_centavos >= 0:
        return None
    texto = normalizar(descricao)
    pago = -valor_centavos

    # 0) o cartao diz que recebeu este valor nestes dias: e o pagamento dele.
    #    Nao precisa da fatura do mes anterior — a fatura seguinte ja imprime
    #    o "pagamento recebido" com o valor
    if data is not None:
        for rec in recebidos or ():
            folga = max(FOLGA_CENTAVOS, int(rec["valor"] * FOLGA_RELATIVA))
            if abs(rec["valor"] - pago) <= folga and abs(rec["data"] - data) <= JANELA_DO_PAGAMENTO:
                return f"pagamento da fatura {rec['nome']} (recebido em {rec['data']:%d/%m})"

    # 1) o valor bate com o total de uma fatura importada, deste mes ou vizinho
    for emissor in emissores_cadastrados:
        for mes in _meses_vizinhos(competencia):
            total = totais.get((emissor["id"], mes))
            if not total or total <= 0:
                continue
            folga = max(FOLGA_CENTAVOS, int(total * FOLGA_RELATIVA))
            if abs(total - pago) <= folga:
                return f"pagamento da fatura {emissor['nome']} de {mes}"

    # 2) cita o emissor de um cartao cadastrado — e nao e um PIX para alguem
    #    que por acaso tem conta la
    if "PIX" not in texto:
        for emissor in emissores_cadastrados:
            if any(token in texto for token in emissor["tokens"]):
                return f"pagamento de cartão {emissor['nome']}"

    # 3) a redacao generica de pagamento de cartao
    if _REDACAO.search(texto):
        return "pagamento de fatura de cartão"
    return None
