from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: EmailStr
    role: Role


class TokenResponse(BaseModel):
    token: str
    user: UserOut
