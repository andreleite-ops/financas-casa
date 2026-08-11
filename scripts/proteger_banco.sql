-- Fecha a porta pública do banco. Rode no Supabase › SQL Editor › New query.
--
-- O QUE ISSO RESOLVE
-- O Supabase publica uma API REST em cima do schema `public`, aberta na
-- internet e autenticada pela chave `anon` — que é feita para ser pública.
-- Sem Row-Level Security, quem tiver essa chave lê, altera e apaga qualquer
-- tabela. É disso que o alerta "Table publicly accessible" está falando.
--
-- POR QUE O APP CONTINUA FUNCIONANDO
-- O app não usa essa API. Ele fala Postgres direto, pelo pooler, com o usuário
-- `postgres` — que é superusuário e, por definição, não passa por RLS. Ligar
-- RLS sem criar nenhuma policy fecha a porta da rua e deixa a de dentro aberta.
--
-- Nenhuma policy é criada de propósito: aqui não existe login de usuário no
-- Supabase. Os dois usuários da casa entram pelo app, e é o app que tem a
-- senha do banco.

-- 1) Liga RLS em tudo que existe no schema público, inclusive tabelas que
--    vierem depois — assim não é preciso lembrar de voltar aqui.
DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT tablename FROM pg_tables WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;

-- 2) Tira qualquer permissão que os papéis da API tenham herdado. RLS sozinho
--    já bastaria; isto é o cinto além do suspensório.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL ROUTINES  IN SCHEMA public FROM anon, authenticated;
REVOKE USAGE ON SCHEMA public FROM anon, authenticated;

-- 3) E que o mesmo valha para o que for criado daqui pra frente.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM anon, authenticated;

-- 4) Confere: rls_ativo tem de ser `true` nas dez tabelas.
SELECT tablename AS tabela, rowsecurity AS rls_ativo
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
