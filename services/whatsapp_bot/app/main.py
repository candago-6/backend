from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

app = FastAPI(title="WhatsApp Bot", version="0.1.0")


class WebhookPayload(BaseModel):
    message: str
    sender_phone: str


def process_message(payload: WebhookPayload):
    # Inicia o fluxo de processamento
    print(f"Iniciando processamento da mensagem de {payload.sender_phone}: {payload.message}")


@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "whatsapp-bot"}


@app.post("/api/v1/webhook")
def receive_whatsapp_event(payload: WebhookPayload, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_message, payload)
    return {"status": "received"}
