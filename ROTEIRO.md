# Roteiro — onde estamos e o que vem

Atualizado em 10/08/2026, fim do dia.

---

## Situação hoje

| | Estado |
|---|---|
| App publicado | ✅ no ar, `andre-leite-financas.streamlit.app` |
| Banco na nuvem | ✅ Supabase (São Paulo), base única para André e Rô |
| Carga inicial 2026 | ✅ bate com a planilha ao centavo, mês a mês |
| Venda do apartamento | ✅ lançada, e fora da renda que baliza o orçamento |
| Classificação | 🔄 em curso — de-para pronto, pente fino com o André |
| Leitura de extratos | ⬜ preparada, à espera das amostras |
| Senhas definitivas | ⬜ ainda na senha provisória `financas` |
| Análise por IA | ⬜ sem chave configurada |

### O número de referência

A tabela dinâmica da própria planilha é a verdade. Para 2026:

| | Planilha | App |
|---|---|---|
| DESP | 823.037,28 | 823.037,28 |
| REC | 1.904.765,63 | 1.904.765,63 |

Confere mês a mês, ao centavo, e um teste automático refaz essa conferência
antes de cada mudança no leitor (`tests/test_planilha_da_casa.py`).

### Como o dinheiro se divide

**Receita — o dono sai da fonte, não da conta em que caiu.** É isso que impede
a dupla contagem quando a mesma receita aparece na planilha da Rô e num
extrato meu.

| Fonte | De quem | Onde entra |
|---|---|---|
| **TAG** | André | Trabalho › Pró-labore / Salário |
| **BIOS** | Rô | Trabalho › Pró-labore / Salário |
| **NUN** / rótulo `ALUGUEL` | Casal — o apartamento é dos dois | Rendimentos › Aluguéis |

**Despesa — sem dono declarado, é da casa.** Só sai do Casal o que diz de quem
é: o nome no fim da descrição (`ALMOÇO ANDRÉ`, `CONSULTA RO`), uma regra
aprendida, uma coluna de pessoa no arquivo, ou uma categoria que é de uma
pessoa por natureza (**Filhos & Pensão é do André**).

Isso vale inclusive para fatura de cartão pessoal: o mercado do mês não vira
despesa de um só por ter passado no cartão dele.

---

## O que foi resolvido hoje

Nove coisas que estavam erradas, todas encontradas confrontando o app com a
planilha de verdade:

1. **A aba lida era a errada** — a pasta começa pela *Tabela Dinâmica*. O
   leitor agora descarta pelo nome qualquer aba de resumo.
2. **A data do Excel vinha com hora** (`2026-01-05 00:00:00`) e caía no leitor
   genérico, que lia 5 de janeiro como 1º de maio. Maio inchava para 486 mil,
   novembro e dezembro sumiam.
3. **Estorno virava receita** — valor negativo dentro de `DESP` é abatimento
   de gasto, não entrada.
4. **`Z195,82`** entrava como 195,82. Valor com letra agora é apontado e fica
   de fora — a linha 1882 continua para corrigir na planilha.
5. **O rótulo ganhava da descrição** — a venda do apartamento vinha marcada
   `TAG` e entrava como pró-labore. Agora a descrição diz *o que foi*, o rótulo
   diz no máximo *de quem é*.
6. **`RO` era lido como Rondônia** e sumia do fim da descrição. Eram 92
   lançamentos perdendo a marca da Rô.
7. **O gasto da casa virava dívida de uma pessoa** — 1.643 lançamentos sem
   dono declarado herdavam a resposta de "de quem é este arquivo". R$ 713 mil
   apareciam como despesa da Rô, que de fato tem R$ 35.371,57.
8. **A tela de classificação travava** — o plano de contas era relido com uma
   consulta por categoria, 18 idas ao banco a cada toque de campo.
9. **Não havia onde lançar pensão e filhos.** Criada a categoria
   **Filhos & Pensão**; só a pensão já são R$ 124.800 no ano.

---

## Etapa 1 — Base íntegra e real *(em curso, do André)*

O objetivo é chegar num passado 100% confiável. Sem isso, nada do que vier
depois vale. Com os totais fechados, sobram duas frentes: duplicidades e
classificação.

