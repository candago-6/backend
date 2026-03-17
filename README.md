# Backend

Setup inicial dos servicos do assistente de WhatsApp (fase base de containers).

## Servicos base criados

Atualmente o backend possui 3 servicos com estrutura minima para subir em container:

1. `pln-pipeline` (`services/pln_pipeline`)
2. `service-manager` (`services/service_manager`)
3. `whatsapp-bot` (`services/whatsapp_bot`)

Cada servico tem:

- `Dockerfile` com Python 3.12 slim
- `requirements.txt` com FastAPI e Uvicorn
- `app/main.py` com endpoint de health check

## Docker Compose

Arquivo: `docker-compose.yml`

Esse compose define:

- build local para os 3 servicos
- publicacao de portas:
	- `8001` para `pln-pipeline`
	- `8002` para `service-manager`
	- `8003` para `whatsapp-bot`
- rede compartilhada `assistente-whatsapp-net`

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

## Health checks disponiveis

- `http://localhost:8001/api/v1/health` -> PLN Pipeline
- `http://localhost:8002/api/v1/health` -> Service Manager
- `http://localhost:8003/api/v1/health` -> WhatsApp Bot

Exemplo de resposta:

```json
{
	"status": "ok",
	"service": "pln-pipeline"
}
```

## Estrutura atual

```text
backend/
├── docker-compose.yml
├── README.md
└── services/
		├── pln_pipeline/
		│   ├── Dockerfile
		│   ├── requirements.txt
		│   └── app/main.py
		├── service_manager/
		│   ├── Dockerfile
		│   ├── requirements.txt
		│   └── app/main.py
		└── whatsapp_bot/
				├── Dockerfile
				├── requirements.txt
				└── app/main.py
```

## Proximo passo

Implementar os endpoints reais de negocio em cada servico e ajustar variaveis de ambiente no `docker-compose.yml` para comunicacao entre eles.
