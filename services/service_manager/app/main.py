from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select
from .database import create_db_and_tables, engine, get_session
from .deps import get_current_user
from .models.admin import AdminUser
from .models.entities import User, Conversation, Message, Feedback, MessageEvaluation
from .routers import auth, users
from .seed import seed_default_admin
from .utils.security import decrypt_data, encrypt_data


class UserUpdate(BaseModel):
    name: Optional[str] = None
    cpf: Optional[str] = None


class MessageEvaluationInput(BaseModel):
    rating: Literal["positive", "negative"]

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
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)


@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "service-manager"}


# User Endpoints
@app.post("/api/v1/users", response_model=User)
def create_user(user: User, session: Session = Depends(get_session)):
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@app.get("/api/v1/users", response_model=list[User])
def list_users(session: Session = Depends(get_session)):
    users = session.exec(select(User)).all()
    for user in users:
        user.cpf = decrypt_data(user.cpf)
    return users


@app.get("/api/v1/users/{user_id}", response_model=User)
def get_user(user_id: int, session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Decrypt CPF for the response
    user.cpf = decrypt_data(user.cpf)
    return user


@app.get("/api/v1/users/phone/{phone}", response_model=User)
def get_user_by_phone(phone: str, session: Session = Depends(get_session)):
    statement = select(User).where(User.phone == phone)
    user = session.exec(statement).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.put("/api/v1/users/{user_id}", response_model=User)
def update_user(user_id: int, payload: UserUpdate, session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.name is not None:
        user.name = payload.name
    if payload.cpf is not None:
        user.cpf = encrypt_data(payload.cpf)

    session.add(user)
    session.commit()
    session.refresh(user)

    user.cpf = decrypt_data(user.cpf)
    return user


# Conversation Endpoints
@app.post("/api/v1/conversations", response_model=Conversation)
def create_conversation(conversation: Conversation, session: Session = Depends(get_session)):
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@app.get("/api/v1/conversations", response_model=list[Conversation])
def list_conversations(session: Session = Depends(get_session)):
    return session.exec(select(Conversation)).all()


@app.get("/api/v1/conversations/active/{user_id}", response_model=Conversation)
def get_active_conversation(user_id: int, session: Session = Depends(get_session)):
    statement = select(Conversation).where(Conversation.user_id == user_id, Conversation.status == "open")
    conversation = session.exec(statement).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="No active conversation found")
    return conversation


@app.get("/api/v1/messages", response_model=list[Message])
def list_messages(session: Session = Depends(get_session)):
    return session.exec(select(Message)).all()


@app.post("/api/v1/messages", response_model=Message)
def create_message(message: Message, session: Session = Depends(get_session)):
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


# Feedback Endpoints (Task 08.1)
@app.get("/api/v1/feedback", response_model=list[Feedback])
def list_feedback(session: Session = Depends(get_session)):
    return session.exec(select(Feedback)).all()

@app.post("/api/v1/feedback", response_model=Feedback)
def create_feedback(feedback: Feedback, session: Session = Depends(get_session)):
    # 1. Verificar se a conversa existe
    conversation = session.get(Conversation, feedback.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # 2. Tentar encontrar feedback existente para esta conversa
    statement = select(Feedback).where(Feedback.conversation_id == feedback.conversation_id)
    existing_feedback = session.exec(statement).first()
    
    if existing_feedback:
        # Atualizar registro existente
        existing_feedback.rating = feedback.rating
        existing_feedback.comment = feedback.comment
        existing_feedback.is_best_answer = feedback.is_best_answer
        session.add(existing_feedback)
        session.commit()
        session.refresh(existing_feedback)
        return existing_feedback
    else:
        # Criar novo registro
        session.add(feedback)
        session.commit()
        session.refresh(feedback)
        return feedback


# Message Evaluation Endpoints (avaliação de respostas da LLM por gestores/analistas)
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
