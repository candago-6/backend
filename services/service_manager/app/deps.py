from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlmodel import Session

from app import security
from app.database import get_session
from app.models.admin import AdminUser, Role

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> AdminUser:
    try:
        user_id = security.decode_access_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    user = session.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado")
    return user


def require_gestor(current_user: AdminUser = Depends(get_current_user)) -> AdminUser:
    if current_user.role != Role.gestor:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a gestores")
    return current_user
