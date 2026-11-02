"""Nome de categoria e de conta são texto do dono, e vão direto para a tela.

As telas desenham HTML — tabela de categoria × mês, barra de meta, cartão de
conta. Um nome com "<", "&" ou apóstrofo quebrava a página em volta dele: o
navegador engolia o resto da linha como se fosse tag aberta, e "Casa
d'Alimentação" dentro de title='...' fechava o atributo no apóstrofo.

Dois escapes diferentes de propósito. Em HTML de bloco (<table>, <div>) o
Streamlit desliga o markdown, e a contrabarra que protegeria o asterisco
apareceria na tela como contrabarra — ali vale `so_html`. Em recado com
markdown e um pouco de HTML em linha, vale `sem_marcacao`.
"""

from __future__ import annotations

from views import dashboard

VENENO = "Casa d'Alim & <b>X</b> *mes*"


def test_a_matriz_de_categorias_nao_deixa_tag_aberta():
    html = dashboard._matriz_mes_a_mes(
        {
            "meses": ["2026-01"],
            "linhas": [{
                "categoria": VENENO, "meses": {"2026-01": 10_000},
                "acumulado": 10_000, "media": 10_000, "ano_anterior": 0,
            }],
        },
        2026,
    )

    assert "<b>X</b>" not in html, "o nome não pode virar tag"
    assert "&lt;b&gt;X&lt;/b&gt;" in html
    # o title da célula é o mesmo nome: o apóstrofo fechava o atributo
    assert "title='Casa d&#39;Alim &amp; &lt;b&gt;X&lt;/b&gt; *mes*'" in html
    # e nenhuma contrabarra: dentro da tabela ela apareceria na tela
    assert "\\*" not in html


def test_a_barra_de_meta_tambem_escapa_o_nome():
    html = dashboard._barra_categoria(
        {"categoria": VENENO, "realizado": 10_000, "meta": 20_000,
         "estourou": False, "meta_e_piso": False, "abaixo_do_piso": False},
        teto=20_000,
    )

    assert "<b>X</b>" not in html
    assert "&lt;b&gt;X&lt;/b&gt;" in html
