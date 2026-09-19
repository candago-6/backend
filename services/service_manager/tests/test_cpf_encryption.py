"""Criptografia do CPF — onde ela realmente acontece, e onde não acontece.

O CPF é o único dado sensível guardado pelo sistema. A proteção é um token Fernet
com a chave ENCRYPTION_KEY.
"""

import pytest
from sqlmodel import Session, select

from app.models.entities import User
from app.utils.security import decrypt_data, encrypt_data
from conftest import auth


def test_ida_e_volta_da_criptografia():
    cifrado = encrypt_data("52998224725")

    assert cifrado != "52998224725"
    assert cifrado.startswith("gAAAA")
    assert decrypt_data(cifrado) == "52998224725"


def test_dois_cifrados_do_mesmo_cpf_sao_diferentes():
    """Fernet usa IV aleatório: não dá para achar CPFs iguais comparando o texto."""
    assert encrypt_data("52998224725") != encrypt_data("52998224725")


def test_valor_vazio_passa_direto():
    """O bot cria o usuário com cpf vazio antes do onboarding."""
    assert encrypt_data("") == ""
    assert decrypt_data("") == ""


def test_decrypt_de_texto_nao_cifrado_devolve_o_proprio_texto():
    """Tolerância a registros legados gravados antes da criptografia."""
    assert decrypt_data("52998224725") == "52998224725"


def test_decrypt_de_token_de_outra_chave_nao_explode():
    from cryptography.fernet import Fernet

    outro = Fernet(Fernet.generate_key()).encrypt(b"52998224725").decode()

    assert decrypt_data(outro) == outro


def test_endpoint_de_criacao_cifra_o_cpf(api_bot, session: Session):
    resposta = api_bot.post(
        "/api/v1/users",
        json={"name": "Novo", "cpf": "11144477735", "phone": "5512900000099"},
    )

    persistido = session.get(User, resposta.json()["id"])
    assert decrypt_data(persistido.cpf) == "11144477735"


def test_endpoint_de_atualizacao_cifra_o_cpf(api_bot, session: Session, cliente_zap: User):
    api_bot.put(f"/api/v1/users/{cliente_zap.id}", json={"cpf": "11144477735"})

    session.expire_all()
    assert decrypt_data(session.get(User, cliente_zap.id).cpf) == "11144477735"


def test_cpf_ja_cifrado_nao_e_cifrado_de_novo_pelo_validator():
    """O validator tem guarda de 'gAAAA' — mas veja o teste abaixo."""
    cifrado = encrypt_data("52998224725")

    assert User.encrypt_cpf(cifrado) == cifrado


@pytest.mark.xfail(
    reason=(
        "ARMADILHA CONHECIDA: o field_validator `encrypt_cpf` não roda em modelo "
        "SQLModel com table=True. Quem instancia User(...) direto no Python grava "
        "o CPF em texto puro — é o caso do app/seed_demo_data.py. "
        "Ver relatório: item 'validator de CPF é código morto'."
    ),
)
def test_instanciar_o_modelo_direto_tambem_deveria_cifrar(session: Session):
    usuario = User(name="Direto", cpf="52998224725", phone="5512900000098")
    session.add(usuario)
    session.commit()
    session.refresh(usuario)

    assert usuario.cpf != "52998224725", "CPF gravado em texto puro"


def test_listagem_nunca_devolve_cpf_em_claro(api_bot, analista, cliente_zap: User):
    """O painel espera o token cifrado e mascara o que não tem 11 dígitos."""
    usuarios = api_bot.get("/api/v1/users", headers=auth(analista)).json()

    assert usuarios[0]["cpf"].startswith("gAAAA")
    assert "52998224725" not in usuarios[0]["cpf"]
