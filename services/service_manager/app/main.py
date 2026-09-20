import os
import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select
from .database import create_db_and_tables, engine, get_session
from .deps import SUPER_ADMIN_EMAIL, get_current_user, require_admin_or_bot, require_super_admin
from .models.admin import AdminUser
from .models.entities import User, Conversation, Message, Feedback, MessageEvaluation
from .routers import auth, users
from .seed import seed_default_admin
from .utils.security import decrypt_data, encrypt_data


class UserUpdate(BaseModel):
    name: Optional[str] = None
    cpf: Optional[str] = None
    phone: Optional[str] = None
    whatsapp_id: Optional[str] = None


class MessageEvaluationInput(BaseModel):
    rating: Literal["positive", "negative"]


class AgentMessageInput(BaseModel):
    content: str


BOT_URL = os.getenv("BOT_URL", "http://whatsapp-bot:8003")
BOT_SECRET = os.getenv("BOT_SECRET", "dev-bot-secret-change-me")
PLN_URL = os.getenv("PLN_URL", "http://pln-pipeline:8001")

# Origens do painel, separadas por vírgula (ex.: "http://10.0.0.5:3000,https://painel.sp.gov.br").
# São várias ao mesmo tempo na prática: localhost para desenvolver, o IP da VM
# para o cliente e, depois, o domínio. O padrão cobre só o desenvolvimento, então
# a variável ausente não abre a API sozinha.
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000")


def parse_cors_origins(bruto: str) -> list[str]:
    """Quebra a lista do .env e tira a barra final de cada origem.

    O navegador casa a origem por string exata e envia `http://host:3000` sem
    barra — uma sobrando no .env derruba o painel inteiro em silêncio. Pelo mesmo
    motivo trocar http por https aqui não é detalhe: é outra origem.

    `*` não serve de atalho: junto de allow_credentials o próprio navegador
    recusa a resposta, então liberar tudo quebra tudo.
    """
    return [origem.strip().rstrip("/") for origem in bruto.split(",") if origem.strip()]


def _chat_id(user: User) -> str:
    """Mirror the bot's chatId resolution: prefer WhatsApp ID, fall back to phone JID."""
    return user.whatsapp_id or f"{user.phone}@c.us"


def send_whatsapp_message(chat_id: str, text: str) -> None:
    """Push an outbound WhatsApp message through the bot. Raises on failure."""
    resp = httpx.post(
        f"{BOT_URL}/send",
        json={"chatId": chat_id, "text": text},
        headers={"X-Bot-Secret": BOT_SECRET},
        timeout=15.0,
    )
    resp.raise_for_status()


def _make_intent_slug(text: str, max_length: int = 60) -> str:
    """Generate a snake_case slug from free-form text, stripping accents."""
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    # Keep only word characters and spaces
    ascii_text = re.sub(r"[^\w\s]", "", ascii_text)
    slug = "_".join(ascii_text.split())
    return slug[:max_length].rstrip("_")


