import enum
import uuid

from sqlmodel import Field, SQLModel


class Role(str, enum.Enum):
    gestor = "gestor"
    analista = "analista"


class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_users"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    hashed_password: str
    role: Role
