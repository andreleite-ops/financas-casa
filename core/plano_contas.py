"""Plano de contas aprovado e dicionario inicial de regras.

E a carga inicial do banco. Depois disso, categorias e subcategorias sao
editadas pelo proprio app (tela Plano de Contas) e as regras crescem sozinhas
com as correcoes manuais.
"""

from __future__ import annotations

RECEITAS: list[tuple[str, list[str]]] = [
    ("Trabalho", ["Pró-labore / Salário", "Bônus / PLR", "Prestação de Serviços", "13º / Férias"]),
    ("Rendimentos", ["Aplicações Financeiras", "Dividendos / JCP", "Aluguéis"]),
    ("Outras Receitas", ["Reembolsos", "Venda de Bens", "Transferências Recebidas"]),
]

DESPESAS: list[tuple[str, list[str]]] = [
    ("Moradia", ["Condomínio", "IPTU", "Energia", "Água / Gás", "Internet / TV / Telefone",
                 "Manutenção & Reforma", "Empregados Domésticos", "Móveis & Decoração"]),
    ("Alimentação", ["No Domicílio", "Fora do Domicílio"]),
    ("Transporte", ["Combustível", "Apps (Uber / 99 / Táxi)", "Estacionamento & Pedágio",
                    "Manutenção do Veículo", "Seguro / IPVA / Licenciamento"]),
    ("Saúde", ["Plano de Saúde", "Médicos & Dentistas", "Farmácia", "Exames", "Terapias",
               "Academia & Bem-estar"]),
    ("Educação", ["Mensalidades & Cursos", "Livros & Materiais", "Idiomas"]),
    # Categoria propria, e nao diluida em Educacao e Saude: pensao e compromisso
    # fixo e nao negociavel, e o que se gasta com os filhos e a pergunta que o
    # Andre faz de verdade — nao daria para responde-la somando pedacos
    # espalhados por cinco categorias.
    ("Filhos & Pensão", ["Pensão Alimentícia", "Escola & Faculdade", "Saúde dos Filhos",
                         "Mesada & Ajuda", "Atividades & Esportes", "Outros Gastos"]),
    ("Lazer & Viagens", ["Viagens", "Passeios & Eventos", "Hobbies", "Clube"]),
    ("Assinaturas & Tecnologia", ["Streaming", "Aplicativos & Software", "Eletrônicos"]),
    ("Vestuário & Cuidados Pessoais", ["Roupas & Calçados", "Cabeleireiro & Estética",
                                       "Perfumaria & Cosméticos"]),
    ("Pets", ["Alimentação", "Veterinário & Farmácia", "Banho & Tosa"]),
    ("Presentes & Doações", ["Presentes", "Doações"]),
    ("Financeiras & Impostos", ["Tarifas Bancárias", "Anuidade de Cartão", "Juros & Multas",
                                "Imposto de Renda", "Seguros (Vida / Residencial)"]),
    ("Poupança & Investimentos", ["Aporte Mensal", "Previdência Privada", "Reserva de Emergência"]),
    ("Outros", ["Saques", "Transferências a Identificar", "A Classificar"]),
    # Nem gasto nem ganho: dinheiro andando entre contas do mesmo dono. Pagar a
    # fatura do cartao aparece dos dois lados — credito na fatura, debito na
    # conta — e sem um lugar proprio isso dobraria a despesa do mes e criaria
    # uma receita que nunca existiu.
    ("Transferências entre Contas", ["Pagamento de Fatura", "Entre Contas Próprias",
                                     "Aplicação / Resgate"]),
]

CONTAS_INICIAIS = [
    # nome, tipo, titular, instituicao, parser
    ("Visa XP", "cartao", "André", "XP", "xp"),
    ("BTG Mastercard", "cartao", "André", "BTG", "btg"),
    ("Nubank Mastercard", "cartao", "Casal", "Nubank", "nubank"),
    ("Bradesco C/C", "corrente", "André", "Bradesco", "bradesco"),
    ("Itaú C/C", "corrente", "Rô", "Itaú", "itau"),
    ("Conjunta C/C", "corrente", "Casal", "Itaú", "itau"),
]