### 1.1 Resolver as duplicidades
**Upload de Extratos › Duplicidades**

Elas **continuam contando** nos relatórios enquanto não forem decididas — o
total não muda enquanto você revisa. Numa planilha com descrição curta digitada
à mão, duas idas à padaria no mesmo dia com o mesmo valor parecem duplicata sem
ser; a expectativa é que o que a Rô lançou não tenha duplicidade nenhuma. Se o
padrão se confirmar, o botão **"Manter as N prováveis"** limpa a fila de uma
vez.

### 1.2 Tratar o que não foi classificado
**Classificação › De-para de rótulos** (primeira aba) e **Fila de pendências**

A Rô classifica com o vocabulário dela, e **13 rótulos cobrem os 441
pendentes** — cinco deles cobrem 83%:

| Rótulo | Lançamentos | Valor |
|---|---|---|
| CUIDADOS PESSOAIS | 133 | 26.640,65 |
| INFRA | 67 | 13.102,68 |
| CONTRIBUIÇÃO MENSAL | 61 | 46.712,07 |
| CONTRIBUIÇÃO IGREJA | 56 | 73.108,72 |
| VIAGEM | 47 | 90.732,23 |
| TAXAS · CASA · NUN · LAZER | 63 | 58.271,10 |
| TORANA · LILLE · RIO · SEGURO | 14 | 2.225,46 |

O de-para traduz cada rótulo **uma vez** e guarda a tradução, então a próxima
importação já entra classificada. Parar na categoria resolve o relatório (é a
categoria que soma) e deixa o lançamento na fila para a subcategoria ser
escolhida caso a caso; escolher a subcategoria de uma vez tira da fila direto.
Desfazer devolve à fila o que a tradução classificou, sem desmanchar o que foi
corrigido à mão depois.

> **Pendente do André:** `TORANA`, `LILLE`, `RIO` e `NUN` como despesa não sei
> o que são. Se forem imóveis ou lugares recorrentes, viram regra.

### 1.3 As três fontes de renda
Quem trouxe o dinheiro sai do rótulo que a Rô usa na planilha, não da conta em
que caiu:

| Rótulo | De quem | Onde entra |
|---|---|---|
| **TAG** | André | Trabalho › Pró-labore / Salário |
| **BIOS** | Rô | Trabalho › Pró-labore / Salário |
| **NUN** | Casal — o apartamento é dos dois | Rendimentos › Aluguéis |

É isso que impede a dupla contagem: a mesma receita, lançada por ela na
planilha e por mim num extrato, cai na mesma pessoa pelos dois caminhos. Na
carga inicial isso separa 1.250.911,96 do André, 96.670,00 da Rô e 37.183,67 do
casal — soma exata do REC da dinâmica.

### 1.4 O que ainda merece olhada
- **Meses futuros** — a planilha tem lançamentos de setembro a dezembro de 2026
  (agendamentos). Eles contam como realizado. Decidir se entram ou ficam de
  fora até a data chegar.
- **Transferências entre contas de vocês** — aplicação, resgate e passagem de
  dinheiro entre contas próprias não são despesa; se estiverem lançadas como
  tal, contam o mesmo dinheiro duas vezes.
- **A linha 1881** (`Z195,82`, SEM PARAR) ficou de fora por erro de digitação
  no arquivo. Corrigir na planilha e reimportar, ou lançar à mão.

> **Pendente do meu lado (se ajudar):** lista dos 20 maiores lançamentos do
> período na Visão Geral — o jeito mais rápido de achar o que puxa o total.

---

## Etapa 2 — Leitura dos extratos reais

Os leitores de **Visa XP, BTG, Bradesco e Itaú** estão implementados sobre o
motor genérico, mas nunca viram o layout de verdade desses bancos. O do
**Nubank** segue o formato conhecido de exportação (`date,title,amount`).

**O preparo já está feito, e mudou o que preciso de você.** Ao subir um PDF, a
tela agora mostra, antes de gravar qualquer coisa:

- quantos lançamentos reconheceu, de quantas linhas de texto
- **quais linhas pareciam lançamento e ficaram de fora** — são elas que dizem
  o que falta ensinar ao leitor
- o texto cru, como o leitor o recebe
- e há campo de **senha do PDF**, que quase todo extrato de banco exige

