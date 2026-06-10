from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, SessionLocal, engine
from app.routers import auth
from app.seed import seed_default_admin

Base.metadata.create_all(bind=engine)
import httpx
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel
from typing import Any


app = FastAPI(title="Gateway Service (Service Manager)", version="0.1.0")


class GatewayRequest(BaseModel):
    from_number: str
    text: str


class GatewayResponse(BaseModel):
    class_response: str

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)


@app.on_event("startup")
def on_startup() -> None:
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()


@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "service-manager"}


@app.post("/api/v1/process-message", response_model=GatewayResponse)
async def process_message(
    payload: GatewayRequest,
    keyword: str = Query(..., description="Keyword que a mensagem deve conter para ser processada"),
) -> Any:
    # Filtro por keyword: ignora mensagens que não contenham a keyword configurada
    if keyword.lower() not in payload.text.lower():
        return Response(status_code=204)

    # Encaminha ao PLN Pipeline
    pln_url = "http://pln-pipeline:8001/api/fasttext"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                pln_url,
                json={"raw_text": payload.text},
                timeout=10.0,
            )
            response.raise_for_status()
            pln_data = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Erro ao contatar o serviço de PLN: {exc}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Erro do serviço de PLN: {exc.response.text}",
        )

    return GatewayResponse(class_response=pln_data["class_response"])
