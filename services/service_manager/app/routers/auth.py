from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app import schemas, security
from app.database import get_session
from app.models.admin import AdminUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, session: Session = Depends(get_session)) -> schemas.TokenResponse:
    user = session.exec(select(AdminUser).where(AdminUser.email == payload.email)).first()
    if not user or not security.verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha inválidos",
        )

    token = security.create_access_token(subject=user.id)
    return schemas.TokenResponse(token=token, user=schemas.UserOut.model_validate(user))