Então não preciso mais dos arquivos: **copie o que a tela mostrar**, umas 10
linhas de lançamento, com os valores trocados. Este repositório é público —
apague nome, agência e conta antes de colar.

Com isso eu calibro cada leitor e escrevo um teste por banco, para o formato
não quebrar depois. Já existem quatro testes gerando PDFs de verdade (fatura
reconhecida, layout desconhecido, arquivo com senha, PDF só de imagem).

**Ao carregar:** um mês por vez, do mais antigo para o mais recente. Depois
confira **Upload › Crítica planilha × extratos**, que confronta as duas fontes
e separa o que bateu, o que faltava na planilha, o que só existe nela (gasto em
dinheiro) e onde os valores divergem.

---

## Etapa 3 — Análise por IA

1. `console.anthropic.com` › **API Keys** › **Create Key**
2. Adicionar crédito em **Billing** — US$ 5 duram muitos meses neste uso
3. No Streamlit: **⋮ › Settings › Secrets**, acrescentar
   `ANTHROPIC_API_KEY = "sk-ant-..."`
4. **Save** › **Reboot app**

Liga duas coisas: a classificação por IA do que as regras não pegam, e a tela
de análise mensal escrita.

---

## Etapa 4 — Beta em produção

1. Rodar `python scripts/gerar_senha.py` e gerar os hashes de André e Rô
2. Colar em **⋮ › Settings › Secrets** (ver `DEPLOY.md`, etapa 5)
3. Conferir que a senha `financas` **parou de funcionar**
4. Mandar o endereço para a Rô

Depois disso, o ciclo mensal é: subir os extratos do mês, resolver a fila de
classificação (que encolhe sozinha, porque cada correção vira memória),
conferir a Visão Geral e ler a análise.

---

## Armadilhas já encontradas (para não repetir)

- **A caixa "o valor vem positivo mesmo quando é gasto"** só serve quando o
  arquivo não tem coluna de tipo. Hoje ela fica desativada automaticamente
  quando existe uma — mas o princípio vale: quem manda no sinal é o arquivo.
- **Sempre olhar a prévia "Como vai ficar"** antes de processar. A coluna
  *Entra ou sai* mostra o erro antes de gravar milhares de lançamentos.
- **Extrato de conta corrente tem coluna de saldo** depois do valor. O leitor
  já trata, mas é o tipo de coisa que passa despercebida: a contagem de linhas
  fica certa e os valores, errados.
- **Desfazer é seguro.** Upload › Histórico › Desfazer uma importação remove
  exatamente os lançamentos daquele arquivo. A importação inteira roda numa
  transação: interromper no meio não deixa nada pela metade.
- **Uma categoria nova não alcança o passado sozinha.** Criar categoria ou
  regra só vale para importações seguintes; o que já está gravado se resolve
  pelo botão **"Reaplicar regras na fila"**.
- **O banco fica em São Paulo e o app roda nos Estados Unidos.** São ~150ms por
  consulta. Qualquer código que consulte dentro de um laço trava a tela — foi
  assim três vezes (seed, importação, plano de contas). Há um teste contando
  consultas em `tests/test_repo.py` para não acontecer de novo.
- **Sigla de duas letras no fim da descrição não é sempre estado.** Nesta casa
  `RO` é a Rô. Só sai UF de verdade, e só quando sobra nome antes dela.
- **Não confiar em número apresentado sem conferir contra a origem.** Mais de
  uma vez um número de base de teste foi mostrado como se fosse da base real. A
  tabela dinâmica da planilha é a única verdade; quando divergir, ela ganha.

---

## Onde está cada coisa

| | |
|---|---|
| Repositório | `andreleite-ops/financas-casa` (público), branch `main` |
| App | `andre-leite-financas.streamlit.app` |
| Banco | Supabase São Paulo, via **Session pooler** — o Direct é IPv6 e o Streamlit é IPv4 |
| Testes | `pytest tests/` — 154 passando |
| Deploy | `DEPLOY.md`, passo a passo, senhas por último |

Para rodar os testes:

```
pip install -r requirements-dev.txt
DATABASE_URL="sqlite:///teste.db" pytest tests/ -q
```
