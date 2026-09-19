"""Endpoints do consumidor de WhatsApp (tabela `user`), usados pelo bot."""

import pytest
from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models.entities import User
from app.utils.security import decrypt_data
from conftest import auth

NOVO = {
    "name": "Cliente WhatsApp",
    "cpf": "52998224725",
    "phone": "5512999887766",
    "whatsapp_id": "5512999887766@c.us",
}


def test_criar_usuario_guarda_o_cpf_criptografado(api_bot, session: Session):
    resposta = api_bot.post("/api/v1/users", json=NOVO)

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["id"]
    assert corpo["name"] == NOVO["name"]
    assert corpo["phone"] == NOVO["phone"]

    persistido = session.get(User, corpo["id"])
    assert persistido.cpf != NOVO["cpf"], "CPF não pode ficar em claro no banco"
    assert persistido.cpf.startswith("gAAAA"), "CPF deve ser um token Fernet"
    assert decrypt_data(persistido.cpf) == NOVO["cpf"]


def test_criar_usuario_sem_cpf_e_aceito(api_bot, session: Session):
    """O bot cria o usuário com cpf vazio antes do onboarding."""
    resposta = api_bot.post(
        "/api/v1/users",
        json={"name": "Cliente WhatsApp", "cpf": "", "phone": "5512900000001"},
    )

    assert resposta.status_code == status.HTTP_200_OK
    assert session.get(User, resposta.json()["id"]).cpf == ""


@pytest.mark.xfail(
    raises=IntegrityError,
    reason=(
        "BUG: POST /users recebe o modelo de tabela cru e o SQLModel não valida "
        "table=True, então campo obrigatório ausente vira None, quebra no INSERT "
        "e devolve 500 em vez de 422. Ver relatório: 'modelos de tabela como schema'."
    ),
)
def test_criar_usuario_sem_campos_obrigatorios_devolve_422(api_bot):
    assert api_bot.post("/api/v1/users", json={"name": "Só nome"}).status_code == 422


def test_buscar_usuario_por_id(api_bot, cliente_zap: User):
    resposta = api_bot.get(f"/api/v1/users/{cliente_zap.id}")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["phone"] == cliente_zap.phone


def test_buscar_usuario_por_telefone(api_bot, cliente_zap: User):
    resposta = api_bot.get(f"/api/v1/users/phone/{cliente_zap.phone}")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["id"] == cliente_zap.id


def test_buscar_usuario_por_whatsapp_id(api_bot, cliente_zap: User):
    resposta = api_bot.get(f"/api/v1/users/whatsapp-id/{cliente_zap.whatsapp_id}")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["id"] == cliente_zap.id


@pytest.mark.parametrize(
    "rota",
    [
        "/api/v1/users/999999",
        "/api/v1/users/phone/5500000000000",
        "/api/v1/users/whatsapp-id/inexistente@c.us",
    ],
)
def test_busca_sem_resultado_devolve_404(api_bot, rota):
    """O bot depende do 404 para decidir entre achar e criar o usuário."""
    resposta = api_bot.get(rota)

    assert resposta.status_code == status.HTTP_404_NOT_FOUND
    assert resposta.json() == {"detail": "User not found"}


def test_listar_usuarios(api_bot, analista, cliente_zap: User):
    """A listagem de consumidores é do painel; o bot busca por telefone/LID."""
    resposta = api_bot.get("/api/v1/users", headers=auth(analista))

    assert resposta.status_code == status.HTTP_200_OK
    assert [u["id"] for u in resposta.json()] == [cliente_zap.id]


def test_atualizar_nome_e_cpf_no_fim_do_onboarding(api_bot, session: Session, cliente_zap: User):
    resposta = api_bot.put(
        f"/api/v1/users/{cliente_zap.id}",
        json={"name": "Maria da Silva", "cpf": "11144477735"},
    )

    assert resposta.status_code == status.HTTP_200_OK
    session.expire_all()
    persistido = session.get(User, cliente_zap.id)
    assert persistido.name == "Maria da Silva"
    assert decrypt_data(persistido.cpf) == "11144477735"
    # campos não enviados continuam como estavam
    assert persistido.phone == "5512991234567"


def test_atualizar_apenas_o_whatsapp_id(api_bot, session: Session, cliente_zap: User):
    """Caminho que o bot usa quando descobre o LID de um usuário já cadastrado."""
    resposta = api_bot.put(
        f"/api/v1/users/{cliente_zap.id}",
        json={"whatsapp_id": "999888@lid"},
    )

    assert resposta.status_code == status.HTTP_200_OK
    session.expire_all()
    persistido = session.get(User, cliente_zap.id)
    assert persistido.whatsapp_id == "999888@lid"
    assert persistido.name == "Maria Consumidora"


def test_atualizar_usuario_inexistente_devolve_404(api_bot):
    resposta = api_bot.put("/api/v1/users/999999", json={"name": "Fantasma"})

    assert resposta.status_code == status.HTTP_404_NOT_FOUND


def test_cpf_nao_e_criptografado_duas_vezes_na_atualizacao(api_bot, session: Session, cliente_zap: User):
    api_bot.put(f"/api/v1/users/{cliente_zap.id}", json={"cpf": "11144477735"})
    api_bot.put(f"/api/v1/users/{cliente_zap.id}", json={"cpf": "11144477735"})

    session.expire_all()
    assert decrypt_data(session.get(User, cliente_zap.id).cpf) == "11144477735"


def test_post_users_aceita_id_vindo_do_cliente(api_bot, session: Session):
    """Comportamento atual, documentado: o endpoint recebe o modelo de tabela cru,
    então o cliente consegue escolher a chave primária (mass assignment).
    Ver relatório: item "modelos de tabela como schema de entrada"."""
    resposta = api_bot.post("/api/v1/users", json={**NOVO, "id": 4242})

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["id"] == 4242
