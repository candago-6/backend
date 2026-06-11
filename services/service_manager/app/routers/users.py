from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app import schemas, security
from app.database import get_session
from app.deps import get_current_user
from app.models.admin import AdminUser

router = APIRouter(prefix="/api/v1/admin-users", tags=["admin-users"])


@router.get("", response_model=list[schemas.UserOut])
def list_users(
    session: Session = Depends(get_session),
    _: AdminUser = Depends(get_current_user),
) -> list[AdminUser]:
    return session.exec(select(AdminUser)).all()


@router.post("", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: schemas.UserCreate,
    session: Session = Depends(get_session),
    _: AdminUser = Depends(get_current_user),
) -> AdminUser:
    if session.exec(select(AdminUser).where(AdminUser.email == payload.email)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado")

    user = AdminUser(
        name=payload.name,
        email=payload.email,
        hashed_password=security.hash_password(payload.password),
        role=payload.role,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.put("/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: str,
    payload: schemas.UserUpdate,
    session: Session = Depends(get_session),
    _: AdminUser = Depends(get_current_user),
) -> AdminUser:
    user = session.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    existing = session.exec(
        select(AdminUser).where(AdminUser.email == payload.email, AdminUser.id != user_id)
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado")

    user.name = payload.name
    user.email = payload.email
    user.role = payload.role
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    session: Session = Depends(get_session),
    _: AdminUser = Depends(get_current_user),
) -> None:
    user = session.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    session.delete(user)
    session.commit()
