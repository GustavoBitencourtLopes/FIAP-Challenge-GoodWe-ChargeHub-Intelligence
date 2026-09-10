#  ChargeGrid Intelligence

> Simulador inteligente de uma rede de carregadores de veículos elétricos em ambientes comerciais, desenvolvido para o **FIAP EV Challenge 2026**, em parceria com a **GoodWe**.

Projeto do 1º ano presencial — foco no setor comercial e varejo (outlets), com otimização em tempo real, controle de demanda, tarifação dinâmica e inteligência artificial aplicada.

🔗 **Demo ao vivo**: [fiap-challenge-goodwe-chargehub.onrender.com](https://fiap-challenge-goodwe-chargehub.onrender.com)

> ⚠️ O projeto está hospedado no plano gratuito do Render. Se o site estiver "dormindo" por inatividade, o primeiro carregamento pode levar de 30 a 50 segundos. 

---

## 📖 Sobre o projeto

O desafio proposto pela GoodWe vai além de simplesmente carregar um veículo elétrico: a proposta é transformar cada sessão de recarga em dados estruturados e inteligência acionável para o outlet.

A ideia central: um cliente chega a um outlet para carregar seu carro elétrico e precisa esperar um período. Esse tempo de espera é uma oportunidade — tanto para o cliente (que pode aproveitar para fazer compras) quanto para o outlet (que pode transformar isso em receita através de recomendações, cupons e cashback).

O **ChargeGrid Intelligence** simula essa operação de ponta a ponta: desde o cadastro do usuário e do veículo, passando pela escolha do carregador e simulação da sessão, até o pagamento e o acompanhamento via um assistente de IA.

⚠️ **Importante**: este é um sistema de **simulação**. Não há integração com hardware real de carregadores — todos os dados de ocupação, tempo e disponibilidade são gerados por um sistema de regras que simula o comportamento de uma operação real.

---

## ✨ Funcionalidades

### Conta e veículo
- Cadastro e login de usuários
- Onboarding em duas etapas: primeiro a conta, depois o veículo (marca, modelo, placa, ano e capacidade da bateria)
- Edição e exclusão do veículo cadastrado

### Simulação de carregamento
- **4 postos de carregamento heterogêneos**: 2x AC 7kW, 1x AC 22kW e 1x DC Rápido 60kW (exclusivo para assinantes VIP)
- O usuário escolhe manualmente qual posto usar, com status ao vivo (disponível / ocupado / em manutenção)
- Cálculo de tempo de carga respeitando a **curva de carregamento real** de baterias de íon-lítio (rápido até 80%, mais lento depois disso)
- Detecção automática de **horário de pico** (08h-10h e 18h-21h, fuso de São Paulo) com taxa extra na tarifa
- Tarifação dinâmica conforme o nível de lotação do outlet (baixa / moderada / alta demanda), com desconto em horários de baixa demanda

### Comercial e pagamento
- Sistema de ofertas/cupons de lojas do outlet, gerado a cada sessão para incentivar o consumo durante a espera
- Cupons resgatáveis, salvos permanentemente no perfil do usuário ("Meus cupons")
- Fluxo de pagamento simulado realista: cartão de crédito/débito (com máscara, detecção de bandeira e parcelamento), Pix (QR code e cronômetro) e carteira digital
- Recibo de compra ao final do pagamento

### Assinatura VIP
- Plano de assinatura que libera acesso exclusivo ao posto DC Rápido 60kW (o mais veloz do outlet)
- Fluxo de assinatura e cancelamento com pagamento simulado

### Inteligência artificial
- Assistente de chat (**Relatórios & IA**) via **Google Gemini**, que:
  - Recomenda o melhor horário para carregar
  - Indica lojas do outlet com promoções ativas (baseado no catálogo real do sistema)
  - Explica a diferença entre os tipos de carregador
  - Responde dúvidas gerais sobre a plataforma e sobre carros elétricos

### Histórico
- Histórico completo de sessões de carregamento, com data, energia consumida, valor pago e forma de pagamento

---

## 🛠️ Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python, Flask, Flask-SQLAlchemy |
| Banco de dados | SQLite (desenvolvimento) — estrutura já compatível com PostgreSQL |
| Inteligência artificial | Google Gemini API (camada gratuita) |
| Frontend | HTML, CSS e JavaScript puro (sem frameworks) |
| Deploy | Render (Gunicorn) |

---

## 📁 Estrutura do projeto

```
ChargeGrid-Intelligence/
│
├── backend/
│   ├── main.py                # aplicação Flask (rotas, modelos, lógica de negócio e IA)
│   └── requirements.txt       # dependências Python
│
├── frontend/
│   ├── partials/               # navbar, footer e botão "voltar ao topo" reutilizáveis
│   ├── static/
│   │   ├── css/style.css       # estilos de todo o site
│   │   ├── js/main.js          # comportamentos compartilhados (navbar, scroll)
│   │   └── img/                # logos e imagens
│   ├── tela_principal.html     # landing page
│   ├── login.html / cadastro.html / cadastro_veiculo.html
│   ├── painel.html             # painel principal pós-login
│   ├── carregamento.html       # simulação de carregamento
│   ├── historico.html
│   ├── minha_conta.html
│   ├── meus_veiculos.html / veiculo_detalhe.html / veiculo_editar.html
│   ├── relatorios_ia.html      # assistente de IA
│   └── vip.html
│
├── database/
│   └── schema.sql              # schema de referência para PostgreSQL
│
├── Procfile                     # comando de inicialização para o Render
├── .env.example                 # modelo de variáveis de ambiente
├── .gitignore
└── README.md
```

---

## 🚀 Como rodar localmente

### 1. Clone o repositório
```bash
git clone https://github.com/<seu-usuario>/FIAP-Challenge-GoodWe-ChargeHub-Intelligence.git
cd FIAP-Challenge-GoodWe-ChargeHub-Intelligence
```

### 2. Instale as dependências
```bash
pip install -r backend/requirements.txt
```

### 3. Configure as variáveis de ambiente
Copie o arquivo `.env.example` para um novo arquivo `.env` na **raiz do projeto** e preencha:

```
GOOGLE_API_KEY=sua_chave_do_google_ai_studio
SECRET_KEY=qualquer-texto-aleatorio-longo
```

A chave gratuita do Gemini pode ser gerada em [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — não é necessário cartão de crédito.

### 4. Rode o servidor
```bash
cd backend
python main.py
```

Acesse em [http://127.0.0.1:5000](http://127.0.0.1:5000)

> O banco de dados SQLite é criado automaticamente na primeira execução (`backend/chargegrid.db`).

---

## 🔑 Variáveis de ambiente

| Variável | Descrição | Obrigatória |
|---|---|---|
| `GOOGLE_API_KEY` | Chave da API do Google Gemini, usada pelo assistente de IA | Sim, para o assistente funcionar |
| `SECRET_KEY` | Chave secreta usada pelo Flask para assinar sessões | Sim |

---

## ☁️ Deploy (Render)

O projeto está publicado em **[fiap-challenge-goodwe-chargehub.onrender.com](https://fiap-challenge-goodwe-chargehub.onrender.com)**.

Configuração usada no Render:
- **Build Command**: `pip install -r backend/requirements.txt`
- **Start Command**: `gunicorn main:app --chdir backend`
- Variáveis `GOOGLE_API_KEY` e `SECRET_KEY` configuradas em **Settings → Environment** do serviço (o arquivo `.env` local não é enviado automaticamente para o Render)

> ⚠️ No plano gratuito do Render, o banco SQLite é apagado a cada novo deploy ou período de inatividade — os dados não são persistentes entre reinicializações.

---

## 🧠 Sobre o sistema de inteligência

O projeto usa dois níveis de "inteligência" distintos, por design:

1. **Sistema baseado em regras** (sem custo): decide horário de pico, lotação do outlet, atribuição de carregadores, tarifação dinâmica e geração de ofertas — tudo determinístico/probabilístico, sem depender de nenhuma API externa.
2. **IA generativa real** (Google Gemini, camada gratuita): usada exclusivamente no assistente de chat, com o catálogo de lojas e as especificações dos carregadores injetados diretamente no prompt, para que as respostas sejam baseadas em dados reais do sistema.

---

## 🎓 Contexto acadêmico

Projeto desenvolvido para o **FIAP EV Challenge 2026**, em parceria com a **GoodWe**, como parte da avaliação que considera:

- Arquitetura funcional (Data Flow)
- Papel da inteligência artificial na solução
- Aderência ao contexto do desafio
- Visão de produto real

---

## 👤 Autores

**Gustavo Bitencourt Lopes** - Líder do grupo
**Leonardo Takachi**
**Daniel Vieira**
**Giovani Salazar**


---

## 📄 Licença

Projeto acadêmico desenvolvido para fins educacionais no âmbito do FIAP EV Challenge 2026.
