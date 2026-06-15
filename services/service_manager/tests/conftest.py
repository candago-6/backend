import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.database import get_session  # noqa: E402
from app.deps import require_gestor  # noqa: E402
from app.main import app  # noqa: E402
from app.models.admin import AdminUser, Role  # noqa: E402


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as test_session:
        yield test_session

    SQLModel.metadata.drop_all(engine)


@pytest.fixture()
def client(session: Session) -> Generator[TestClient, None, None]:
    gestor = AdminUser(
        id="gestor-test-id",
        name="Gestor Teste",
        email="gestor@example.com",
        hashed_password="unused",
        role=Role.gestor,
    )

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    def override_require_gestor() -> AdminUser:
        return gestor

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_gestor] = override_require_gestor

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.clear()
