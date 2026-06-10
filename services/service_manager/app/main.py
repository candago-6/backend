from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from sqlmodel import Session, select
from .database import create_db_and_tables, get_session
from .models.entities import User, Conversation, Message, Feedback
from .utils.security import decrypt_data

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables with retries
    create_db_and_tables()
    yield


app = FastAPI(title="Service Manager", version="0.1.0", lifespan=lifespan)


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
@app.post("/api/v1/feedback", response_model=Feedback)
def create_feedback(feedback: Feedback, session: Session = Depends(get_session)):
    # Verify if message exists
    message = session.get(Message, feedback.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback
