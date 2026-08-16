"""Leituras caras guardadas entre um rerun e outro.

O Streamlit re-executa o script inteiro a cada toque de campo, e o banco fica
em São Paulo enquanto o app roda nos Estados Unidos: cada consulta custa uns
150ms de ida e volta. Reler a mesma coisa em todo rerun é o que faz um clique
demorar.

O que torna isto seguro é o carimbo de `db.versao_dos_dados()`: ele entra na
chave de cada cache e sobe sozinho a cada escrita no banco, venha de onde
vier. Salvou um lançamento? A chave muda, o cache anterior deixa de ser
encontrado e a próxima leitura vai ao banco. Não existe lista de pontos de
gravação para manter — e portanto não existe ponto de gravação para esquecer,
que é como cache vira mentira silenciosa na tela.

O TTL é a segunda rede: se algum dia o app rodar em mais de um processo, o
contador de um não enxerga a escrita do outro, e o TTL limita o estrago.

Só entra aqui leitura que **não é a resposta ao que a pessoa acabou de fazer**.
A fila de pendências, por exemplo, fica de fora de propósito: é a tela onde se
grava e se relê no mesmo gesto, e um lançamento que continuasse na fila depois
de classificado não seria dado velho — seria a tela mentindo sobre o trabalho
de quem está olhando.
"""

from __future__ import annotations

import streamlit as st

from core import analytics, db, dedup, reconcile, repo

TTL = 300


def versao() -> int:
    return db.versao_dos_dados()


@st.cache_data(ttl=TTL, show_spinner=False)
def plano_de_contas(_engine, versao: int, natureza: str | None = None) -> list[dict]:
    """O plano inteiro. Lido por seis telas, e muda quando alguém o edita."""
    with _engine.connect() as conn:
        return repo.plano_de_contas(conn, natureza=natureza)


@st.cache_data(ttl=TTL, show_spinner=False)
def contas(_engine, versao: int, so_ativas: bool = True) -> list[dict]:
    with _engine.connect() as conn:
        return repo.listar_contas(conn, so_ativas=so_ativas)


