# Roteiro — onde estamos e o que vem

Atualizado em 03/08/2026.

---

## Situação hoje

| | Estado |
|---|---|
| App publicado | ✅ no ar, `andre-leite-financas.streamlit.app` |
| Banco na nuvem | ✅ Supabase (São Paulo), base única para André e Rô |
| Carga inicial | ✅ importada — 1761 lançamentos, 1196 classificados |
| Senhas definitivas | ⬜ ainda na senha provisória `financas` |
| Análise por IA | ⬜ sem chave configurada |

Números da última importação a conferir: despesas de 2026 em ~R$ 500 mil,
ainda **não validadas** contra a realidade.

---

## Etapa 1 — Base íntegra e real *(em curso, do André)*

O objetivo é chegar num passado 100% confiável. Sem isso, nada do que vier
depois vale.

### 1.1 Resolver as duplicidades
**Upload de Extratos › Duplicidades** — 143 pendentes, todas "prováveis".

Elas estão **fora dos relatórios** enquanto não forem decididas. Revise
algumas: numa planilha com descrição curta digitada à mão, duas idas à padaria
no mesmo dia com o mesmo valor parecem duplicata sem ser. Se o padrão se
confirmar, o botão **"Manter as N prováveis"** devolve todas de uma vez.

### 1.2 Tratar o que não foi classificado
**Classificação › Fila de pendências** — 565 lançamentos.

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

### 1.3 Incluir as receitas do André
Hoje só as receitas lançadas na planilha da Rô estão na base. As do André
faltam — sem elas, a comparação receita × despesa fica sem sentido e o
orçamento por % da renda não fecha.

Formas de entrar: acrescentar na planilha e reimportar, ou virão junto com o
extrato do Bradesco na Etapa 2.

### 1.4 Validar o total
Quatro suspeitos quando o número parecer alto demais:

- **Meses futuros** — a planilha tem lançamentos com data de setembro, outubro
  e novembro de 2026 (agendamentos). Eles contam como gasto realizado. Decidir
  se entram ou ficam de fora até a data chegar.
- **Maio de 2026** — apareceu com ~94 mil contra ~20 mil dos outros meses.
- **Transferências entre contas de vocês** — aplicação, resgate e passagem de
  dinheiro entre contas próprias não são despesa; se estiverem lançadas como
  tal, contam o mesmo dinheiro duas vezes.
- **O que ainda está sem categoria** — entra no total mesmo sem classificação.

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
