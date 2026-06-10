import os

from sqlalchemy.orm import Session

from app import models, security


def seed_default_admin(db: Session) -> None:
    if db.query(models.User).count() > 0:
        return

    email = os.getenv("ADMIN_EMAIL", "admin@procon.sp.gov.br")
    password = os.getenv("ADMIN_PASSWORD", "admin123")

    admin = models.User(
        name="Administrador",
        email=email,
        hashed_password=security.hash_password(password),
        role=models.Role.gestor_gerencia,
    )
    db.add(admin)
    db.commit()
