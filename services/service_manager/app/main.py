import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any

app = FastAPI(title="Gateway Service (Service Manager)", version="0.1.0")

class MessageRequest(BaseModel):
    from_number: str
    text: str

@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "service-manager"}

@app.post("/api/v1/process-message")
async def process_message(payload: MessageRequest) -> dict[str, Any]:
    # Faz uma requisição assíncrona ao PLN Pipeline
    pln_url = "http://pln-pipeline:8001/api/fasttext"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                pln_url,
                json={"raw_text": payload.text},
                timeout=10.0
            )
            response.raise_for_status()
            pln_data = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Erro ao contatar o serviço de PLN: {exc}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=f"Erro do serviço de PLN: {exc.response.text}")

    # Por enquanto, apenas retorna os dados da predição mantendo a resposta estática no WhatsApp
    return {
        "status": "success",
        "message": "Processamento concluído",
        "pln_prediction": pln_data
    }
