# Controle de Finanças Domésticas — Planejamento

**Status: plano aprovado — Fases 1 a 5 implementadas** · 02/08/2026

O código está em `financas/`. Como rodar, publicar no Streamlit Cloud e
configurar o Supabase: veja `financas/README.md`.

Sistema de controle financeiro do casal (André e Rô), alimentado por extratos de
cartões de crédito e contas correntes, com classificação automática de despesas,
tela de classificação manual, dashboards e análise de IA.

---

## 1. Arquitetura

Padrão de sempre: **GitHub + Streamlit**.

```
┌─────────────────────────────────────────────────────────┐
│  Streamlit Community Cloud (grátis)                     │
│  App protegido por senha — André e Rô acessam de        │
│  qualquer computador pelo navegador                     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  Supabase (PostgreSQL na nuvem, plano grátis)           │
│  Base ÚNICA e compartilhada: transações, plano de       │
│  contas, regras de classificação, metas, uploads        │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  Claude API (Haiku) — classificação das despesas que    │
│  as regras não resolvem + análise mensal de gastos      │
└─────────────────────────────────────────────────────────┘
```

- **Código**: este repositório GitHub, pasta `financas/` (o Streamlit Cloud
  aponta para `financas/app.py`).
- **Banco na nuvem**: Supabase (PostgreSQL, plano grátis — 500 MB, muito mais
  que suficiente). Uma base única: vocês dois leem e gravam a mesma base de
  qualquer computador. Alternativa considerada: Google Sheets (rejeitada —
  frágil para dedup e volume de transações).
- **Acesso**: tela de login com **apenas 2 usuários — André e Rô**, cada um
  com sua própria senha (hash armazenado em `st.secrets`, via
  `streamlit-authenticator`). Não há cadastro aberto nem outros usuários. O
  login identifica automaticamente quem fez cada upload e classificação.
- **IA**: Claude API (modelo Haiku, baratíssimo — centavos por mês) para
  classificar o que as regras não pegarem e para a análise mensal escrita.

## 2. Fontes de dados (contas cadastradas)

| Conta | Tipo | Titular | Formatos esperados |
|---|---|---|---|
| Visa XP | Cartão de crédito | André | PDF, CSV |
| BTG Mastercard | Cartão de crédito | André | PDF, CSV, XLSX |
| Nubank Mastercard | Cartão de crédito | — | PDF, CSV |
| Bradesco C/C | Conta corrente | André | PDF, CSV, XLSX |
| Itaú C/C | Conta corrente | Rô | PDF, CSV, XLSX |
| Conjunta C/C | Conta corrente | Casal (Rô) | PDF, CSV, XLSX |

Cada instituição tem um **parser próprio** (layout de fatura/extrato é
diferente). Para construir os parsers precisaremos de **1 arquivo de amostra de
cada fonte** (pode ser de mês antigo).

**Cadastro de contas gerenciável pelo app**: as contas acima são só a carga
inicial. Na tela de Upload haverá um "Gerenciar contas e cartões" para
**incluir novas contas/cartões e desativar antigos** a qualquer momento (troca
de cartão, banco novo etc.), informando nome, tipo (cartão ou conta corrente),
titular e instituição — sem mexer em código. Contas desativadas param de
aparecer no upload, mas o histórico de lançamentos delas continua nos
relatórios. Conta nova de instituição que já tem parser funciona na hora;
instituição inédita usa o leitor genérico de CSV/XLSX (mapeamento de colunas
assistido) até criarmos o parser específico.

## 3. Pipeline de classificação (3 camadas)

1. **Memória de estabelecimentos** — o sistema lembra toda reclassificação
   manual: se "PADARIA STELLA" foi marcada como Alimentação › No Domicílio uma
   vez, todas as próximas vão automático. É a camada que aprende.
2. **Regras por palavra-chave** — dicionário inicial por categoria (ex.:
   UBER/99 → Transporte › Apps; RAIA/DROGASIL → Saúde › Farmácia; IFOOD →
   Alimentação › Fora do Domicílio).
