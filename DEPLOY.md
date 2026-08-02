# Como colocar o app no ar — passo a passo

Do zero até você e a Rô acessando pelo navegador, de computadores diferentes,
na mesma base. São 5 etapas; reserve uns 30 minutos. **As senhas de vocês ficam
para o final**, depois que tudo o mais estiver funcionando.

Você vai precisar de: uma conta no GitHub (já tem), uma no Supabase e uma no
Streamlit. As duas últimas são gratuitas e aceitam login com a conta do Google.

---

## Etapa 1 — Criar o banco no Supabase

O Supabase é onde os lançamentos ficam guardados. É ele que faz vocês dois
verem os mesmos dados de computadores diferentes.

1. Acesse **[supabase.com](https://supabase.com)** e clique em **Start your
   project**. Entre com o Google.
2. Clique em **New project**.
3. Preencha:
   - **Name:** `financas-casa`
   - **Database Password:** clique em **Generate a password** e **guarde essa
     senha** — você vai colar ela no passo 5 desta etapa. Não é a senha de
     vocês para entrar no app, é a senha do banco.
   - **Region:** `South America (São Paulo)`
4. Clique em **Create new project** e espere uns 2 minutos.
5. Com o projeto criado, clique na engrenagem **Project Settings** (canto
   inferior esquerdo) › **Database** › role até **Connection string** ›
   aba **URI**.

   Vai aparecer algo assim:

   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.abcdefgh.supabase.co:5432/postgres
   ```

6. Copie essa linha, troque `[YOUR-PASSWORD]` (incluindo os colchetes) pela
   senha que você guardou no passo 3, e cole num bloco de notas. **Essa linha
   inteira é o seu `DATABASE_URL`.**

> As tabelas são criadas sozinhas na primeira vez que o app abrir. Você não
> precisa rodar nenhum comando de SQL.

---

## Etapa 2 — Publicar o app no Streamlit

1. Acesse **[share.streamlit.io](https://share.streamlit.io)** e clique em
   **Sign in with GitHub**. Autorize o acesso.
2. Clique em **Create app** › **Deploy a public app from GitHub**.
3. Preencha:
   - **Repository:** `andreleite-ops/financas-casa`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** escolha o endereço, por exemplo `andre-leite-financas`. Ele
     vira `andre-leite-financas.streamlit.app` — é esse link que vocês dois vão
     usar.

   > Como este repositório é público, o Streamlit publica sem consumir a vaga
   > de app privado da sua conta. Público é o endereço, não os dados: quem
   > abrir encontra a tela de login e não passa dela.
4. **Antes de clicar em Deploy**, clique em **Advanced settings**.
5. Em **Python version**, escolha **3.11**.
6. Na caixa **Secrets**, cole exatamente isto, trocando pela sua linha da
   Etapa 1:

   ```toml
   DATABASE_URL = "postgresql://postgres:SUASENHA@db.abcdefgh.supabase.co:5432/postgres"
   ```

7. Clique em **Save** e depois em **Deploy**.

A primeira publicação leva de 3 a 5 minutos (ele instala as bibliotecas). Se
aparecer uma tela preta com texto rolando, é isso acontecendo — é normal.

---

## Etapa 3 — Conferir que subiu

Abra o endereço do app. Você deve ver a tela de login **Finanças da Casa**.

Como ainda não configuramos as senhas de vocês, o app avisa que está em modo de
desenvolvimento. Entre com:

- **Quem está entrando:** André
- **Senha:** `financas`

Se você entrou e a barra lateral **não** mostra mais o aviso amarelo "Base local
(SQLite)", o Supabase está conectado. Pode seguir.

> **Se aparecer o aviso amarelo de base local**, o `DATABASE_URL` não chegou.
> Volte no Streamlit: menu **⋮** (canto superior direito) › **Settings** ›
> **Secrets**, confira se a linha está lá e se a senha do banco substituiu o
> `[YOUR-PASSWORD]` corretamente. Salve e clique em **Reboot app**.

---

## Etapa 4 — Ligar a análise por IA (opcional)

Pode pular e fazer depois. Sem isso, tudo funciona — só a tela Análise IA e a
classificação automática por IA ficam desligadas (as regras continuam
funcionando normalmente).

1. Acesse **[console.anthropic.com](https://console.anthropic.com)** ›
   **API Keys** › **Create Key**. Copie a chave (começa com `sk-ant-`).
2. Adicione créditos em **Billing** — US$ 5 duram muitos meses no uso de vocês.
3. No Streamlit: **⋮** › **Settings** › **Secrets** e acrescente uma linha:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-cole-a-sua-chave-aqui"
   ```

4. **Save** › **Reboot app**.

---

## Etapa 5 — As senhas de vocês dois

Agora que o resto está funcionando, troque a senha de desenvolvimento pelas
senhas de verdade.

### 5.1 Gerar os códigos das senhas

No seu computador, dentro da pasta do projeto:

```bash
python scripts/gerar_senha.py
```

Ele pede a senha do André e a da Rô (digitando duas vezes cada). **O que você
digita não aparece na tela e não é gravado em lugar nenhum** — só o código
embaralhado (hash) é gerado.

No final ele imprime um bloco assim:

```toml
[usuarios.andre]
nome = "André"
senha_hash = "$2b$12$K8vN...uma sequência longa..."

[usuarios.ro]
nome = "Rô"
senha_hash = "$2b$12$9pQr...outra sequência longa..."
```

> **Não tem Python instalado?** Instale em
> [python.org/downloads](https://www.python.org/downloads/) marcando
> **"Add Python to PATH"** na primeira tela. Depois abra o Prompt de Comando na
> pasta do projeto e rode `pip install bcrypt` antes do comando acima.

### 5.2 Colar no Streamlit

No Streamlit: **⋮** › **Settings** › **Secrets**. O conteúdo final deve ficar
assim (as três partes juntas):

```toml
DATABASE_URL = "postgresql://postgres:SUASENHA@db.abcdefgh.supabase.co:5432/postgres"
ANTHROPIC_API_KEY = "sk-ant-..."

[usuarios.andre]
nome = "André"
senha_hash = "$2b$12$K8vN..."

[usuarios.ro]
nome = "Rô"
senha_hash = "$2b$12$9pQr..."
```

Clique em **Save** e depois em **Reboot app**.

### 5.3 Testar

Abra o app de novo. O aviso de modo de desenvolvimento tem que ter sumido, e a
senha `financas` **não pode mais funcionar**. Entre com a senha nova do André,
saia, e teste a da Rô.

**Pronto.** Mande o endereço para a Rô — ela entra do computador dela, com a
senha dela, e vocês veem exatamente os mesmos dados.

---

## Primeiro uso, na ordem certa

1. **Upload de Extratos › Contas e cartões** — confira as 6 contas. Ajuste os
   nomes, desative o que não existir mais, inclua o que faltar.
2. **Upload de Extratos › Enviar arquivo** — marque **"Planilha da Rô"**, envie
   a planilha dela e confira o mapeamento das colunas na tela.
3. Depois envie os extratos e faturas, um por vez, do mês mais antigo para o
   mais recente.
4. **Upload › Crítica planilha × extratos** — veja o que bateu e o que divergiu.
5. **Classificação** — resolva a fila. Cada correção vira memória e a fila
   encolhe sozinha nos meses seguintes.
6. **Orçamento & Metas** — ajustem juntos os percentuais da renda.
7. **Visão Geral** e **Análise IA** — o resultado.

---

## Coisas que podem dar errado

**"Error installing requirements"** na publicação — quase sempre é a versão do
Python. Vá em **Settings › Advanced** e confirme **3.11**, depois **Reboot app**.

**O app dorme depois de uns dias sem uso.** É normal no plano gratuito. Quem
abrir primeiro espera uns 30 segundos e ele volta, com todos os dados intactos.

**"This app has gone over its resource limits"** — clique em **Reboot app**.

**Esqueceram a senha** — rode o `gerar_senha.py` de novo e substitua o hash nos
Secrets. Não há recuperação de senha, e isso é proposital: ninguém além de
vocês entra.

**Precisa apagar tudo e recomeçar** — no Supabase: **Table Editor**, selecione
as tabelas e delete. Na próxima abertura o app recria tudo vazio.

---

## Segurança, em uma linha

O `secrets.toml` nunca vai para o GitHub (está no `.gitignore`), as senhas são
guardadas como hash bcrypt — não dá para voltar ao texto original — e o banco
do Supabase só aceita conexão com a senha que você gerou na Etapa 1.
