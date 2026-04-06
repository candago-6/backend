from pydantic import BaseModel, Field
from typing import List, Optional


class WhatsAppMessageText(BaseModel):
    body: str


class WhatsAppMessage(BaseModel):
    from_number: str = Field(..., alias="from")
    id: str
    timestamp: str
    text: WhatsAppMessageText
    type: str


class WhatsAppContact(BaseModel):
    wa_id: str
    profile: dict


class WhatsAppMetadata(BaseModel):
    display_phone_number: str
    phone_number_id: str


class WhatsAppValue(BaseModel):
    messaging_product: str
    metadata: WhatsAppMetadata
    contacts: Optional[List[WhatsAppContact]] = None
    messages: Optional[List[WhatsAppMessage]] = None


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str


class WhatsAppEntry(BaseModel):
    id: str
    changes: List[WhatsAppChange]


class WhatsAppWebhookPayload(BaseModel):
    object: str
    entry: List[WhatsAppEntry]


# Modelo para a resposta do envio de mensagem
class MessageResponse(BaseModel):
    status: str
    message_id: Optional[str] = None
    error: Optional[str] = None
