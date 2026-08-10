# Roteiro — onde estamos e o que vem

Atualizado em 10/08/2026.

---

## Situação hoje

| | Estado |
|---|---|
| App publicado | ✅ no ar, `andre-leite-financas.streamlit.app` |
| Banco na nuvem | ✅ Supabase (São Paulo), base única para André e Rô |
| Carga inicial | ✅ bate com a planilha ao centavo (ver abaixo) |
| Senhas definitivas | ⬜ ainda na senha provisória `financas` |
| Análise por IA | ⬜ sem chave configurada |

### O número de referência

A tabela dinâmica da própria planilha é a verdade. Para 2026:

| | Planilha | App |
|---|---|---|
| DESP | 791.370,25 | 791.370,25 |
| REC | 1.384.765,63 | 1.384.765,63 |

Confere também mês a mês, ao centavo, e o mesmo teste roda automaticamente
antes de cada mudança no leitor.

Como se chegou aqui — quatro coisas que estavam erradas:

1. **A aba lida era a errada.** A pasta começa pela *Tabela Dinâmica*; o leitor
   pegava a primeira aba e lia o resumo. Agora ele descarta pelo nome qualquer
   aba de resumo e procura a que tem data e valor.
2. **O mês era o da data, não o da competência.** A dinâmica agrupa por
   `MÊS/ANO`. Vinte e duas linhas caíam num mês diferente do que a Rô lançou.
3. **Estorno virava receita.** Um valor negativo dentro de `DESP` é abatimento
   de gasto; o leitor tomava o módulo e o transformava em entrada.
4. **`Z195,82`** entrava como 195,82. Valor com letra no meio agora é apontado
   e fica de fora — inventar o número seria pior que apontar a linha.

Uma decisão mudou junto: **suspeita provável de duplicidade continua contando**
nos relatórios até você decidir. Só a duplicata exata contra arquivo já enviado
fica de fora. Sem isso, o total logo depois do upload já vinha menor que o da
planilha, sem nada explicando a diferença.

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
**Classificação › Fila de pendências**

Boa parte usa classificações da Rô que não existem no plano de contas:
`CUIDADOS PESSOAIS`, `INFRA`, `CONTRIBUIÇÃO MENSAL`, `EXTRA`, `TAXAS`, `TAG`,
`ALUGUEL`. Classificar isso à mão, um por um, não é viável.

> **Pendente do meu lado:** a tela de **de-para**, que lista as classificações
> distintas da planilha e permite mapear cada uma **uma vez** para o plano de
> contas — resolvendo centenas de lançamentos de uma vez. A estrutura de dados
> já está pronta (tabela `de_para` e a coluna que guarda a classificação de
> origem); falta a tela.
>
> **Decisão pendente do casal:** traduzir o vocabulário da Rô para o nosso
> plano, ou adaptar o plano ao vocabulário dela? Ela usa isso há anos; a
> segunda opção costuma funcionar melhor.

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

**O que preciso de você:** para cada uma das contas, uma amostra —

- **PDF:** ~10 linhas de lançamento copiadas como texto, com o cabeçalho e
  alguma linha de total. Pode trocar os valores; o que importa é o formato.
- **CSV/XLSX:** o cabeçalho e 4 ou 5 linhas.

Pode colar direto na conversa. Se preferir mandar o arquivo, use uma pasta
`amostras/` — mas **este repositório é público**: troque os valores e apague
nome, agência e conta antes.

Com as amostras eu calibro cada leitor e escrevo um teste por banco, para o
formato não quebrar depois.

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