@st.cache_data(ttl=TTL, show_spinner=False)
def competencias(_engine, versao: int) -> list[str]:
    with _engine.connect() as conn:
        return repo.competencias_disponiveis(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def contexto_do_mes(_engine, versao: int, competencia: str) -> str:
    """O texto que vai para a IA — umas quinze consultas para montar.

    A tela de Análise IA tem quatro abas, e o Streamlit executa todas em todo
    rerun. Sem cache, escolher uma subcategoria na quarta aba remontava este
    contexto e o do ano inteiro, do zero.

    O carimbo de versão importa em dobro aqui: é a impressão digital deste
    texto que decide se a análise já gravada está desatualizada. Um contexto
    velho faria o aviso "os números mudaram" não aparecer — e alguém leria
    texto vencido achando que vale.
    """
    with _engine.connect() as conn:
        return analytics.contexto_para_ia(conn, competencia)


@st.cache_data(ttl=TTL, show_spinner=False)
def contexto_do_ano(_engine, versao: int, competencia: str) -> str:
    with _engine.connect() as conn:
        return analytics.contexto_do_ano(conn, competencia)


@st.cache_data(ttl=TTL, show_spinner=False)
def usos_das_categorias(_engine, versao: int) -> dict[int, dict]:
    with _engine.connect() as conn:
        return repo.usos_das_categorias(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def critica(_engine, versao: int) -> dict:
    """Planilha × extratos, confrontados período a período.

    Quatro consultas, e a aba dela é uma de sete — todas executadas em todo
    rerun da tela de Upload, inclusive enquanto alguém escolhe o arquivo a
    enviar na primeira.
    """
    with _engine.connect() as conn:
        return reconcile.criticar(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def painel_do_mes(_engine, versao: int, competencia: str, pessoa: str) -> dict:
    """Os oito números da Visão Geral, numa chamada só.

    É a tela de abertura e a que se volta a olhar depois de classificar. São
    oito agregações diferentes — não dá para transformá-las numa consulta —,
    mas dá para não refazê-las quando nada mudou: abrir uma categoria, trocar
    de aba ou clicar em qualquer botão da tela repetia as oito.

    Cachear aqui só é seguro por causa do carimbo de versão: é justamente esta
    tela que alguém abre para conferir se a classificação surtiu efeito, e um
    número velho aqui seria pior do que a demora.
    """
    ano = int(competencia[:4])
    with _engine.connect() as conn:
        atual = analytics.resumo(conn, competencia=competencia, pessoa=pessoa)
        categorias = analytics.por_categoria(conn, competencia=competencia, pessoa=pessoa)
        metas = repo.listar_metas(conn, ano)
        return {
            "atual": atual,
            "serie": analytics.serie_mensal(conn, ano, pessoa=pessoa),
            "categorias": categorias,
            "metas": metas,
            # o orçamento é da casa inteira, e `categorias` pode estar filtrado
            # por pessoa: só reaproveita quando as duas perguntas são a mesma
            "orcamento": analytics.orcamento(
                conn, competencia, metas, resumo_do_mes=atual,
                gastos_do_mes=categorias if pessoa == "Todos" else None,
            ),
            "matriz": analytics.tabela_mes_a_mes(conn, ano, pessoa=pessoa),
            "anual": analytics.comparativo_anual(conn, pessoa=pessoa),
            "acumulado": analytics.resumo(conn, ano=ano, pessoa=pessoa),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def meses_com_despesa(_engine, versao: int) -> set[str]:
    """Em que meses há gasto lançado — decide onde a Visão Geral abre."""
    with _engine.connect() as conn:
        return analytics.meses_com_despesa(conn)


# --------------------------------------------------------------------------
# a tela de classificação
# --------------------------------------------------------------------------
# Estas leituras parecem justamente as que não se pode cachear: são a resposta
# ao que a pessoa acabou de fazer. Elas entram aqui porque o carimbo de versão
# resolve isso pela raiz — salvar um lançamento é uma escrita, a escrita muda
# o carimbo, e a fila é relida. O que sai de graça é o resto: escolher uma
# categoria, digitar no filtro, trocar de aba. Classificar cinquenta
# lançamentos são dezenas de reruns e só uma dúzia de gravações.
@st.cache_data(ttl=TTL, show_spinner=False)
def fila_de_pendencias(_engine, versao: int, competencia: str | None, termo: str) -> list[dict]:
    with _engine.connect() as conn:
        return repo.fila_pendentes(conn, limite=5000, competencia=competencia, termo=termo)


@st.cache_data(ttl=TTL, show_spinner=False)
def busca(_engine, versao: int, termo: str, limite: int = 40) -> list[dict]:
    with _engine.connect() as conn:
        return repo.buscar_transacoes(conn, termo, limite=limite)


@st.cache_data(ttl=TTL, show_spinner=False)
def painel_de_classificacao(_engine, versao: int) -> dict:
    """O cabeçalho da tela de Classificação: contagens e avisos de dono."""
    with _engine.connect() as conn:
        donos_errados, orfaos = repo.alertas_de_dono(conn)
        return {
            "por_mes": repo.pendentes_por_competencia(conn),
            "donos_errados": donos_errados,
            "orfaos": orfaos,
            "rotulos": repo.rotulos_pendentes(conn),
            "traduzidos": repo.listar_de_para(conn),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def contadores_da_barra(_engine, versao: int) -> tuple[int, int]:
    """Os dois números do menu. Rodam em todo rerun de toda tela."""
    with _engine.connect() as conn:
        return repo.contar_pendentes(conn), dedup.contar_pendentes(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def cobertura_de_uploads(_engine, versao: int, competencias: tuple[str, ...]) -> dict:
    """O mapa "o que falta carregar", conta por mês."""
    with _engine.connect() as conn:
        return {
            "contas": repo.cobertura(conn, list(competencias)),
            "planilha": repo.cobertura_planilha(conn, list(competencias)),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def lancamentos_manuais(_engine, versao: int, ano: int, natureza: str) -> list[dict]:
    with _engine.connect() as conn:
        return repo.lancamentos_manuais(conn, ano, natureza=natureza)


@st.cache_data(ttl=TTL, show_spinner=False)
def uploads(_engine, versao: int) -> list[dict]:
    with _engine.connect() as conn:
        return repo.listar_uploads(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def duplicidades(_engine, versao: int) -> list[dict]:
    with _engine.connect() as conn:
        return dedup.pendentes(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def cobertura_do_mes(_engine, versao: int, competencia: str) -> dict:
    with _engine.connect() as conn:
        return analytics.cobertura_da_classificacao(conn, competencia)


@st.cache_data(ttl=TTL, show_spinner=False)
def painel_do_ano(_engine, versao: int, competencia: str) -> dict:
    with _engine.connect() as conn:
        return {
            "janela": analytics.janela_de_doze_meses(conn, competencia),
            "do_ano": analytics.resumo_do_ano(conn, competencia),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def metas_do_ano(_engine, versao: int, ano: int) -> dict:
    """Metas gravadas e a média do ano — o que a tela precisa antes de saber
    qual renda a pessoa vai considerar."""
    with _engine.connect() as conn:
        return {
            "metas": repo.listar_metas(conn, ano),
            "media": analytics.resumo(conn, ano=ano),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def realizado_do_orcamento(_engine, versao: int, ano: int, competencia: str,
                           renda_base: int) -> dict:
    """Sugestão pelas médias e realizado × meta — dependem da renda escolhida.

    Fica separado do bloco acima de propósito: a renda considerada é um campo
    que a pessoa mexe, e juntar as duas coisas faria toda mexida naquele campo
    reler também as metas, que não dependem dele.
    """
    with _engine.connect() as conn:
        return {
            "sugestao": repo.metas_pela_media(conn, ano, renda_base),
            "realizado": analytics.orcamento(
                conn, competencia, repo.listar_metas(conn, ano), renda_base=renda_base
            ),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def painel_de_receitas(_engine, versao: int, ano: int, competencia: str | None) -> dict:
    """De onde vem a renda, por pessoa e por tipo."""
    filtro = {"competencia": competencia} if competencia else {"ano": ano}
    with _engine.connect() as conn:
        return {
            "total": analytics.resumo(conn, **filtro)["receitas"],
            "por_pessoa": analytics.receitas_por_pessoa(conn, **filtro),
            "matriz": analytics.receitas_por_pessoa_e_tipo(conn, ano),
            "itens": analytics.lancamentos(
                conn, **filtro, natureza="receita", limite=400
            ),
        }


@st.cache_data(ttl=TTL, show_spinner=False)
def analise_gravada(_engine, versao: int, competencia: str, contexto: str,
                    tipo: str = "mes") -> dict | None:
    """A última análise escrita, e se os números mudaram desde então."""
    with _engine.connect() as conn:
        return repo.ultima_analise(conn, competencia, contexto, tipo=tipo)


@st.cache_data(ttl=TTL, show_spinner=False)
def perguntas_anteriores(_engine, versao: int, competencia: str) -> list[dict]:
    with _engine.connect() as conn:
        return repo.perguntas_anteriores(conn, competencia)


@st.cache_data(ttl=TTL, show_spinner=False)
def sem_subcategoria(_engine, versao: int, competencia: str) -> list[dict]:
    with _engine.connect() as conn:
        return repo.sem_subcategoria(conn, competencia=competencia, limite=500)
