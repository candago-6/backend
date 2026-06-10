from datetime import datetime, timezone
from typing import List, Optional
from sqlmodel import Field, Relationship, SQLModel
from pydantic import field_validator
from ..utils.security import encrypt_data


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True, description="Unique identifier for the user")
    name: str = Field(description="Full name of the user")
    cpf: str = Field(index=True, description="User CPF (stored encrypted)")  # Stored encrypted
    phone: str = Field(index=True, description="WhatsApp phone number of the user")

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
    id: Optional[int] = Field(default=None, primary_key=True, description="Unique identifier for the conversation")
    user_id: int = Field(foreign_key="user.id", description="ID of the user associated with this conversation")
    protocol: str = Field(index=True, unique=True, description="Unique protocol number for the conversation")
    status: str = Field(default="open", description="Current status of the conversation: open, closed, archived")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of conversation creation")

    user: User = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(back_populates="conversation")
    feedback: Optional["Feedback"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True, description="Unique identifier for the message")
    conversation_id: int = Field(foreign_key="conversation.id", description="ID of the conversation this message belongs to")
    role: str = Field(description="Role of the message sender: user, bot, system")  # user, bot, system
    content: str = Field(description="Content of the message")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of the message")

    conversation: Conversation = Relationship(back_populates="messages")


class Feedback(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True, description="Unique identifier for the feedback")
    conversation_id: int = Field(foreign_key="conversation.id", description="ID of the conversation being evaluated")
    rating: int = Field(ge=1, le=5, description="Rating from 1 to 5")  # 1 to 5
    comment: Optional[str] = Field(default=None, description="Optional user comment")
    is_best_answer: bool = Field(default=False, description="Whether this is marked as the best answer")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of feedback creation")

    conversation: Conversation = Relationship(back_populates="feedback")

