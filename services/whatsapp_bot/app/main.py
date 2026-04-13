import httpx
import os
from fastapi import FastAPI, Query, HTTPException, Response
from .models.schemas import WhatsAppWebhookPayload, MessageResponse

app = FastAPI(title="WhatsApp Bot", version="0.1.0")

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "procon_jacarei_2026")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "seu_token_aqui")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "seu_id_aqui")
WHATSAPP_URL = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"

@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "whatsapp-bot"}

@app.get("/api/v1/webhook")
def verify_webhook(
    mode: str = Query(..., alias="hub.mode"),
    token: str = Query(..., alias="hub.verify_token"),
    challenge: str = Query(..., alias="hub.challenge")
):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("WEBHOOK_VERIFIED")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/api/v1/webhook")
async def receive_message(payload: WhatsAppWebhookPayload):
    for entry in payload.entry:
        for change in entry.changes:
            if change.value.messages:
                for message in change.value.messages:
                    print(f"Mensagem recebida de {message.from_number}: {message.text.body}")
    return {"status": "success", "message": "Event received"}

async def send_whatsapp_message(to: str, text: str):
    """
    Função interna para enviar mensagens via WhatsApp Cloud API (Tarefa 01.6).
    """
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(WHATSAPP_URL, headers=headers, json=payload)
        return response.json()

@app.post("/api/v1/send", response_model=MessageResponse)
async def send_message_endpoint(to: str, text: str):
    """
    Endpoint manual para testar o envio de mensagens (Tarefa 01.8).
    """
    try:
        result = await send_whatsapp_message(to, text)
        if "error" in result:
            return MessageResponse(status="error", error=str(result["error"]))
        return MessageResponse(status="success", message_id=result.get("messages", [{}])[0].get("id"))
    except Exception as e:
        return MessageResponse(status="error", error=str(e))
