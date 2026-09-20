# Backend — Assistente de WhatsApp (Procon)

Pipeline de PLN orquestrado por um Gateway para responder dúvidas de consumidores via WhatsApp.

## Arquitetura

```
[Usuário WhatsApp]
       ↓ mensagem
[whatsapp-bot]  — observa mensagens via whatsapp-web.js
       ↓ POST /api/v1/process-message?keyword=<FILTER_KEYWORD>
[service-manager]  — Gateway: aplica filtro de keyword e orquestra
       ↓ POST /api/fasttext  (somente se mensagem contiver a keyword)
[pln-pipeline]  — processa o texto com FastText e retorna class_response
       ↑ class_response
[service-manager]
       ↑ { class_response }
[whatsapp-bot]
       ↑ msg.reply(class_response)
[Usuário WhatsApp]
```

## Serviços

| Serviço | Diretório | Porta | Publicada no host? | Tecnologia |
|---|---|---|---|---|
| `pln-pipeline` | `services/pln_pipeline` | `8001` | não | Python / FastAPI |
| `service-manager` | `services/service_manager` | `8002` | **sim** | Python / FastAPI |
| `whatsapp-bot` | `services/whatsapp_bot` | `8003` | não | Node.js / Express |

Só o `service-manager` tem porta no host, porque o painel roda no navegador do
usuário e chama a API direto. Os outros dois conversam apenas pela rede interna do
compose: publicar `8001` deixava `/api/rag_remote` (que gasta crédito do Gemini)
aberto na máquina, e publicar `8003` deixava o QR de pareamento do WhatsApp
acessível a qualquer um.

## Pré-requisitos