def _enrich_faq_dataset(conversation_id: int, session: Session) -> None:
    """Extract user questions and bot answers from the conversation and send
    them to the PLN pipeline to enrich the FAQ dataset."""
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.timestamp)
    ).all()

    user_questions = [m.content for m in messages if m.role == "user"]
    bot_answers = [m.content for m in messages if m.role == "bot"]

    if not user_questions or not bot_answers:
        return

    # Use the first bot answer as the canonical answer
    answer = bot_answers[0]
    intent = _make_intent_slug(user_questions[0])

    if not intent:
        return

    try:
        resp = httpx.post(
            f"{PLN_URL}/api/faq-dataset/entries",
            json={"intent": intent, "answer": answer, "questions": user_questions},
            timeout=15.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        # Best-effort: do not break the feedback flow if PLN is unreachable.
        print(f"[enrich_faq] failed to push FAQ entry: {exc}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables with retries
    create_db_and_tables()

    with Session(engine) as session:
        seed_default_admin(session)

    yield


app = FastAPI(title="Service Manager", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)


@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "service-manager"}


# Conexão do WhatsApp — só para o administrador do sistema
#
# O bot não é publicado no host (docker-compose.yml), de propósito: quem alcança
# GET /qr direto conecta o próprio aparelho na conta de atendimento. Estas duas
# rotas são a única porta de entrada, e passam pelo login do painel.
@app.get("/api/v1/bot/status")
def bot_status(_: AdminUser = Depends(require_super_admin)) -> dict:
    """Em qual estado está o pareamento: iniciando, aguardando_leitura ou conectado."""
    try:
        resp = httpx.get(
            f"{BOT_URL}/status",
            headers={"X-Bot-Secret": BOT_SECRET},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Bot indisponível: {exc}")

    if resp.status_code == 401:
        raise HTTPException(
            status_code=502,
            detail="O bot recusou a credencial. Confira se BOT_SECRET é o mesmo nos dois serviços.",
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Bot respondeu {resp.status_code}.")

    return resp.json()


@app.get(
    "/api/v1/bot/qr",
    responses={200: {"content": {"image/png": {}}, "description": "QR de pareamento"}},
)
def bot_qr(_: AdminUser = Depends(require_super_admin)) -> Response:
    """Devolve o QR de pareamento como PNG, buscando-o no bot pela rede interna."""
    try:
        resp = httpx.get(
            f"{BOT_URL}/qr",
            headers={"X-Bot-Secret": BOT_SECRET},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Bot indisponível: {exc}")

    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Nenhum QR pendente. O bot já está conectado ou ainda está iniciando.",
        )
    if resp.status_code == 401:
        raise HTTPException(
            status_code=502,
            detail="O bot recusou a credencial. Confira se BOT_SECRET é o mesmo nos dois serviços.",
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Bot respondeu {resp.status_code}.")

    # no-store: o QR expira em segundos e não pode ser servido de cache.
    return Response(
        content=resp.content,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# User Endpoints
@app.post("/api/v1/users", response_model=User, dependencies=[Depends(require_admin_or_bot)])
def create_user(user: User, session: Session = Depends(get_session)):
    # Garante que o CPF esteja criptografado na criação
    if user.cpf:
        user.cpf = encrypt_data(user.cpf)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@app.get("/api/v1/users", response_model=list[User], dependencies=[Depends(get_current_user)])
def list_users(session: Session = Depends(get_session)):
    users_list = session.exec(select(User)).all()
    # Retornamos os dados como estão no banco (criptografados)
    return users_list


@app.get("/api/v1/users/{user_id}", response_model=User, dependencies=[Depends(require_admin_or_bot)])
def get_user(user_id: int, session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/api/v1/users/phone/{phone}", response_model=User, dependencies=[Depends(require_admin_or_bot)])
def get_user_by_phone(phone: str, session: Session = Depends(get_session)):
    statement = select(User).where(User.phone == phone)
    user = session.exec(statement).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/api/v1/users/whatsapp-id/{whatsapp_id}", response_model=User, dependencies=[Depends(require_admin_or_bot)])
def get_user_by_whatsapp_id(whatsapp_id: str, session: Session = Depends(get_session)):
    statement = select(User).where(User.whatsapp_id == whatsapp_id)
    user = session.exec(statement).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.put("/api/v1/users/{user_id}", response_model=User, dependencies=[Depends(require_admin_or_bot)])
def update_user(user_id: int, payload: UserUpdate, session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.name is not None:
        user.name = payload.name
    if payload.cpf is not None:
        # Forçamos a criptografia manual pois a atribuição direta ignora o validator do modelo
        user.cpf = encrypt_data(payload.cpf)
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.whatsapp_id is not None:
        user.whatsapp_id = payload.whatsapp_id

    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# Conversation Endpoints
@app.post("/api/v1/conversations", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def create_conversation(conversation: Conversation, session: Session = Depends(get_session)):
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.get("/api/v1/conversations", response_model=list[Conversation], dependencies=[Depends(require_admin_or_bot)])
def list_conversations(session: Session = Depends(get_session)):
    return session.exec(select(Conversation)).all()


@app.get("/api/v1/conversations/active/{user_id}", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def get_active_conversation(user_id: int, session: Session = Depends(get_session)):
    active_statuses = ["open", "waiting_human", "human_handover", "confirming_closure", "awaiting_feedback"]
    statement = select(Conversation).where(
        Conversation.user_id == user_id, 
        Conversation.status.in_(active_statuses)
    )
    conversation = session.exec(statement).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="No active conversation found")
    return conversation


@app.get("/api/v1/conversations/{conversation_id}", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def get_conversation(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/close", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def close_conversation(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.status = "closed"
    conversation.failed_attempts = 0
    conversation.patience_msg_sent = False
    # Nota: is_onboarded não é resetado para mantermos o histórico, mas um novo protocolo terá is_onboarded=False

    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/mark-onboarded", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def mark_onboarded(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.is_onboarded = True
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/mark-patience-sent", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def mark_patience_sent(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.patience_msg_sent = True
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/update-status", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def update_conversation_status(conversation_id: int, status: str, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    from datetime import datetime, timezone
    conversation.status = status
    conversation.updated_at = datetime.now(timezone.utc)
    
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/increment-failures", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def increment_failures(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    conversation.failed_attempts += 1
    if conversation.failed_attempts >= 3:
        conversation.status = "waiting_human"
    
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/reset-failures", response_model=Conversation, dependencies=[Depends(require_admin_or_bot)])
def reset_failures(conversation_id: int, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    conversation.failed_attempts = 0
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


# Live Handover Endpoints (analyst takes over the conversation from the dashboard)
@app.post("/api/v1/conversations/{conversation_id}/takeover", response_model=Conversation)
def takeover_conversation(
    conversation_id: int,
    session: Session = Depends(get_session),
    current_user: AdminUser = Depends(get_current_user),
):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.status == "closed":
        raise HTTPException(status_code=409, detail="Conversation already closed")
    if conversation.assigned_admin_id and conversation.assigned_admin_id != current_user.id:
        raise HTTPException(status_code=409, detail="Conversation already taken by another agent")

    conversation.status = "human_handover"
    conversation.assigned_admin_id = current_user.id
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.post("/api/v1/conversations/{conversation_id}/agent-message", response_model=Message)
def agent_message(
    conversation_id: int,
    payload: AgentMessageInput,
    session: Session = Depends(get_session),
    current_user: AdminUser = Depends(get_current_user),
):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status != "human_handover":
        raise HTTPException(status_code=409, detail="Conversation is not under human handover")
    # Handover can be reached without an explicit takeover (e.g. the analyst replied
    # directly in WhatsApp, which the bot detects and flips to human_handover).
    # Claim it for the first agent who sends from the dashboard.
    if conversation.assigned_admin_id is None:
        conversation.assigned_admin_id = current_user.id
    elif conversation.assigned_admin_id != current_user.id:
        raise HTTPException(status_code=403, detail="Conversation assigned to another agent")

    user = session.get(User, conversation.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Conversation user not found")

    # Deliver to WhatsApp first; only persist if it actually went out.
    try:
        send_whatsapp_message(_chat_id(user), payload.content)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to deliver message via bot: {exc}")

    message = Message(conversation_id=conversation_id, role="agent", content=payload.content)
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(message)
    session.add(conversation)
    session.commit()
    session.refresh(message)
    return message


@app.post("/api/v1/conversations/{conversation_id}/release", response_model=Conversation)
def release_conversation(
    conversation_id: int,
    to_bot: bool = False,
    session: Session = Depends(get_session),
    current_user: AdminUser = Depends(get_current_user),
):
    """End the live handover. Default: send a closing message + ask for a 1-5 rating
    (status -> awaiting_feedback, bot collects the rating and closes). Pass to_bot=true
    to instead hand control back to the bot (status -> open)."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.assigned_admin_id and conversation.assigned_admin_id != current_user.id:
        raise HTTPException(status_code=403, detail="Conversation assigned to another agent")

    conversation.assigned_admin_id = None
    conversation.failed_attempts = 0
    if to_bot:
        conversation.status = "open"
    else:
        # Close out: notify the client and ask for a satisfaction rating.
        # The bot's awaiting_feedback handler collects the 1-5 reply and closes.
        conversation.status = "awaiting_feedback"
        conversation.patience_msg_sent = False
        user = session.get(User, conversation.user_id)
        if user:
            closing = (
                "Seu atendimento foi encerrado pelo nosso atendente. "
                "Para finalizar, avalie o atendimento com uma nota de *1 a 5*."
            )
            try:
                send_whatsapp_message(_chat_id(user), closing)
            except httpx.HTTPError as exc:
                # Best-effort: still transition state so the dashboard reflects the end.
                print(f"[release] failed to send closing message: {exc}")
            session.add(Message(conversation_id=conversation_id, role="system", content=closing))
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.get("/api/v1/messages", response_model=list[Message], dependencies=[Depends(get_current_user)])
def list_messages(session: Session = Depends(get_session)):
    return session.exec(select(Message)).all()


@app.post("/api/v1/messages", response_model=Message, dependencies=[Depends(require_admin_or_bot)])
def create_message(message: Message, session: Session = Depends(get_session)):
    session.add(message)
    
    # Update conversation last activity
    conversation = session.get(Conversation, message.conversation_id)
    if conversation:
        from datetime import datetime, timezone
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
        
    session.commit()
    session.refresh(message)
    return message


# Feedback Endpoints
@app.get("/api/v1/feedback", response_model=list[Feedback], dependencies=[Depends(get_current_user)])
def list_feedback(session: Session = Depends(get_session)):
    return session.exec(select(Feedback)).all()

@app.post("/api/v1/feedback", response_model=Feedback, dependencies=[Depends(require_admin_or_bot)])
def create_feedback(feedback: Feedback, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, feedback.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    statement = select(Feedback).where(Feedback.conversation_id == feedback.conversation_id)
    existing_feedback = session.exec(statement).first()
    
    if existing_feedback:
        existing_feedback.rating = feedback.rating
        existing_feedback.comment = feedback.comment
        existing_feedback.is_best_answer = feedback.is_best_answer
        session.add(existing_feedback)
        session.commit()
        session.refresh(existing_feedback)
        saved = existing_feedback
    else:
        session.add(feedback)
        session.commit()
        session.refresh(feedback)
        saved = feedback

    # Enrich the FAQ dataset with positive feedback conversations
    if saved.rating > 3:
        _enrich_faq_dataset(saved.conversation_id, session)

    return saved


# Message Evaluation Endpoints
@app.get("/api/v1/message-evaluations", response_model=list[MessageEvaluation])
def list_message_evaluations(
    session: Session = Depends(get_session),
    _: AdminUser = Depends(get_current_user),
):
    return session.exec(select(MessageEvaluation)).all()


@app.put("/api/v1/messages/{message_id}/evaluation", response_model=MessageEvaluation)
def evaluate_message(
    message_id: int,
    payload: MessageEvaluationInput,
    session: Session = Depends(get_session),
    current_user: AdminUser = Depends(get_current_user),
):
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    statement = select(MessageEvaluation).where(MessageEvaluation.message_id == message_id)
    evaluation = session.exec(statement).first()

    if evaluation:
        evaluation.rating = payload.rating
        evaluation.admin_user_id = current_user.id
    else:
        evaluation = MessageEvaluation(
            message_id=message_id,
            admin_user_id=current_user.id,
            rating=payload.rating,
        )

    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)
    return evaluation
