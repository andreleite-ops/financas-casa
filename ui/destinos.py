"""Categoria e subcategoria como uma escolha só.

Duas telas precisam do mesmo campo: a fila de classificação, onde o
lançamento ainda não tem destino, e a revisão de subcategorias da Análise IA,
onde ele tem categoria mas o grupo às vezes está errado. Escrever a lista nos
dois lugares faria a mesma escolha aparecer com dois vocabulários — e o
segundo lugar, mais tarde, esqueceria de filtrar por natureza.
"""

from __future__ import annotations

Destino = tuple[int, int | None]


def rotulo(categoria: str, subcategoria: str | None = None) -> str:
    """Como o destino aparece na lista: a categoria, e a subcategoria abaixo."""
    return f"    ↳ {categoria} › {subcategoria}" if subcategoria else categoria


def do_plano(plano, natureza: str, primeiro: int | None = None) -> dict[str, Destino]:
    """Rótulo legível -> (categoria_id, subcategoria_id), achatado num nível só.

    A categoria aparece sozinha e depois cada subcategoria dela, indentada. O
    destino inteiro vira uma escolha só: quem quer o detalhe pega a
    subcategoria, quem não quer para na categoria — e nos dois casos o
    lançamento sai da fila.

    `primeiro` traz o bloco de uma categoria para o topo. Serve à revisão de
    subcategorias, onde quase sempre a categoria está certa e o que falta é o
    detalhe dela: o caso comum fica à mão sem esconder o resto do plano, que é
    justamente o que se precisa quando o grupo veio errado.
    """
    blocos: dict[int, dict[str, Destino]] = {}
    for categoria in plano:
        if categoria["natureza"] != natureza or not categoria["ativa"]:
            continue
        bloco: dict[str, Destino] = {rotulo(categoria["nome"]): (categoria["id"], None)}
        for sub in categoria["subcategorias"]:
            if sub["ativa"]:
                bloco[rotulo(categoria["nome"], sub["nome"])] = (categoria["id"], sub["id"])
        blocos[categoria["id"]] = bloco

    ordem = list(blocos)
    if primeiro in blocos:
        ordem.remove(primeiro)
        ordem.insert(0, primeiro)

    saida: dict[str, Destino] = {}
    for categoria_id in ordem:
        saida.update(blocos[categoria_id])
    return saida
