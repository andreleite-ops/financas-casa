"""Testes de parsers.pdf: extrair_linhas() recebe texto puro (sem gerar PDF)."""

from __future__ import annotations

from datetime import date

import pytest

from parsers.base import ErroDeLeitura
from parsers.pdf import extrair_linhas, texto_do_pdf


def test_linha_tipica_de_fatura_vira_lancamento_negativo():
    lancamentos, ignoradas = extrair_linhas(
        "12/07 IFOOD *RESTAURANTE 86,40", competencia="2026-07"
    )
    assert len(lancamentos) == 1
    lan = lancamentos[0]
    assert lan.data == date(2026, 7, 12)
    assert lan.descricao == "IFOOD *RESTAURANTE"
    assert lan.valor_centavos == -8640
    assert ignoradas == []


def test_linha_de_estorno_vira_positivo():
    lancamentos, _ = extrair_linhas(
        "14/07 ESTORNO SUPERMERCADO XYZ 45,90", competencia="2026-07"
    )
    assert len(lancamentos) == 1
    assert lancamentos[0].valor_centavos == 4590


def test_linha_de_ruido_total_da_fatura_e_ignorada():
    # com data na frente, a linha casa o padrao de lancamento mas e
    # reconhecida como ruido (RUIDO) e vai para a lista de ignoradas.
    lancamentos, ignoradas = extrair_linhas(
        "31/07 TOTAL DA FATURA 3.400,00", competencia="2026-07"
    )
    assert lancamentos == []
    assert len(ignoradas) == 1
    assert "TOTAL DA FATURA" in ignoradas[0]


def test_linha_de_ruido_sem_data_na_frente_nao_gera_lancamento():
    # sem data no inicio a linha nem casa o regex de lancamento: some
    # silenciosamente, sem virar lancamento.
    lancamentos, _ = extrair_linhas("TOTAL DA FATURA 3.400,00", competencia="2026-07")
    assert lancamentos == []


def test_linha_sem_cara_de_lancamento_e_ignorada():
    lancamentos, ignoradas = extrair_linhas(
        "Consulte condições no site do banco", competencia="2026-07"
    )
    assert lancamentos == []
    assert ignoradas == []


def test_marca_c_no_fim_forca_entrada():
    lancamentos, _ = extrair_linhas(
        "18/07 COMPRA NORMAL LOJA 30,00 C", competencia="2026-07"
    )
    assert len(lancamentos) == 1
    assert lancamentos[0].valor_centavos == 3000  # positivo, apesar de nao ser estorno


def test_marca_d_no_fim_forca_saida_mesmo_com_palavra_estorno():
    lancamentos, _ = extrair_linhas(
        "17/07 ESTORNO COMPRA CANCELADA 25,00 D", competencia="2026-07"
    )
    assert len(lancamentos) == 1
    assert lancamentos[0].valor_centavos == -2500  # marca D vence a palavra ESTORNO


def test_multiplas_linhas_mistura_lancamentos_e_ruido():
    texto = "\n".join(
        [
            "12/07 IFOOD *RESTAURANTE 86,40",
            "31/07 TOTAL DA FATURA 3.400,00",
            "14/07 ESTORNO SUPERMERCADO XYZ 45,90",
            "Consulte condições no site do banco",
        ]
    )
    lancamentos, ignoradas = extrair_linhas(texto, competencia="2026-07")
    assert len(lancamentos) == 2
    assert len(ignoradas) == 1


def test_texto_do_pdf_levanta_erro_de_leitura_com_bytes_invalidos():
    with pytest.raises(ErroDeLeitura):
        texto_do_pdf(b"isto definitivamente nao e um PDF")