3. **IA (Claude Haiku)** — o que sobrar vai para a IA classificar com nível de
   confiança. Confiança baixa ou dúvida → fila da **tela de Classificação
   Manual**.

Na tela de Classificação Manual o usuário também pode **reclassificar qualquer
lançamento**, mesmo os já classificados pelo sistema — e a correção alimenta a
camada 1.

### Carga inicial: a planilha da Rô

O ponto de partida do histórico é a **planilha que a Rô vem lançando**. Ela
entra por um importador próprio (mapeamento de colunas assistido) e os
lançamentos ficam marcados com origem "planilha". Como ela pode não ter 100%
dos lançamentos, quando os extratos/faturas dos mesmos períodos forem
subidos o sistema roda uma **crítica de conferência**:

- **Faltantes** — está no extrato, não está na planilha (entra com a
  classificação automática);
- **Sobras** — está na planilha, não apareceu em nenhum extrato (pode ser
  lançamento em dinheiro; o usuário decide manter ou excluir);
- **Divergências** — mesmo lançamento com valor ou data diferente (o usuário
  escolhe qual versão vale);
- **Duplicidades** — mesmo lançamento nas duas origens (o do extrato
  prevalece, herdando a categoria que a Rô já tinha dado na planilha).

### Duplicidades — sempre mediante confirmação

Nada é excluído em silêncio. Se um extrato/fatura for subido duas vezes, ou
faturas com períodos sobrepostos:

- **Duplicata exata** (mesma conta + data + valor + descrição, via hash) — o
  sistema identifica, mostra os pares lado a lado na tela de conferência e
  **exclui somente após confirmação** (com botão "confirmar todas" para lotes
  óbvios);
- **Provável duplicata** (mesma conta, valor e data próxima, descrição
  parecida) — nunca é excluída automaticamente; fica na fila para o usuário
  decidir "excluir duplicado" ou "manter os dois" (ex.: dois cafés iguais no
  mesmo dia são legítimos).

## 4. Modelo de dados (Supabase)

- `transacoes` — data, descrição, valor, tipo (despesa/receita), conta,
  categoria, subcategoria, pessoa (André/Rô/Casal), status (auto/manual/
  pendente), hash_dedup, arquivo de origem, competência (mês da fatura)
- `contas` — cadastro gerenciável: nome, tipo (cartão/conta corrente),
  titular, instituição, parser associado, ativa/inativa (carga inicial: as 6
  contas acima)
- `categorias` / `subcategorias` — o plano de contas (editável pelo app)
- `regras` — padrão de texto → categoria (inclui a memória aprendida)
- `metas` — % do orçamento por categoria, por ano (inclui Poupança)
- `uploads` — histórico de arquivos processados

## 5. Telas do app

1. **Visão Geral (Dashboard)** — cards do mês (receitas, despesas, poupança,
   % do orçamento usado), gasto por categoria vs meta, evolução mensal
   receitas × despesas, tabela mês a mês, acumulado no ano e comparativo ano a
   ano.
2. **Upload de Extratos** — escolhe a conta, arrasta o arquivo (PDF/CSV/XLSX),
   vê o resumo do processamento (lidas, classificadas, pendentes, possíveis
   duplicidades). Inclui o gerenciador de contas/cartões, a **fila de
   duplicidades aguardando confirmação** e a **crítica planilha × extratos**.
3. **Classificação Manual** — fila de pendências + busca de qualquer
   lançamento para reclassificar; correções viram regra.
4. **Receitas** — pró-labore/salário, bônus e prestação de serviços, sempre
   separados André × Rô.
5. **Orçamento & Metas** — define % da renda por categoria (Poupança é uma
   categoria de meta), acompanha realizado vs meta.
6. **Análise IA** — texto mensal: onde o dinheiro está indo, o que fugiu do
   padrão, oportunidades de economia, evolução da poupança.
