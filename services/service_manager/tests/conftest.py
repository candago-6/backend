"""Fixtures compartilhadas da suíte do service-manager.

A suíte roda inteira em SQLite na memória e sobrescreve `get_session`, então não
precisa do Postgres nem de nenhum outro container no ar. O `lifespan` do FastAPI
(que cria as tabelas no Postgres e faz o seed do admin) não é executado porque o
`TestClient` é instanciado fora de um `with` — é isso que mantém a suíte offline.
"""

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

# Precisa vir antes de qualquer import de `app.*`: app/utils/security.py levanta
# ValueError no import se ENCRYPTION_KEY não estiver definida.
os.environ.setdefault("ENCRYPTION_KEY", "v6u6W5q7U5p4S3Y2X1W0V9U8T7S6R5Q4P3O2N1M0L8k=")
os.environ.setdefault("JWT_SECRET", "test-secret")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel, Session, create_engine  # noqa: E402

from app import deps  # noqa: E402
from app import main as main_module  # noqa: E402
from app import security  # noqa: E402
from app.database import get_session  # noqa: E402
from app.deps import require_gestor  # noqa: E402
from app.main import app  # noqa: E402
from app.models.admin import AdminUser, Role  # noqa: E402
from app.models.entities import Conversation, Message, User  # noqa: E402
from app.utils.security import encrypt_data  # noqa: E402

SENHA_PADRAO = "SenhaForte123"


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
    """Cliente com `require_gestor` sobrescrito — atalho para os testes de CRUD.

    Use `api` quando o teste precisar exercitar o login/JWT de verdade.
    """
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


@pytest.fixture()
def api(session: Session) -> Generator[TestClient, None, None]:
    """Cliente sem credencial nenhuma: é com ele que se testa a recusa."""

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def api_bot(session: Session) -> Generator[TestClient, None, None]:
    """Cliente autenticado como o whatsapp-bot (header X-Bot-Secret).

    É a visão que o bot tem da API durante o atendimento. Use esta fixture nos
    testes de fluxo do bot; use `api` quando o ponto for a ausência de credencial.
    """

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(app, headers={"X-Bot-Secret": deps.BOT_SECRET})
    yield test_client

    app.dependency_overrides.clear()


def _criar_admin(session: Session, nome: str, email: str, role: Role) -> AdminUser:
    admin = AdminUser(
        name=nome,
        email=email,
        hashed_password=security.hash_password(SENHA_PADRAO),
        role=role,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


@pytest.fixture()
def gestor(session: Session) -> AdminUser:
    return _criar_admin(session, "Gestora Chefe", "gestora@procon.sp.gov.br", Role.gestor)


@pytest.fixture()
def analista(session: Session) -> AdminUser:
    return _criar_admin(session, "Analista Um", "analista1@procon.sp.gov.br", Role.analista)


@pytest.fixture()
def outro_analista(session: Session) -> AdminUser:
    return _criar_admin(session, "Analista Dois", "analista2@procon.sp.gov.br", Role.analista)


def auth(admin: AdminUser) -> dict[str, str]:
    """Header Authorization para o admin informado."""
    return {"Authorization": f"Bearer {security.create_access_token(admin.id)}"}


@pytest.fixture()
def h_gestor(gestor: AdminUser) -> dict[str, str]:
    return auth(gestor)


@pytest.fixture()
def h_analista(analista: AdminUser) -> dict[str, str]:
    return auth(analista)


@pytest.fixture()
def h_outro_analista(outro_analista: AdminUser) -> dict[str, str]:
    return auth(outro_analista)


class BotFalso:
    """Substitui `send_whatsapp_message` para não bater no whatsapp-bot de verdade."""

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []
        self.falhar = False

    def __call__(self, chat_id: str, text: str) -> None:
        if self.falhar:
            raise httpx.ConnectError("bot indisponível")
        self.enviados.append((chat_id, text))

    @property
    def ultimo_texto(self) -> str:
        return self.enviados[-1][1]


@pytest.fixture()
def bot(monkeypatch: pytest.MonkeyPatch) -> BotFalso:
    falso = BotFalso()
    monkeypatch.setattr(main_module, "send_whatsapp_message", falso)
    return falso


@pytest.fixture()
def cliente_zap(session: Session) -> User:
    """Consumidor que fala com o bot pelo WhatsApp (tabela `user`).

    O CPF é cifrado à mão de propósito: o `field_validator` de `User.cpf` não roda
    em modelo `table=True`, então quem instancia o modelo direto precisa cifrar —
    é exatamente o que os endpoints fazem. Ver `test_cpf_encryption.py`.
    """
    usuario = User(
        name="Maria Consumidora",
        cpf=encrypt_data("52998224725"),
        phone="5512991234567",
        whatsapp_id="5512991234567@c.us",
    )
    session.add(usuario)
    session.commit()
    session.refresh(usuario)
    return usuario


@pytest.fixture()
def conversa(session: Session, cliente_zap: User) -> Conversation:
    conv = Conversation(user_id=cliente_zap.id, protocol="PROCON-TESTE-0001", status="open")
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv


@pytest.fixture()
def mensagem_bot(session: Session, conversa: Conversation) -> Message:
    msg = Message(
        conversation_id=conversa.id,
        role="bot",
        content="Você tem direito à troca em até 30 dias.",
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg
