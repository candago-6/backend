import os

from sqlmodel import Session, select

from app import security
from app.models.admin import AdminUser, Role


def seed_default_admin(session: Session) -> None:
    if session.exec(select(AdminUser)).first():
        return

    email = os.getenv("ADMIN_EMAIL", "admin@procon.sp.gov.br")
    password = os.getenv("ADMIN_PASSWORD", "admin123")

    admin = AdminUser(
        name="Administrador",
        email=email,
        hashed_password=security.hash_password(password),
        role=Role.gestor_gerencia,
    )
    session.add(admin)
    session.commit()