7. **Plano de Contas** — visualiza e edita categorias/subcategorias.

## 6. Plano de Contas proposto

Todo lançamento (receita ou despesa) carrega **Pessoa: André / Rô / Casal**.

### RECEITAS
| Categoria | Subcategorias |
|---|---|
| 1. Trabalho | Pró-labore / Salário · Bônus / PLR · Prestação de Serviços · 13º / Férias |
| 2. Rendimentos | Aplicações Financeiras · Dividendos / JCP · Aluguéis |
| 3. Outras Receitas | Reembolsos · Venda de Bens · Transferências Recebidas |

### DESPESAS
| Categoria | Subcategorias |
|---|---|
| 1. Moradia | Condomínio · IPTU · Energia · Água / Gás · Internet / TV / Telefone · Manutenção & Reforma · Empregados Domésticos · Móveis & Decoração |
| 2. Alimentação | No Domicílio (supermercado, feira, padaria, açougue) · Fora do Domicílio (restaurantes, delivery, bares & cafés) |
| 3. Transporte | Combustível · Apps (Uber / 99 / Táxi) · Estacionamento & Pedágio · Manutenção do Veículo · Seguro / IPVA / Licenciamento |
| 4. Saúde | Plano de Saúde · Médicos & Dentistas · Farmácia · Exames · Terapias · Academia & Bem-estar |
| 5. Educação | Mensalidades & Cursos · Livros & Materiais · Idiomas |
| 6. Lazer & Viagens | Viagens · Passeios & Eventos · Hobbies · Clube |
| 7. Assinaturas & Tecnologia | Streaming · Aplicativos & Software · Eletrônicos |
| 8. Vestuário & Cuidados Pessoais | Roupas & Calçados · Cabeleireiro & Estética · Perfumaria & Cosméticos |
| 9. Pets | Alimentação · Veterinário & Farmácia · Banho & Tosa |
| 10. Presentes & Doações | Presentes · Doações |
| 11. Financeiras & Impostos | Tarifas Bancárias · Anuidade de Cartão · Juros & Multas · Imposto de Renda · Seguros (Vida / Residencial) |
| 12. Poupança & Investimentos | Aporte Mensal · Previdência Privada · Reserva de Emergência |
| 13. Outros | Saques · Transferências a Identificar · A Classificar |

O plano é editável pelo próprio app — dá para criar/renomear categorias sem
mexer em código.

## 7. Fases de execução (com outros modelos)

| Fase | Entrega | Situação |
|---|---|---|
| 1 | Estrutura do app + autenticação (2 usuários) + plano de contas no banco | ✅ feito |
| 2 | Upload + leitores das 6 fontes + importador da planilha da Rô + duplicidades com confirmação + crítica planilha × extratos | ✅ feito (leitores por banco a calibrar com arquivos reais) |
| 3 | Motor de classificação (memória → regras → IA) + tela de classificação manual | ✅ feito |
| 4 | Dashboards: tabelas mês a mês, acumulado no ano, ano a ano, gráficos | ✅ feito |
| 5 | Receitas André × Rô + orçamento % por categoria + análise IA mensal | ✅ feito |
| 6 | Deploy no Streamlit Cloud + rodada com arquivos reais | ⏳ depende de você (conta Supabase + arquivos de amostra) |

## 8. O que precisamos de você para começar

1. **Aprovação** deste plano, do plano de contas e do mockup (`mockup.html`).
2. **A planilha da Rô** (a carga inicial do histórico — vamos mapear as
   colunas dela juntos na Fase 2).
3. **1 arquivo de amostra de cada fonte** (as 6 contas) — pode ser mês antigo.
4. **Conta no Supabase** (grátis, criamos juntos na Fase 1 — 5 minutos).
5. **Chave da API Anthropic** para classificação IA e análise mensal (custo
   estimado: centavos/mês com Haiku). Sem a chave, o sistema funciona só com
   regras + manual.
