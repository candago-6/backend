# Backend

Setup inicial dos serviços do assistente de WhatsApp (fase base de containers).

## Serviços base criados

Atualmente o backend possui 3 serviços com estrutura mínima para subir em container:

1. `pln-pipeline` (`services/pln_pipeline`)
2. `service-manager` (`services/service_manager`)
3. `whatsapp-bot` (`services/whatsapp_bot`)

Cada serviço tem:

- `Dockerfile` com Python 3.12 slim
- `requirements.txt` com FastAPI e Uvicorn
- `app/main.py` com endpoint de health check

## Docker Compose

Arquivo: `docker-compose.yml`

Esse compose define:

- build local para os 3 serviços
- publicação de portas:
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

---

## Pipeline de PLN

A pipeline de PLN é responsável por transformar uma mensagem não estruturada em informação processável. Ela pode ser executada como parte do Web Service de Orquestração ou como um serviço separado.

### Saída esperada (exemplo)

Em alto nível, a saída deve incluir **intenção/classe** e um nível de **confiança**, para orientar o roteamento (resposta automática vs. pedir esclarecimento vs. humano).

```json
{
	"mensagemOriginal": "Quero saber o andamento da minha reclamação 4589",
	"intencao": "consultar_reclamacao",
	"classePrevista": "consulta_andamento_processo",
	"confianca": 0.94,
	"acaoSugerida": "consultar_api_protocolos"
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

## Próximo passo

Implementar os endpoints reais de negócio em cada serviço e ajustar variáveis de ambiente no `docker-compose.yml` para comunicação entre eles.
