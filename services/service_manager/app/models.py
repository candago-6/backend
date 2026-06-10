import enum
import uuid

from sqlalchemy import Column, Enum as SAEnum, String

from app.database import Base


class Role(str, enum.Enum):
    gestor_gerencia = "gestor_gerencia"
    gestor_analista = "gestor_analista"
    analista = "analista"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(SAEnum(Role), nullable=False)
