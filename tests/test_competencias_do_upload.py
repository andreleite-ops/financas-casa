"""A lista de meses da tela de upload.

Ela começava no mês de hoje e só andava para trás. Fatura de cartão fecha num
mês e vence no seguinte — a compra de agosto entra na fatura de setembro, e é
setembro a competência dela —, então o upload mais comum da casa era
justamente o que não tinha como ser feito.
"""

from __future__ import annotations

from datetime import date

from views.upload import MESES_A_FRENTE, MESES_PARA_TRAS, _competencias_sugeridas


def _hoje() -> str:
    return date.today().strftime("%Y-%m")


def test_oferece_meses_a_frente_do_atual():
    """O mês que vem tem de estar lá: é a competência da fatura que vence nele."""
    opcoes = _competencias_sugeridas()
    hoje = _hoje()
    assert opcoes.index(hoje) == MESES_A_FRENTE          # há meses acima do de hoje
    assert opcoes[0] > hoje


def test_abre_no_mes_de_hoje_e_nao_no_primeiro_da_lista():
    """Os meses à frente existem para o cartão; o padrão continua sendo hoje."""
    assert _competencias_sugeridas()[MESES_A_FRENTE] == _hoje()


def test_vai_fundo_para_tras_tambem():
    """Histórico não tem prazo: extrato antigo, ano fechado que se resolve pegar."""
    opcoes = _competencias_sugeridas()
    assert len(opcoes) == MESES_A_FRENTE + MESES_PARA_TRAS
    assert opcoes[-1] < f"{date.today().year - 4}-01"


def test_lista_e_continua_do_mais_novo_para_o_mais_antigo():
    """Sem buraco e sem repetição — e ordenada, que é como se procura um mês."""
    opcoes = _competencias_sugeridas()
    assert opcoes == sorted(set(opcoes), reverse=True)
    for anterior, seguinte in zip(opcoes, opcoes[1:]):
        ano_a, mes_a = int(anterior[:4]), int(anterior[5:])
        ano_s, mes_s = int(seguinte[:4]), int(seguinte[5:])
        assert (ano_a * 12 + mes_a) - (ano_s * 12 + mes_s) == 1


def test_virada_de_ano_nao_produz_mes_zero():
    """A conta de meses era feita à mão e é onde nasce o "2026-00"."""
    from views.upload import _passo_de_mes

    assert _passo_de_mes(2026, 1, -1) == (2025, 12)
    assert _passo_de_mes(2026, 12, +1) == (2027, 1)
    assert _passo_de_mes(2026, 8, +24) == (2028, 8)
    assert _passo_de_mes(2026, 8, -60) == (2021, 8)
