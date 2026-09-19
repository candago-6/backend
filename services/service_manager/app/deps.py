import os
import secrets as secrets_lib

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlmodel import Session

from app import security
from app.database import get_session
from app.models.admin import AdminUser, Role

# `auto_error=False` para o header ausente não virar 403 antes de darmos chance ao
# X-Bot-Secret: quem chama pode ser o bot, que não tem login.
optional_bearer = HTTPBearer(auto_error=False)

# O whatsapp-bot é cliente desta API e não tem usuário para autenticar. Ele usa o
# mesmo segredo que o service-manager já manda no sentido inverso (POST /send do
# bot), então a credencial é uma só, configurada em BOT_SECRET.
BOT_SECRET = os.getenv("BOT_SECRET", "dev-bot-secret-change-me")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    session: Session = Depends(get_session),
) -> AdminUser:
    """Exige um token de admin válido.

    Usa `optional_bearer` para poder devolver 401 quando o header falta. Com o
    `HTTPBearer` padrão o FastAPI responde 403 nesse caso, o que confunde
    "não autenticado" com "autenticado e sem permissão" — e, do lado do painel,
    403 não dispara o interceptor do axios, então uma sessão expirada deixava a
    tela travada em vez de voltar para o login. O 403 fica só para `require_gestor`,
    onde ele é o código certo.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = security.decode_access_token(credentials.credentials)
    except (JWTError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    user = session.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado")
    return user


def require_gestor(current_user: AdminUser = Depends(get_current_user)) -> AdminUser:
    if current_user.role != Role.gestor:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a gestores")
    return current_user


def _admin_do_token(
    credentials: HTTPAuthorizationCredentials | None,
    session: Session,
) -> AdminUser | None:
    """Resolve o admin do Bearer, ou None se o token faltar/não valer.

    Diferente de `get_current_user`, não levanta: aqui um token ruim ainda pode
    perder para o X-Bot-Secret. O `KeyError` cobre um JWT bem assinado mas sem a
    claim `sub`, que em `decode_access_token` escaparia como erro 500.
    """
    if credentials is None:
        return None
    try:
        user_id = security.decode_access_token(credentials.credentials)
    except (JWTError, KeyError):
        return None
    return session.get(AdminUser, user_id)


def _e_o_bot(x_bot_secret: str | None) -> bool:
    if not x_bot_secret or not BOT_SECRET:
        return False
    return secrets_lib.compare_digest(x_bot_secret, BOT_SECRET)


def require_admin_or_bot(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    session: Session = Depends(get_session),
) -> AdminUser | None:
    """Aceita o token de um admin OU o segredo do bot.

    É a guarda das rotas que o whatsapp-bot consome durante o atendimento. Devolve
    o admin quando a chamada veio do painel e None quando veio do bot — nenhuma
    dessas rotas precisa saber quem é o chamador, só que ele é conhecido.
    """
    if _e_o_bot(x_bot_secret):
        return None

    usuario = _admin_do_token(credentials, session)
    if usuario is not None:
        return usuario

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credencial ausente ou inválida",
        headers={"WWW-Authenticate": "Bearer"},
    )