- [Docker](https://www.docker.com/) e Docker Compose instalados
- Acesso a um número de WhatsApp para escanear o QR Code

## Como subir

Na pasta `backend`:

```bash
docker compose up --build
```

Para rodar em background:

```bash
docker compose up --build -d
```

Para parar:

```bash
docker compose down
```

## Primeiro uso (QR Code)

Ao subir pela primeira vez, o `whatsapp-bot` exibirá um QR Code no terminal. Escaneie-o com o WhatsApp do número que será o assistente:

```bash
docker compose logs -f whatsapp-bot
```

1. Abra o WhatsApp no celular
2. Vá em **Dispositivos conectados → Conectar dispositivo**
3. Escaneie o QR Code exibido no log do container `whatsapp_bot`

Se o QR do terminal não escanear bem, dá para pegá-lo como PNG — mas a rota exige
o `BOT_SECRET`, porque quem lê esse QR conecta o próprio aparelho na conta de
atendimento:

```bash
# de dentro da rede do compose (a porta 8003 não é publicada)
docker compose exec service-manager \
    python -c "import httpx,os; open('/tmp/qr.png','wb').write(httpx.get('http://whatsapp-bot:8003/qr', headers={'X-Bot-Secret': os.environ['BOT_SECRET']}).content)"
docker compose cp service-manager:/tmp/qr.png ./qr.png
```

No navegador, `GET /qr?secret=<BOT_SECRET>` também funciona (o navegador não tem
como mandar header) — mas isso exige publicar a porta 8003 temporariamente.

### Pelo painel (recomendado para quem não usa terminal)

O painel tem a página **Conexão**, em `/dashboard/conexao`: mostra o estado do
pareamento e o QR, que se renova sozinho a cada 20 s.

Ela é restrita a **uma conta**, a de `ADMIN_EMAIL` — não ao cargo de gestor.
Quem lê aquele QR conecta o próprio aparelho na conta de atendimento, então nem
todo gestor chega lá. O item só aparece no menu para essa conta, e o backend
recusa as rotas `/api/v1/bot/status` e `/api/v1/bot/qr` para qualquer outra:

| Quem chama | Resposta |
|---|---|
| conta de `ADMIN_EMAIL` | `200` |
| outro gestor ou analista | `403 Acesso restrito ao administrador do sistema` |
| sem token | `401` |
| com o `X-Bot-Secret` | `401` — a credencial do bot serve ao atendimento, não ao pareamento |

O painel precisa saber qual é essa conta para decidir se mostra o menu. Como
`NEXT_PUBLIC_*` é embutida no build, ela vai como argumento de build no compose
do frontend (`NEXT_PUBLIC_SUPER_ADMIN_EMAIL`) e **precisa ser o mesmo valor** do
`ADMIN_EMAIL` daqui. Se divergir, o menu some para quem deveria vê-lo — mas o
acesso continua correto, porque quem decide é o backend.

A porta 8003 continua fechada: o service-manager busca o QR pela rede interna e
repassa a imagem já autenticada.

A sessão é salva localmente em `services/whatsapp_bot/.wwebjs_auth` e não precisa ser re-autenticada nas próximas subidas.

## Variáveis de ambiente

As variáveis são configuradas no `docker-compose.yml`, nos serviços `whatsapp-bot` e `service-manager`:

| Variável | Padrão | Descrição |
|---|---|---|
| `GATEWAY_URL` | `http://service_manager:8002/api/v1/process-message` | URL do Gateway (service-manager) |
| `FILTER_KEYWORD` | `Procon` | Keyword que a mensagem deve conter para ser processada. Mensagens sem essa keyword são ignoradas. |
| `BOT_SECRET` | `dev-bot-secret-change-me` | Segredo exigido em `POST /send` e `GET /qr`. **Trocar em produção.** |
| `API_BIND` | `0.0.0.0` | Interface em que a porta 8002 do `service-manager` responde. Use `127.0.0.1` para limitar à própria máquina enquanto não houver proxy reverso com TLS. |
| `CORS_ORIGINS` | `http://localhost:3000` | Origens do painel que o navegador pode usar para chamar a API, separadas por vírgula. Precisa mudar sempre que o painel não for aberto em `localhost`. |
| `RAG_FALLBACK_ENABLED` | `false` | Liga a contingência por RAG (a "IA Avançada"). **Desligada a pedido do cliente.** |

### O painel em outra máquina (CORS)

O painel roda no navegador do analista e chama a API direto, então `localhost`
só funciona para quem abre o painel na própria VM. Para o cliente usar da rede,
duas variáveis mudam juntas — e é sempre o mesmo endereço, só a porta muda:

```bash
# na máquina do backend, antes do docker compose up
CORS_ORIGINS=http://10.0.0.5:3000     # de onde o painel é aberto (backend)
NEXT_PUBLIC_API_URL=http://10.0.0.5:8002   # para onde o painel chama (frontend)
```

`NEXT_PUBLIC_API_URL` entra na imagem no build, não em runtime: mudou, rebuilda
o frontend.

Duas armadilhas, as duas silenciosas — o painel abre normalmente e toda chamada
falha no console do navegador, sem nada no log da API:

- **barra no fim** (`http://10.0.0.5:3000/`) — o `CORS_ORIGINS` tolera e remove;
  o `NEXT_PUBLIC_API_URL` não, vira `//api/v1/...`.
- **`https` onde o painel serve `http`** — origens diferentes para o navegador.
  Quando entrar o proxy reverso com TLS, as duas variáveis mudam junto.

Definir `CORS_ORIGINS` substitui a lista inteira: para continuar desenvolvendo
contra `localhost` ao mesmo tempo, mantenha as duas
(`CORS_ORIGINS=http://localhost:3000,http://10.0.0.5:3000`).

### A contingência por RAG ("IA Avançada")

Quando o DistilBERT não entende a pergunta, existe um segundo caminho: uma LLM
remota tenta responder a partir do FAQ, e a resposta chega ao cliente prefixada
com `*[IA Avançada - Contingência]*`.

Ela está **desligada**. Para reativar, troque no `docker-compose.yml` (ou defina
no `.env`):

```yaml
RAG_FALLBACK_ENABLED: "true"
```

Só um valor afirmativo explícito liga (`true`, `1`, `yes`, `on`, `sim`). Qualquer
outra coisa — inclusive um valor mal digitado como `ture` — deixa desligado, de
propósito: um erro de digitação não pode reativar sozinho algo que o cliente
pediu para não usar.

**Consequência operacional de manter desligada:** a contingência era o que
recuperava uma pergunta não entendida antes de ela virar falha. Sem ela, toda
resposta não entendida conta uma falha e a terceira manda a conversa para a fila
humana. Espere mais atendimentos chegando ao analista.

O estado aparece no log de subida do bot:

```text
Bot na porta 8003 [modo: development, palavra-chave: procon, IA avancada: desligada]
```

Para alterar a keyword sem rebuild, edite o `docker-compose.yml`:

```yaml
environment:
  - FILTER_KEYWORD=SuaKeywordAqui
```

## Health checks

Do host, só o `service-manager` responde:

```bash
curl http://localhost:8002/api/v1/health
```

Os outros dois são alcançáveis de dentro da rede do compose:

```bash
docker compose exec pln-pipeline  python -c "import httpx; print(httpx.get('http://localhost:8001/api/health').json())"
docker compose exec whatsapp-bot  node -e "fetch('http://localhost:8003/api/v1/health').then(r=>r.json()).then(console.log)"
```

Exemplo de resposta:

```json
{
    "status": "ok",
    "service": "pln-pipeline"
}
```

## Como rodar os testes

Todos os comandos abaixo saem da pasta `backend`.

### Service Manager (pytest)

Suíte principal: login, cadastro/permissão de analista, consumidores, máquina de
estados da conversa, handover ao vivo, mensagens, avaliações e o inventário de
autenticação das rotas. Roda em SQLite na memória, sem Postgres e sem nenhum
container no ar além do próprio.

```bash
docker compose build service-manager
docker compose run --rm --no-deps -v "$PWD/services/service_manager/tests:/app/tests" service-manager pytest /app/tests -q
```

Resultado esperado:

```text
309 passed, 6 xfailed
```

Os 6 `xfailed` não são falhas de execução: são bugs conhecidos, cada um com o
motivo escrito no próprio teste. Quando o bug for corrigido, o teste vira
`XPASS` e o marcador `@pytest.mark.xfail` deve ser removido.

Para ver o que cada um documenta:

```bash
docker compose run --rm --no-deps -v "$PWD/services/service_manager/tests:/app/tests" service-manager pytest /app/tests -q -rx
```

### Smoke test ponta a ponta (stack no ar)

Cobre o que a suíte em SQLite não alcança: Postgres real, migração das tabelas,
seed do admin, CORS e a ligação service-manager → whatsapp-bot. Não envia nada no
WhatsApp (usa um número inexistente de propósito).

```bash
docker compose up -d
docker compose run --rm --no-deps -v "$PWD/services/service_manager/tests:/app/tests" service-manager \
    python /app/tests/e2e_smoke.py --api http://service-manager:8002 \
    --pln http://pln-pipeline:8001 --bot http://whatsapp-bot:8003
```

Ou, de fora dos containers (precisa de `httpx` instalado):

```bash
python services/service_manager/tests/e2e_smoke.py
```

Ele imprime, no fim, o SQL para apagar os registros que criou.

### PLN Pipeline (unittest)

`app/tests/` está no `.dockerignore`, então os testes **não** vão para a imagem:
é preciso montar a pasta na hora de rodar.

```bash
docker compose run --rm --no-deps -v "$PWD/services/pln_pipeline/app/tests:/app/app/tests" pln-pipeline \
    python -m unittest app.tests.test_api_routes
```

Resultado esperado: `Ran 52 tests ... OK`.

Cobre as rotas `/api/health`, `/api/distilbert`, `/api/rag_remote`,
`/api/retraining-dataset` e as quatro de vetorização, além das regras de limiar
do KNN e dos auxiliares do RAG. DistilBERT e Gemini são substituídos por dublês,
então não há download de modelo nem chamada paga.

Os scripts antigos, que precisam do serviço no ar, continuam valendo:

```bash
docker compose up -d pln-pipeline
docker compose run --rm --no-deps -v "$PWD/services/pln_pipeline/app/tests:/app/app/tests" pln-pipeline \
    python -m unittest app.tests.test_retraining_dataset
docker compose run --rm --no-deps -v "$PWD/services/pln_pipeline/app/tests:/app/app/tests" pln-pipeline \
    python -m unittest app.tests.test_rag_remote
docker compose exec pln-pipeline python app/tests/pln_knn_smoke_test.py --route /api/fasttext/knn --limit 10
```

### WhatsApp Bot (node:test)

Os predicados que decidem o rumo da conversa (sim/não, filtro de grupo e canal,
CPF, texto da pergunta de verificação) ficam em `lib/predicates.js` e têm teste
sem Chromium e sem rede:

```bash
docker compose run --rm --no-deps --entrypoint npm whatsapp-bot test
```

Ou direto, com Node instalado:

```bash
cd services/whatsapp_bot && npm test
```

Resultado esperado: `pass 35`.

## Dados de teste

### Históricos de conversa mocados

Insere conversas completas, com roteiro escrito, cobrindo todos os status que o
painel exibe — além de notas de 1 a 5 e avaliações de resposta, para os
indicadores e o gráfico terem dados.

```bash
docker compose exec service-manager python -m app.seed_mock_conversations
docker compose exec service-manager python -m app.seed_mock_conversations --reset
docker compose exec service-manager python -m app.seed_mock_conversations --repeat 3
docker compose exec service-manager python -m app.seed_mock_conversations --only-reset
```

Cria também dois logins de painel (senha `mock12345`):
`gestora@mock.procon.sp.gov.br` e `analista@mock.procon.sp.gov.br`.

Tudo que ele grava usa o prefixo `PROCON-MOCK-`, telefones `551290012XXX` e
e-mails `@mock.procon.sp.gov.br`, então `--reset` remove só o que é dele.

**Teste ao vivo:** com `--phone` ele cria, para um número real, uma conversa
parada em "Aguardando atendente". No painel, em Conversas, é só clicar em
Atender → Assumir atendimento e responder: a mensagem sai de verdade no WhatsApp
daquele número.

```bash
docker compose exec service-manager python -m app.seed_mock_conversations --reset --phone 5512999999999
```

O número vai completo: **DDI 55 + DDD + número**, 12 ou 13 dígitos. O script recusa
qualquer outro formato antes de gravar, porque um número malformado não falha na
hora — o atendimento aparece no painel normalmente e só o envio quebra, com 502.

Só funciona com o bot pareado: sem sessão do WhatsApp não há como a mensagem sair,
e o painel devolve o mesmo 502. Confira com `docker compose logs whatsapp-bot` —
se aparecer `Bot pronto!`, está pareado.

### Volume sintético para as métricas

Para encher o dashboard com 14 dias de dados sorteados (outro conjunto, prefixo
`PROCON-DEMO-`):

```bash
docker compose exec service-manager python -m app.seed_demo_data
docker compose exec service-manager python -m app.seed_demo_data --reset
```

## Autenticação da API

Três níveis, todos no `service-manager`:

| Nível | Credencial | Rotas |
|---|---|---|
| aberto | nenhuma | `GET /api/v1/health`, `POST /auth/login` |
| admin ou bot | JWT do painel **ou** header `X-Bot-Secret` | rotas do atendimento: consumidores por telefone/LID, conversas, troca de status, gravação de mensagem e de nota |
| admin | JWT do painel | leituras em bloco (`GET /api/v1/users`, `/messages`, `/feedback`), ações de atendente (takeover, agent-message, release, avaliação) e `/api/v1/admin-users` (este, só gestor) |

O `whatsapp-bot` é cliente desta API e não tem login: ele autentica com o mesmo
`BOT_SECRET` que o `service-manager` já usa para falar com ele em `POST /send`.
É uma credencial só, nos dois sentidos.

O segredo do bot **não** abre as leituras em bloco. Ele busca consumidor por
telefone ou LID, um de cada vez; quem lista o cadastro inteiro é o painel. Assim,
se o segredo vazar, ele não serve para varrer dado pessoal.

Códigos de resposta:

- `401` — credencial ausente ou inválida (o painel trata e volta para o login)
- `403` — autenticado, mas sem permissão (analista tentando `/api/v1/admin-users`)

## Endpoints do Gateway (service-manager)

### `POST /api/v1/process-message?keyword={keyword}`

Recebe uma mensagem do WhatsApp, aplica o filtro de keyword e orquestra o processamento pelo PLN.

**Request body:**
```json
{
    "from_number": "5511999999999@c.us",
    "text": "Procon como faço para reclamar de um produto com defeito?"
}
```

**Respostas:**
- `200 OK` — mensagem passou no filtro e foi processada:
```json
{
    "class_response": "Texto da resposta gerado pelo PLN..."
}
```
- `204 No Content` — mensagem não continha a keyword, ignorada sem resposta ao usuário.

## Endpoints do PLN Pipeline

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/fasttext` | Vetorização FastText + similaridade + resposta |
| `POST` | `/api/fasttext/knn` | FastText com KNN |
| `POST` | `/api/w2vec` | Vetorização Word2Vec + similaridade + resposta |
| `POST` | `/api/w2vec/knn` | Word2Vec com KNN |
| `POST` | `/api/rag_remote` | Resposta via LLM com RAG remoto |
| `POST` | `/api/distilbert` | Resposta via IA Local com FineTuning |
| `POST` | `/api/retraining-dataset` | Recebe `question` e `answer` do Frontend e popula o dataset de re-treinamento |

### `POST /api/retraining-dataset`

Recebe pares revisados pelo Frontend para alimentar o dataset usado em re-treinamento.

**Request body:**
```json
{
    "question": "Como cancelar uma cobrança indevida no cartão?",
    "answer": "Procure o fornecedor e registre reclamação com os comprovantes."
}
```

**Resposta:**
```json
{
    "message": "Registro salvo no dataset de re-treinamento.",
    "total_records": 1,
    "record": {
        "question": "Como cancelar uma cobrança indevida no cartão?",
        "answer": "Procure o fornecedor e registre reclamação com os comprovantes."
    }
}
```

## Estrutura do projeto

```text
backend/
├── docker-compose.yml
├── README.md
└── services/
    ├── pln_pipeline/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── app/
    │       ├── main.py
    │       ├── models/
    │       │   ├── schemas.py
    │       │   ├── fastText_pipe.py
    │       │   ├── w2vec_pipe.py
    │       │   └── rag_remote.py
    │       └── utils/
    │           ├── duvidas_frequentes.txt
    │           ├── duvidas_frequentes_clean.txt
    │           ├── item_responses.json
    │           ├── class_responses.json
    │           └── faq_fonte.pdf
    ├── service_manager/           ← Gateway
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── app/main.py
    └── whatsapp_bot/
        ├── Dockerfile
        ├── package.json
        ├── index.js
        └── app/
            └── models/schemas.py
```
