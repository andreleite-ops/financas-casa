# Controle de Finanças Domésticas — André & Rô

App Streamlit sobre base única na nuvem. Importa faturas de cartão e extratos
de conta corrente, classifica as despesas automaticamente, mantém uma tela de
classificação manual que ensina o sistema, e apresenta gastos em tabelas,
gráficos e uma análise escrita por IA.

O plano completo e o mockup aprovado estão em `PLANEJAMENTO.md` e `mockup.html`.

## Como rodar na sua máquina

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/streamlit run app.py
```

Sem nenhuma configuração o app sobe com um banco SQLite local
(`dados/financas.db`) e senha de desenvolvimento `financas`. É o
suficiente para experimentar; **não** serve para vocês dois usarem juntos, porque
cada computador teria a sua própria base.

### Ver o app com dados de exemplo

```bash
DATABASE_URL="sqlite:///$PWD/dados/demo.db" .venv/bin/python scripts/gerar_demo.py
DATABASE_URL="sqlite:///$PWD/dados/demo.db" .venv/bin/streamlit run app.py
```

Gera 18 meses de lançamentos fictícios para navegar por todas as telas. Nada
disso vai para o repositório — `dados/` está no `.gitignore`.

## Colocar no ar (base única, os dois acessando)

### 1. Banco no Supabase

1. Crie um projeto grátis em [supabase.com](https://supabase.com).
2. Em **Project Settings › Database › Connection string › URI**, copie a URL e
   troque `[YOUR-PASSWORD]` pela senha do banco.
3. Guarde: é o `DATABASE_URL`.

As tabelas são criadas sozinhas na primeira execução — não precisa rodar SQL.

### 2. Senhas de vocês dois

```bash
.venv/bin/python scripts/gerar_senha.py
```

Ele pede a senha do André e a da Rô e imprime o bloco pronto para colar. As
senhas em si não são gravadas em lugar nenhum — só o hash bcrypt.

### 3. Segredos

Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml` e
preencha `DATABASE_URL`, `ANTHROPIC_API_KEY` (opcional) e o bloco de usuários.
**O `secrets.toml` nunca é comitado.**

### 4. Streamlit Cloud

1. [share.streamlit.io](https://share.streamlit.io) › New app › aponte para este
   repositório.
2. **Main file path:** `app.py`
3. Em **Settings › Secrets**, cole o mesmo conteúdo do `secrets.toml`.

Pronto: os dois acessam pelo navegador, de qualquer computador, na mesma base.

## Como o sistema classifica

Três camadas, nesta ordem:

1. **Memória de estabelecimentos** — toda correção manual vira regra. Corrigiu
   "PADARIA STELLA" uma vez, as próximas entram sozinhas. A chave ignora
   parcela, data e código de terminal, então o mesmo lugar escrito de formas
   diferentes cai na mesma memória.
2. **Regras por palavra-chave** — dicionário inicial com ~180 padrões
   (IFOOD → Alimentação › Fora do Domicílio, DROGASIL → Saúde › Farmácia…).
3. **IA (Claude Haiku)** — só o que sobrou. Confiança baixa vai para a fila
   manual em vez de chutar.

**Guarda de natureza:** uma regra de receita nunca classifica dinheiro que
*saiu* da conta. "PAGAMENTO SALÁRIO EMPREGADA" é despesa, mesmo casando a
palavra SALÁRIO.

Sem `ANTHROPIC_API_KEY` o sistema funciona normalmente — só a camada 3 e a tela
de análise ficam desligadas.

## Duplicidades e a planilha da Rô

Nada é excluído sem confirmação. Ao reimportar um arquivo:

- **Duplicata exata** (mesma conta, data, valor e descrição) entra no banco
  *inativa*, fora dos relatórios, e espera o seu OK na aba **Duplicidades**.
- **Provável duplicata** (mesmo valor e estabelecimento, poucos dias de
  diferença) nunca sai sozinha — dois cafés iguais no mesmo dia podem ser reais.

A planilha da Rô entra como origem `planilha` e é a carga inicial do histórico.
Quando o extrato do mesmo período chega, a aba **Crítica** confronta os dois:
conferidos, faltantes na planilha, só na planilha e divergências de valor/data.
O lançamento do extrato prevalece **herdando a categoria que ela já tinha dado**.

## Detalhes que valem saber

- **Dinheiro é inteiro de centavos**, nunca float. Somas de 18 meses fecham no
  centavo.
- **Sinal**: negativo = saída, positivo = entrada. Um estorno lançado numa
  categoria de despesa abate o total daquela categoria em vez de inflar as
  receitas.
- **Poupança não é despesa.** O total de despesas exclui Poupança &
  Investimentos, que aparece em separado; a meta dela é **piso**, não teto —
  superar é bom, e o app mostra em verde.
- **Contas e cartões são cadastro**, não código. Incluir e desativar pela tela
  de Upload; conta desativada some do upload mas mantém o histórico.
- **Instituição nova** sem leitor próprio entra pelo leitor genérico de
  CSV/XLSX, com mapeamento de colunas assistido na tela.

## Estrutura

```
app.py                 navegação, login e inicialização
core/                  banco, seed, dedup, classificação, análises, IA
parsers/               leitores: genérico CSV/XLSX, PDF e por instituição
views/                 as 7 telas
ui/                    tema e gráficos
scripts/               gerar_senha.py e gerar_demo.py
tests/                 suíte pytest
```

## Por que este repositório é público

O Streamlit Community Cloud só permite um app privado por conta, e essa vaga já
está ocupada por outro app. Aqui fica **apenas o código** — nenhum dado e
nenhuma senha:

- os lançamentos ficam no banco do Supabase, fora do repositório;
- as senhas de André e Rô ficam nos Secrets do Streamlit, como hash bcrypt;
- o `secrets.toml` está no `.gitignore` e nunca é enviado.

Quem abrir o endereço do app encontra a tela de login e não passa dela.

## Testes

```bash
cd financas && ../.venv/bin/python -m pytest tests/ -q
```

## O que ainda depende de arquivos reais

Os leitores de **Visa XP, BTG, Bradesco e Itaú** estão implementados sobre o
motor genérico e marcados como `CALIBRAR` — funcionam para CSV/XLSX e para PDFs
de layout comum, mas só dá para garantir cada formato com uma fatura e um
extrato de verdade em mãos. O leitor do **Nubank** já segue o formato conhecido
de exportação (`date,title,amount`).
