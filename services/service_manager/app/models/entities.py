from datetime import datetime, timezone
from typing import List, Optional
from sqlmodel import Field, Relationship, SQLModel
from pydantic import field_validator
from ..utils.security import encrypt_data


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    cpf: str = Field(index=True)  # Stored encrypted
    phone: str = Field(index=True)
    
    conversations: List["Conversation"] = Relationship(back_populates="user")

    @field_validator("cpf")
    @classmethod
    def encrypt_cpf(cls, v: str) -> str:
        # Simple check to avoid double encryption if it's already a Fernet token
        # (Fernet tokens usually start with gAAAA)
        if v.startswith("gAAAA"):
            return v
        return encrypt_data(v)


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    protocol: str = Field(index=True, unique=True)
    status: str = Field(default="open")  # open, closed, archived
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    user: User = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id")
    role: str  # user, bot, system
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    conversation: Conversation = Relationship(back_populates="messages")
    feedback: Optional["Feedback"] = Relationship(back_populates="message")


class Feedback(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: int = Field(foreign_key="message.id")
    rating: int = Field(ge=1, le=5)  # 1 to 5
    comment: Optional[str] = None
    is_best_answer: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    message: Message = Relationship(back_populates="feedback")
