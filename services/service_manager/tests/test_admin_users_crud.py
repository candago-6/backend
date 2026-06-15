from fastapi import status
from sqlmodel import Session, select

from app import security
from app.models.admin import AdminUser, Role


def _valid_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "name": "Maria Gestora",
        "email": "maria.gestora@example.com",
        "password": "SenhaForte123",
        "role": "gestor",
    }
    payload.update(overrides)
    return payload


def test_create_admin_user_with_valid_data_persists_hashed_password(client, session: Session):
    response = client.post("/api/v1/admin-users", json=_valid_payload())

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["id"]
    assert body["name"] == "Maria Gestora"
    assert body["email"] == "maria.gestora@example.com"
    assert body["role"] == "gestor"
    assert "password" not in body
    assert "hashed_password" not in body

    persisted = session.exec(
        select(AdminUser).where(AdminUser.email == "maria.gestora@example.com")
    ).one()
    assert persisted.id == body["id"]
    assert persisted.hashed_password != "SenhaForte123"
    assert security.verify_password("SenhaForte123", persisted.hashed_password)


def test_create_admin_user_blocks_invalid_data(client, session: Session):
    invalid_email_response = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(email="email-invalido"),
    )
    invalid_role_response = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(email="role.invalido@example.com", role="admin"),
    )

    assert invalid_email_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert invalid_role_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert session.exec(select(AdminUser)).all() == []


def test_create_admin_user_blocks_duplicate_email_with_error_message(client):
    payload = _valid_payload(email="duplicado@example.com")
    first_response = client.post("/api/v1/admin-users", json=payload)
    duplicate_response = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(name="Outro Usuario", email="duplicado@example.com"),
    )

    assert first_response.status_code == status.HTTP_201_CREATED
    assert duplicate_response.status_code == status.HTTP_409_CONFLICT
    assert duplicate_response.json() == {"detail": "E-mail já cadastrado"}


def test_list_admin_users_returns_registered_records(client):
    maria = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(name="Maria Gestora", email="maria@example.com", role="gestor"),
    ).json()
    ana = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(name="Ana Analista", email="ana@example.com", role="analista"),
    ).json()

    response = client.get("/api/v1/admin-users")

    assert response.status_code == status.HTTP_200_OK
    users_by_email = {user["email"]: user for user in response.json()}
    assert users_by_email["maria@example.com"] == maria
    assert users_by_email["ana@example.com"] == ana


def test_update_admin_user_changes_existing_information_and_persists(client, session: Session):
    created = client.post("/api/v1/admin-users", json=_valid_payload()).json()
    update_payload = {
        "name": "Maria Analista",
        "email": "maria.analista@example.com",
        "role": "analista",
    }

    response = client.put(f"/api/v1/admin-users/{created['id']}", json=update_payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {**update_payload, "id": created["id"]}

    persisted = session.get(AdminUser, created["id"])
    assert persisted is not None
    assert persisted.name == "Maria Analista"
    assert persisted.email == "maria.analista@example.com"
    assert persisted.role == Role.analista


def test_update_admin_user_reports_missing_and_duplicate_email_errors(client):
    existing = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(email="existente@example.com"),
    ).json()
    target = client.post(
        "/api/v1/admin-users",
        json=_valid_payload(email="alvo@example.com"),
    ).json()

    missing_response = client.put(
        "/api/v1/admin-users/id-inexistente",
        json={"name": "Nao Existe", "email": "nao.existe@example.com", "role": "gestor"},
    )
    duplicate_response = client.put(
        f"/api/v1/admin-users/{target['id']}",
        json={"name": "Alvo", "email": existing["email"], "role": "analista"},
    )

    assert missing_response.status_code == status.HTTP_404_NOT_FOUND
    assert missing_response.json() == {"detail": "Usuário não encontrado"}
    assert duplicate_response.status_code == status.HTTP_409_CONFLICT
    assert duplicate_response.json() == {"detail": "E-mail já cadastrado"}


def test_delete_admin_user_when_allowed_removes_record_from_database(client, session: Session):
    created = client.post("/api/v1/admin-users", json=_valid_payload()).json()

    response = client.delete(f"/api/v1/admin-users/{created['id']}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""
    assert session.get(AdminUser, created["id"]) is None
    assert client.get("/api/v1/admin-users").json() == []


def test_delete_admin_user_reports_missing_record(client):
    response = client.delete("/api/v1/admin-users/id-inexistente")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Usuário não encontrado"}
