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

| Serviço | Diretório | Porta | Tecnologia |
|---|---|---|---|
| `pln-pipeline` | `services/pln_pipeline` | `8001` | Python / FastAPI |
| `service-manager` | `services/service_manager` | `8002` | Python / FastAPI |
| `whatsapp-bot` | `services/whatsapp_bot` | `8003` | Node.js / Express |

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

1. Abra o WhatsApp no celular
2. Vá em **Dispositivos conectados → Conectar dispositivo**
3. Escaneie o QR Code exibido no log do container `whatsapp_bot`

A sessão é salva localmente em `services/whatsapp_bot/.wwebjs_auth` e não precisa ser re-autenticada nas próximas subidas.

## Variáveis de ambiente

As variáveis são configuradas no `docker-compose.yml`, no serviço `whatsapp-bot`:

| Variável | Padrão | Descrição |
|---|---|---|
| `GATEWAY_URL` | `http://service_manager:8002/api/v1/process-message` | URL do Gateway (service-manager) |
| `FILTER_KEYWORD` | `Procon` | Keyword que a mensagem deve conter para ser processada. Mensagens sem essa keyword são ignoradas. |

Para alterar a keyword sem rebuild, edite o `docker-compose.yml`:

```yaml
environment:
  - FILTER_KEYWORD=SuaKeywordAqui
```

## Health checks

- `http://localhost:8001/api/health` → PLN Pipeline
- `http://localhost:8002/api/v1/health` → Service Manager (Gateway)
- `http://localhost:8003/api/v1/health` → WhatsApp Bot

Exemplo de resposta:

```json
{
    "status": "ok",
    "service": "pln-pipeline"
}
```

## Como rodar os testes

### Service Manager

A suíte de CRUD dos usuários administrativos fica em `services/service_manager/tests`.
Ela usa SQLite em memória e sobrescreve as dependências da API, então não precisa subir o Postgres.

Se estiver em qualquer subpasta do repositório, volte para a raiz antes de rodar:

```bash
cd "$(git rev-parse --show-toplevel)"
docker compose build service-manager
docker compose run --rm --no-deps -v "$PWD/services/service_manager/tests:/app/tests" service-manager pytest /app/tests
```

Ou rode de qualquer subpasta usando o caminho da raiz do Git diretamente:

```bash
docker compose build service-manager
docker compose run --rm --no-deps -v "$(git rev-parse --show-toplevel)/services/service_manager/tests:/app/tests" service-manager pytest /app/tests
```

Resultado esperado:

```text
8 passed
```

### PLN Pipeline

Os scripts de teste ficam em `services/pln_pipeline/app/tests`.
Para os testes que chamam endpoints HTTP, suba o serviço antes:

```bash
docker compose up --build pln-pipeline
```

Em outro terminal, rode os scripts a partir da pasta `backend`:

```bash
docker compose exec pln-pipeline python -m unittest app.tests.test_retraining_dataset
```

```bash
docker compose exec pln-pipeline python -m unittest app.tests.test_rag_remote
```

```bash
docker compose exec pln-pipeline python app/tests/pln_knn_smoke_test.py --route /api/fasttext/knn --limit 10
```

```bash
docker compose exec pln-pipeline python app/tests/pln_knn_smoke_test.py --route /api/w2vec/knn --limit 10
```

```bash
docker compose exec pln-pipeline python app/tests/pln_distilbert_knn_comparison_test.py --knn-route /api/fasttext/knn --questions-per-intent 1 --limit 20
```

```bash
docker compose exec pln-pipeline python app/tests/pln_distilbert_knn_comparison_test.py --knn-route /api/w2vec/knn --questions-per-intent 1 --limit 20
```

Também é possível apontar os scripts para outra URL usando `--base-url`, por exemplo:

```bash
python services/pln_pipeline/app/tests/pln_knn_smoke_test.py --base-url http://localhost:8001 --route /api/fasttext/knn --limit 10
```

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
| `POST` | `/api/rag` | Resposta via LLM com RAG local (PDF) |
| `POST` | `/api/rag_remote` | Resposta via LLM com RAG remoto |
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
    │       │   ├── rag_pipeline.py
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
