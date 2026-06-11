import os

from sqlalchemy import text
from sqlmodel import Session, select

from app import security
from app.models.admin import AdminUser, Role


def seed_default_admin(session: Session) -> None:
    # Migra valores antigos do enum Role (gestor_gerencia/gestor_analista -> gestor).
    # ADD VALUE precisa ser commitado antes de poder ser usado em outra instrução.
    session.exec(text("ALTER TYPE role ADD VALUE IF NOT EXISTS 'gestor'"))
    session.commit()
    session.exec(
        text("UPDATE admin_users SET role = 'gestor' WHERE role::text IN ('gestor_gerencia', 'gestor_analista')")
    )
    session.commit()

    if session.exec(select(AdminUser)).first():
        return

    email = os.getenv("ADMIN_EMAIL", "admin@procon.sp.gov.br")
    password = os.getenv("ADMIN_PASSWORD", "admin123")

    admin = AdminUser(
        name="Administrador",
        email=email,
        hashed_password=security.hash_password(password),
        role=Role.gestor,
    )
    session.add(admin)
    session.commit()
