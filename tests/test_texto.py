"""Testes de core.texto: normalizacao de descricoes de extrato."""

from __future__ import annotations

from core.texto import chave_estabelecimento, normalizar, parcela_de, sem_acento


# --------------------------------------------------------------------------
# normalizar
# --------------------------------------------------------------------------
def test_normalizar_remove_acento_e_deixa_maiuscula():
    assert normalizar("Pádaria Zé") == "PADARIA ZE"


def test_normalizar_remove_prefixo_pag():
    assert normalizar("PAG*Clinica Vertex") == "CLINICA VERTEX"


def test_normalizar_remove_prefixo_mercpago():
    assert normalizar("MERCPAGO*Uber Trip") == "UBER TRIP"


def test_normalizar_remove_prefixo_ec():
    assert normalizar("EC *CLINICA VERTEX RJ") == "CLINICA VERTEX RJ"


def test_normalizar_vazio():
    assert normalizar("") == ""
    assert normalizar(None) == ""


# --------------------------------------------------------------------------
# chave_estabelecimento
# --------------------------------------------------------------------------
def test_chave_estabelecimento_colapsa_datas_diferentes():
    chave_1 = chave_estabelecimento("UBER *TRIP 12/07")
    chave_2 = chave_estabelecimento("UBER *TRIP 28/07")
    assert chave_1 == chave_2
    assert chave_1 == "UBER TRIP"


def test_chave_estabelecimento_colapsa_parcelas_diferentes():
    chave_1 = chave_estabelecimento("MAGAZINE LUIZA PARC 01/12")
    chave_2 = chave_estabelecimento("MAGAZINE LUIZA PARC 07/12")
    assert chave_1 == chave_2


def test_chave_estabelecimento_remove_codigo_de_terminal():
    chave = chave_estabelecimento("PAG*CLINICA VERTEX 044223198")
    assert "044223198" not in chave
    assert chave == "CLINICA VERTEX"


def test_chave_estabelecimento_vazia_para_string_vazia():
    assert chave_estabelecimento("") == ""


# --------------------------------------------------------------------------
# parcela_de
# --------------------------------------------------------------------------
def test_parcela_de_reconhece_parcelamento():
    assert parcela_de("COMPRA LOJA PARC 03/12") == (3, 12)


def test_parcela_de_reconhece_sem_a_palavra_parc():
    assert parcela_de("MAGAZINE LUIZA 05/10") == (5, 10)


def test_parcela_de_nenhuma_parcela():
    assert parcela_de("PADARIA CENTRAL") is None


def test_parcela_de_ignora_fracao_invalida():
    # parcela maior que o total, ou total fora do range plausivel, nao conta
    assert parcela_de("QUALQUER COISA 12/03") is None  # atual > total
    assert parcela_de("CODIGO 99/99") is None  # total > 48


def test_sem_acento_utilitario():
    assert sem_acento("São Paulo — Ação") == "Sao Paulo — Acao"


# --------------------------------------------------------------------------
# o que vai para a tela: markdown e HTML sao duas feridas diferentes
# --------------------------------------------------------------------------
def test_sem_marcacao_protege_markdown_e_html():
    """"PCART*TAB*SAO PAULO" aparecia com TAB em italico, e um pagador
    chamado "A<B LTDA" fazia o navegador engolir o resto do cartao."""
    from core.texto import sem_marcacao

    assert sem_marcacao("PCART*TAB*SP") == r"PCART\*TAB\*SP"
    assert sem_marcacao("A<B LTDA") == "A&lt;B LTDA"
    assert sem_marcacao("CASA & CIA") == "CASA &amp; CIA"
    # o cifrao e o pior: um par deles vira formula e a tela come o meio
    assert sem_marcacao("R$ 10 a R$ 20") == r"R\$ 10 a R\$ 20"


def test_sem_marcacao_escapa_o_e_comercial_antes_do_resto():
    """Escapar na ordem errada transforma "&" em "&amp;amp;" na tela."""
    from core.texto import sem_marcacao

    assert sem_marcacao("A & <B>") == "A &amp; &lt;B&gt;"


def test_so_html_nao_mete_contrabarra_em_html_de_bloco():
    """Dentro de uma <table> o Streamlit desliga o markdown: ali a contrabarra
    que protegeria o asterisco apareceria na tela como contrabarra mesmo."""
    from core.texto import so_html

    assert so_html("PCART*TAB*SP") == "PCART*TAB*SP"
    assert so_html("A<B & C") == "A&lt;B &amp; C"


def test_so_html_fecha_a_porta_do_atributo():
    """"Casa d'Alimentacao" dentro de title='...' fechava o atributo no
    apostrofo, e o resto do nome virava atributo do elemento."""
    from core.texto import so_html

    assert so_html("Casa d'Alimentacao") == "Casa d&#39;Alimentacao"
    assert so_html('Bar "do Ze"') == "Bar &quot;do Ze&quot;"


def test_nenhum_dos_dois_quebra_com_vazio():
    from core.texto import sem_marcacao, so_html

    assert sem_marcacao(None) == ""
    assert so_html(None) == ""