# As tres fontes de renda da casa, ditas pelo Andre. Ficam separadas das demais
# regras porque so elas dizem tambem DE QUEM e o dinheiro, e isso e o que
# impede a dupla contagem: a mesma receita lancada na planilha da Ro e no
# extrato do Andre precisa cair na mesma pessoa nos dois caminhos.
#   TAG  -> Andre (trabalho dele)
#   BIOS -> Ro (trabalho dela)
#   NUN  -> Casal (aluguel do apartamento, que e dos dois)
# (padrao, categoria, subcategoria, pessoa)
REGRAS_RECEITA: list[tuple[str, str, str, str]] = [
    ("TAG", "Trabalho", "Pró-labore / Salário", "André"),
    ("BIOS", "Trabalho", "Pró-labore / Salário", "Rô"),
    ("NUN", "Rendimentos", "Aluguéis", "Casal"),
    # o aluguel do NUN as vezes vem so pelo rotulo, sem o nome na descricao
    ("ALUGUEL", "Rendimentos", "Aluguéis", "Casal"),
    # a venda do apartamento da mae do Andre: patrimonio dele virando saldo.
    # Entra em Venda de Bens, que fica fora da renda base do orcamento.
    ("VENDA APTO", "Outras Receitas", "Venda de Bens", "André"),
    ("VENDA DE APTO", "Outras Receitas", "Venda de Bens", "André"),
    ("VENDA APARTAMENTO", "Outras Receitas", "Venda de Bens", "André"),
    ("VENDA IMOVEL", "Outras Receitas", "Venda de Bens", "André"),
    ("VENDA DE IMOVEL", "Outras Receitas", "Venda de Bens", "André"),
]

