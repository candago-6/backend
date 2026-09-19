"""Login do painel e ciclo de vida do token JWT."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from jose import jwt

from app import security
from app.models.admin import AdminUser, Role
from conftest import SENHA_PADRAO, auth


def test_login_com_credenciais_validas_devolve_token_e_perfil(api, gestor: AdminUser):
    resposta = api.post(
        "/auth/login",
        json={"email": gestor.email, "password": SENHA_PADRAO},
    )

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["token"]
    assert corpo["user"] == {
        "id": gestor.id,
        "name": gestor.name,
        "email": gestor.email,
        "role": "gestor",
    }
    # O hash nunca pode vazar na resposta do login.
    assert "hashed_password" not in corpo["user"]
    assert "password" not in corpo["user"]


def test_token_do_login_identifica_o_usuario_correto(api, gestor: AdminUser):
    token = api.post(
        "/auth/login", json={"email": gestor.email, "password": SENHA_PADRAO}
    ).json()["token"]

    assert security.decode_access_token(token) == gestor.id


def test_token_expira_em_oito_horas(api, gestor: AdminUser):
    token = api.post(
        "/auth/login", json={"email": gestor.email, "password": SENHA_PADRAO}
    ).json()["token"]

    exp = jwt.get_unverified_claims(token)["exp"]
    restante = datetime.fromtimestamp(exp, tz=timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(hours=7, minutes=55) < restante <= timedelta(hours=8)


def test_login_com_senha_errada_e_recusado(api, gestor: AdminUser):
    resposta = api.post("/auth/login", json={"email": gestor.email, "password": "outraSenha"})

    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED
    assert resposta.json() == {"detail": "E-mail ou senha inválidos"}


def test_login_com_email_inexistente_devolve_a_mesma_mensagem(api):
    """Mensagem idêntica à da senha errada: não revela quais e-mails existem."""
    resposta = api.post(
        "/auth/login", json={"email": "ninguem@procon.sp.gov.br", "password": SENHA_PADRAO}
    )

    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED
    assert resposta.json() == {"detail": "E-mail ou senha inválidos"}


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "nao-e-email", "password": SENHA_PADRAO},
        {"email": "alguem@procon.sp.gov.br"},
        {"password": SENHA_PADRAO},
        {},
    ],
)
def test_login_com_payload_invalido_devolve_422(api, payload):
    assert api.post("/auth/login", json=payload).status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_senha_e_guardada_como_hash_bcrypt(session, gestor: AdminUser):
    assert gestor.hashed_password != SENHA_PADRAO
    assert gestor.hashed_password.startswith("$2")
    assert security.verify_password(SENHA_PADRAO, gestor.hashed_password)


# --- Validação do token nas rotas protegidas -------------------------------

ROTA_PROTEGIDA = "/api/v1/message-evaluations"


def test_rota_protegida_sem_header_authorization(api):
    assert api.get(ROTA_PROTEGIDA).status_code == status.HTTP_401_UNAUTHORIZED


def test_rota_protegida_com_esquema_diferente_de_bearer(api):
    resposta = api.get(ROTA_PROTEGIDA, headers={"Authorization": "Basic YWJjOjEyMw=="})
    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_rota_protegida_com_token_malformado(api):
    resposta = api.get(ROTA_PROTEGIDA, headers={"Authorization": "Bearer nao-e-um-jwt"})
    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_rota_protegida_com_token_expirado(api, gestor: AdminUser):
    expirado = jwt.encode(
        {"sub": gestor.id, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        security.SECRET_KEY,
        algorithm="HS256",
    )

    resposta = api.get(ROTA_PROTEGIDA, headers={"Authorization": f"Bearer {expirado}"})
    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_rota_protegida_com_token_assinado_por_outra_chave(api, gestor: AdminUser):
    forjado = jwt.encode(
        {"sub": gestor.id, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "chave-do-atacante",
        algorithm="HS256",
    )

    resposta = api.get(ROTA_PROTEGIDA, headers={"Authorization": f"Bearer {forjado}"})
    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_token_de_usuario_removido_para_de_funcionar(api, session, gestor: AdminUser):
    headers = auth(gestor)
    assert api.get(ROTA_PROTEGIDA, headers=headers).status_code == status.HTTP_200_OK

    session.delete(gestor)
    session.commit()

    resposta = api.get(ROTA_PROTEGIDA, headers=headers)
    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED
    assert resposta.json() == {"detail": "Usuário não encontrado"}


def test_token_valido_sem_claim_sub_devolve_401(api):
    """Regressão do achado M8: o KeyError de `payload["sub"]` escapava do
    `except JWTError` e virava 500. Hoje as duas dependências tratam."""
    sem_sub = jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        security.SECRET_KEY,
        algorithm="HS256",
    )

    resposta = api.get(ROTA_PROTEGIDA, headers={"Authorization": f"Bearer {sem_sub}"})
    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_analista_tambem_acessa_rotas_de_atendimento(api, analista: AdminUser):
    """Só /api/v1/admin-users é restrito a gestor; o painel é dos dois perfis."""
    assert api.get(ROTA_PROTEGIDA, headers=auth(analista)).status_code == status.HTTP_200_OK