# (padrao, categoria, subcategoria) - casam por "contem" sobre a descricao
# normalizada (maiuscula, sem acento). Prioridade menor = avaliada primeiro.
REGRAS_INICIAIS: list[tuple[str, str, str]] = [
    # Alimentacao - fora do domicilio
    ("IFOOD", "Alimentação", "Fora do Domicílio"),
    ("RAPPI", "Alimentação", "Fora do Domicílio"),
    ("UBER EATS", "Alimentação", "Fora do Domicílio"),
    ("RESTAURANTE", "Alimentação", "Fora do Domicílio"),
    ("PIZZARIA", "Alimentação", "Fora do Domicílio"),
    ("CHURRASCARIA", "Alimentação", "Fora do Domicílio"),
    ("LANCHONETE", "Alimentação", "Fora do Domicílio"),
    ("BAR ", "Alimentação", "Fora do Domicílio"),
    ("CAFE", "Alimentação", "Fora do Domicílio"),
    ("STARBUCKS", "Alimentação", "Fora do Domicílio"),
    ("MC DONALDS", "Alimentação", "Fora do Domicílio"),
    ("MCDONALDS", "Alimentação", "Fora do Domicílio"),
    ("BURGER", "Alimentação", "Fora do Domicílio"),
    ("SUBWAY", "Alimentação", "Fora do Domicílio"),
    ("OUTBACK", "Alimentação", "Fora do Domicílio"),
    ("PADARIA", "Alimentação", "Fora do Domicílio"),
    # Alimentacao - no domicilio
    ("SUPERMERCADO", "Alimentação", "No Domicílio"),
    ("SUPERM", "Alimentação", "No Domicílio"),
    ("PAO DE ACUCAR", "Alimentação", "No Domicílio"),
    ("CARREFOUR", "Alimentação", "No Domicílio"),
    ("EXTRA ", "Alimentação", "No Domicílio"),
    ("ASSAI", "Alimentação", "No Domicílio"),
    ("SENDAS", "Alimentação", "No Domicílio"),
    ("HORTIFRUTI", "Alimentação", "No Domicílio"),
    ("ST MARCHE", "Alimentação", "No Domicílio"),
    ("EATALY", "Alimentação", "No Domicílio"),
    ("ACOUGUE", "Alimentação", "No Domicílio"),
    ("EMPORIO", "Alimentação", "No Domicílio"),
    ("ZONA SUL", "Alimentação", "No Domicílio"),
    ("PRINCESA", "Alimentação", "No Domicílio"),
    # Transporte
    ("UBER", "Transporte", "Apps (Uber / 99 / Táxi)"),
    ("99APP", "Transporte", "Apps (Uber / 99 / Táxi)"),
    ("99 TECNOLOGIA", "Transporte", "Apps (Uber / 99 / Táxi)"),
    ("CABIFY", "Transporte", "Apps (Uber / 99 / Táxi)"),
    ("TAXI", "Transporte", "Apps (Uber / 99 / Táxi)"),
    ("POSTO", "Transporte", "Combustível"),
    ("IPIRANGA", "Transporte", "Combustível"),
    ("SHELL", "Transporte", "Combustível"),
    ("PETROBRAS DISTRIB", "Transporte", "Combustível"),
    ("ESTACIONAMENTO", "Transporte", "Estacionamento & Pedágio"),
    ("ESTAPAR", "Transporte", "Estacionamento & Pedágio"),
    ("CONECTCAR", "Transporte", "Estacionamento & Pedágio"),
    ("SEM PARAR", "Transporte", "Estacionamento & Pedágio"),
    ("VELOE", "Transporte", "Estacionamento & Pedágio"),
    ("IPVA", "Transporte", "Seguro / IPVA / Licenciamento"),
    ("DETRAN", "Transporte", "Seguro / IPVA / Licenciamento"),
    ("PORTO SEGURO AUTO", "Transporte", "Seguro / IPVA / Licenciamento"),
    # Saude
    ("DROGARIA", "Saúde", "Farmácia"),
    ("DROGASIL", "Saúde", "Farmácia"),
    ("RAIA", "Saúde", "Farmácia"),
    ("PACHECO", "Saúde", "Farmácia"),
    ("FARMACIA", "Saúde", "Farmácia"),
    ("PANVEL", "Saúde", "Farmácia"),
    ("UNIMED", "Saúde", "Plano de Saúde"),
    ("AMIL", "Saúde", "Plano de Saúde"),
    ("BRADESCO SAUDE", "Saúde", "Plano de Saúde"),
    ("SULAMERICA SAUDE", "Saúde", "Plano de Saúde"),
    ("HOSPITAL", "Saúde", "Médicos & Dentistas"),
    ("CLINICA", "Saúde", "Médicos & Dentistas"),
    ("ODONTO", "Saúde", "Médicos & Dentistas"),
    ("LABORATORIO", "Saúde", "Exames"),
    ("FLEURY", "Saúde", "Exames"),
    ("DASA", "Saúde", "Exames"),
    ("SMARTFIT", "Saúde", "Academia & Bem-estar"),
    ("SMART FIT", "Saúde", "Academia & Bem-estar"),
    ("ACADEMIA", "Saúde", "Academia & Bem-estar"),
    ("BODYTECH", "Saúde", "Academia & Bem-estar"),
    # Moradia
    ("CONDOMINIO", "Moradia", "Condomínio"),
    ("IPTU", "Moradia", "IPTU"),
    ("LIGHT SERVICOS", "Moradia", "Energia"),
    ("ENEL", "Moradia", "Energia"),
    ("CEMIG", "Moradia", "Energia"),
    ("ELETROPAULO", "Moradia", "Energia"),
    ("CPFL", "Moradia", "Energia"),
    ("SABESP", "Moradia", "Água / Gás"),
    ("CEDAE", "Moradia", "Água / Gás"),
    ("COMGAS", "Moradia", "Água / Gás"),
    ("NATURGY", "Moradia", "Água / Gás"),
    ("VIVO", "Moradia", "Internet / TV / Telefone"),
    ("CLARO", "Moradia", "Internet / TV / Telefone"),
    ("TIM ", "Moradia", "Internet / TV / Telefone"),
    ("OI FIBRA", "Moradia", "Internet / TV / Telefone"),
    ("NET SERVICOS", "Moradia", "Internet / TV / Telefone"),
    ("LEROY MERLIN", "Moradia", "Manutenção & Reforma"),
    ("TELHANORTE", "Moradia", "Manutenção & Reforma"),
    ("TOK STOK", "Moradia", "Móveis & Decoração"),
    ("MOBLY", "Moradia", "Móveis & Decoração"),
    # Assinaturas & tecnologia
    ("NETFLIX", "Assinaturas & Tecnologia", "Streaming"),
    ("SPOTIFY", "Assinaturas & Tecnologia", "Streaming"),
    ("DISNEY", "Assinaturas & Tecnologia", "Streaming"),
    ("HBO", "Assinaturas & Tecnologia", "Streaming"),
    ("PRIME VIDEO", "Assinaturas & Tecnologia", "Streaming"),
    ("GLOBOPLAY", "Assinaturas & Tecnologia", "Streaming"),
    ("YOUTUBE PREMIUM", "Assinaturas & Tecnologia", "Streaming"),
    ("APPLE.COM", "Assinaturas & Tecnologia", "Aplicativos & Software"),
    ("GOOGLE ", "Assinaturas & Tecnologia", "Aplicativos & Software"),
    ("MICROSOFT", "Assinaturas & Tecnologia", "Aplicativos & Software"),
    ("OPENAI", "Assinaturas & Tecnologia", "Aplicativos & Software"),
    ("ANTHROPIC", "Assinaturas & Tecnologia", "Aplicativos & Software"),
    ("ICLOUD", "Assinaturas & Tecnologia", "Aplicativos & Software"),
    ("AMAZON", "Assinaturas & Tecnologia", "Eletrônicos"),
    ("KABUM", "Assinaturas & Tecnologia", "Eletrônicos"),
    # Vestuario & cuidados
    ("RENNER", "Vestuário & Cuidados Pessoais", "Roupas & Calçados"),
    ("ZARA", "Vestuário & Cuidados Pessoais", "Roupas & Calçados"),
    ("RIACHUELO", "Vestuário & Cuidados Pessoais", "Roupas & Calçados"),
    ("C&A", "Vestuário & Cuidados Pessoais", "Roupas & Calçados"),
    ("CENTAURO", "Vestuário & Cuidados Pessoais", "Roupas & Calçados"),
    ("NIKE", "Vestuário & Cuidados Pessoais", "Roupas & Calçados"),
    ("CABELEIREIRO", "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética"),
    ("SALAO", "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética"),
    ("BARBEARIA", "Vestuário & Cuidados Pessoais", "Cabeleireiro & Estética"),
    ("SEPHORA", "Vestuário & Cuidados Pessoais", "Perfumaria & Cosméticos"),
    ("BOTICARIO", "Vestuário & Cuidados Pessoais", "Perfumaria & Cosméticos"),
    ("NATURA", "Vestuário & Cuidados Pessoais", "Perfumaria & Cosméticos"),
    # Lazer & viagens
    ("LATAM", "Lazer & Viagens", "Viagens"),
    ("GOL LINHAS", "Lazer & Viagens", "Viagens"),
    ("AZUL LINHAS", "Lazer & Viagens", "Viagens"),
    ("BOOKING", "Lazer & Viagens", "Viagens"),
    ("AIRBNB", "Lazer & Viagens", "Viagens"),
    ("DECOLAR", "Lazer & Viagens", "Viagens"),
    ("HOTEL", "Lazer & Viagens", "Viagens"),
    ("CINEMARK", "Lazer & Viagens", "Passeios & Eventos"),
    ("CINEMA", "Lazer & Viagens", "Passeios & Eventos"),
    ("INGRESSO.COM", "Lazer & Viagens", "Passeios & Eventos"),
    ("SYMPLA", "Lazer & Viagens", "Passeios & Eventos"),
    ("TEATRO", "Lazer & Viagens", "Passeios & Eventos"),
    # Pets
    ("PETZ", "Pets", "Veterinário & Farmácia"),
    ("COBASI", "Pets", "Veterinário & Farmácia"),
    ("VETERINAR", "Pets", "Veterinário & Farmácia"),
    ("PET SHOP", "Pets", "Banho & Tosa"),
    ("PETSHOP", "Pets", "Banho & Tosa"),
    # Educacao
    ("COLEGIO", "Educação", "Mensalidades & Cursos"),
    ("ESCOLA", "Educação", "Mensalidades & Cursos"),
    ("UNIVERSIDADE", "Educação", "Mensalidades & Cursos"),
    ("FGV", "Educação", "Mensalidades & Cursos"),
    ("UDEMY", "Educação", "Mensalidades & Cursos"),
    ("LIVRARIA", "Educação", "Livros & Materiais"),
    ("KINDLE", "Educação", "Livros & Materiais"),
    ("CULTURA INGLESA", "Educação", "Idiomas"),
    ("WIZARD", "Educação", "Idiomas"),
    # Financeiras & impostos
    ("TARIFA", "Financeiras & Impostos", "Tarifas Bancárias"),
    ("CESTA DE SERVICOS", "Financeiras & Impostos", "Tarifas Bancárias"),
    ("PACOTE DE SERVICOS", "Financeiras & Impostos", "Tarifas Bancárias"),
    ("ANUIDADE", "Financeiras & Impostos", "Anuidade de Cartão"),
    ("JUROS", "Financeiras & Impostos", "Juros & Multas"),
    ("MULTA", "Financeiras & Impostos", "Juros & Multas"),
    ("IOF", "Financeiras & Impostos", "Juros & Multas"),
    # Filhos & pensao
    ("PENSAO ALIMENTICIA", "Filhos & Pensão", "Pensão Alimentícia"),
    ("PENSAO", "Filhos & Pensão", "Pensão Alimentícia"),
    ("MESADA", "Filhos & Pensão", "Mesada & Ajuda"),
    # os dois lados do pagamento de fatura
    ("PAGAMENTO RECEBIDO", "Transferências entre Contas", "Pagamento de Fatura"),
    ("PAGAMENTO DE FATURA", "Transferências entre Contas", "Pagamento de Fatura"),
    ("PAGTO FATURA", "Transferências entre Contas", "Pagamento de Fatura"),
    ("PAGAMENTO EFETUADO", "Transferências entre Contas", "Pagamento de Fatura"),
    # a fatura em PDF do Nubank chama o pagamento da fatura anterior de
    # "Pagamento em 21 JUL". Sem esta regra ele fica sem categoria e, por ser
    # entrada, é somado como receita — uma renda que nunca existiu.
    ("PAGAMENTO EM", "Transferências entre Contas", "Pagamento de Fatura"),
    ("PAGAMENTO CARTAO", "Transferências entre Contas", "Pagamento de Fatura"),
    ("DARF", "Financeiras & Impostos", "Imposto de Renda"),
    ("IRPF", "Financeiras & Impostos", "Imposto de Renda"),
    ("IMPOSTO DE RENDA", "Financeiras & Impostos", "Imposto de Renda"),
    # o IR da venda do apartamento: sai como despesa, no mesmo mes da venda
    ("GANHO DE CAPITAL", "Financeiras & Impostos", "Imposto de Renda"),
    ("IR GANHO DE CAPITAL", "Financeiras & Impostos", "Imposto de Renda"),
    ("IR VENDA", "Financeiras & Impostos", "Imposto de Renda"),
    ("SEGURO DE VIDA", "Financeiras & Impostos", "Seguros (Vida / Residencial)"),
    ("PORTO SEGURO RESID", "Financeiras & Impostos", "Seguros (Vida / Residencial)"),
    # Poupanca & investimentos
    ("APLICACAO", "Poupança & Investimentos", "Aporte Mensal"),
    ("APLIC AUTOMATICA", "Poupança & Investimentos", "Aporte Mensal"),
    ("CDB", "Poupança & Investimentos", "Aporte Mensal"),
    ("TESOURO DIRETO", "Poupança & Investimentos", "Aporte Mensal"),
    ("PREVIDENCIA", "Poupança & Investimentos", "Previdência Privada"),
    ("PGBL", "Poupança & Investimentos", "Previdência Privada"),
    ("VGBL", "Poupança & Investimentos", "Previdência Privada"),
    # Receitas
    ("PRO LABORE", "Trabalho", "Pró-labore / Salário"),
    ("PRO-LABORE", "Trabalho", "Pró-labore / Salário"),
    ("PROLABORE", "Trabalho", "Pró-labore / Salário"),
    ("SALARIO", "Trabalho", "Pró-labore / Salário"),
    ("REMUNERACAO", "Trabalho", "Pró-labore / Salário"),
    ("BONUS", "Trabalho", "Bônus / PLR"),
    ("PLR", "Trabalho", "Bônus / PLR"),
    ("PARTICIPACAO NOS LUCROS", "Trabalho", "Bônus / PLR"),
    ("13 SALARIO", "Trabalho", "13º / Férias"),
    ("DECIMO TERCEIRO", "Trabalho", "13º / Férias"),
    ("FERIAS", "Trabalho", "13º / Férias"),
    ("HONORARIOS", "Trabalho", "Prestação de Serviços"),
    ("NF SERVICO", "Trabalho", "Prestação de Serviços"),
    ("CONSULTORIA", "Trabalho", "Prestação de Serviços"),
    ("RENDIMENTO", "Rendimentos", "Aplicações Financeiras"),
    ("RESGATE", "Rendimentos", "Aplicações Financeiras"),
    ("DIVIDENDO", "Rendimentos", "Dividendos / JCP"),
    ("JCP", "Rendimentos", "Dividendos / JCP"),
    ("JUROS S/ CAPITAL", "Rendimentos", "Dividendos / JCP"),
    ("ALUGUEL RECEB", "Rendimentos", "Aluguéis"),
    # Outros
    ("SAQUE", "Outros", "Saques"),
    ("PRESENTE", "Presentes & Doações", "Presentes"),
    ("DOACAO", "Presentes & Doações", "Doações"),
]

# Metas iniciais (% da renda mensal) - editaveis na tela Orcamento
# Categorias que sao, por natureza, de uma pessoa so. Pensao e gasto com os
# filhos e do Andre: nao e gasto da casa a ser rateado, e o relatorio por pessoa
# so faz sentido se essa distincao existir. Vale para qualquer lancamento que
# caia na categoria, venha ele da planilha ou de um extrato.
DONO_POR_CATEGORIA = {
    "Filhos & Pensão": "André",
}

METAS_INICIAIS = {
    "Poupança & Investimentos": 20.0,
    "Moradia": 20.0,
    "Alimentação": 12.0,
    "Lazer & Viagens": 8.0,
    "Saúde": 8.0,
    "Transporte": 6.0,
    "Educação": 4.0,
    "Filhos & Pensão": 0.0,   # sem chute: o André define na tela de Orçamento
    "Assinaturas & Tecnologia": 3.0,
    "Vestuário & Cuidados Pessoais": 4.0,
    "Financeiras & Impostos": 5.0,
    "Presentes & Doações": 2.0,
    "Pets": 2.0,
    "Outros": 6.0,
}
