"""Inventário de autenticação da API.

Este arquivo não testa regra de negócio: ele congela QUEM pode chamar O QUÊ.
Se alguém publicar uma rota nova sem credencial, o teste quebra aqui e não em
produção.

Três níveis de acesso:

  aberto           /api/v1/health e /auth/login — precisam ser alcançáveis sem nada.
  admin ou bot     rotas do atendimento. O whatsapp-bot é cliente da API e não tem
                   login, então autentica com o header X-Bot-Secret; o painel
                   autentica com o JWT do analista. Nenhuma delas precisa saber
                   quem chamou, só que o chamador é conhecido.
  admin            leituras em bloco (todos os consumidores, todas as mensagens,
                   todas as notas) e as ações de atendente. O bot nunca chama
                   nenhuma delas, então o segredo dele não abre essas portas.
  super admin      o pareamento do WhatsApp (/api/v1/bot/**). Restrito à conta de
                   ADMIN_EMAIL: quem lê o QR conecta o próprio aparelho na conta
                   de atendimento. Ver test_bot_connection.py.
"""

import pytest
from fastapi import status

from app import deps
from app.main import app
from conftest import auth

SEGREDO_BOT = {"X-Bot-Secret": deps.BOT_SECRET}

ABERTAS = {
    ("get", "/api/v1/health"),
    ("post", "/auth/login"),
}

# Rotas que o bot consome durante o atendimento.
# (método, rota, corpo) — o corpo precisa ser aceitável, senão o teste morre na
# validação antes de chegar na asserção de acesso.
ADMIN_OU_BOT = [
    ("POST", "/api/v1/users", {"name": "X", "cpf": "", "phone": "5512900000000"}),
    ("GET", "/api/v1/users/1", {}),
    ("PUT", "/api/v1/users/1", {}),
    ("GET", "/api/v1/users/phone/5512999999999", {}),
    ("GET", "/api/v1/users/whatsapp-id/x@c.us", {}),
    ("POST", "/api/v1/conversations", {'user_id': 1, 'protocol': 'P-SEC-1', 'status': 'open'}),
    ("GET", "/api/v1/conversations", {}),
    ("GET", "/api/v1/conversations/1", {}),
    ("GET", "/api/v1/conversations/active/1", {}),
    ("POST", "/api/v1/conversations/1/close", {}),
    ("POST", "/api/v1/conversations/1/mark-onboarded", {}),
    ("POST", "/api/v1/conversations/1/mark-patience-sent", {}),
    ("POST", "/api/v1/conversations/1/update-status?status=open", {}),
    ("POST", "/api/v1/conversations/1/increment-failures", {}),
    ("POST", "/api/v1/conversations/1/reset-failures", {}),
    ("POST", "/api/v1/messages", {'conversation_id': 1, 'role': 'user', 'content': 'x'}),
    ("POST", "/api/v1/feedback", {'conversation_id': 1, 'rating': 5}),
]

# Leituras em bloco: só o painel. O bot busca registro a registro.
SO_ADMIN_LEITURA = [
    ("GET", "/api/v1/users", {}),
    ("GET", "/api/v1/messages", {}),
    ("GET", "/api/v1/feedback", {}),
]

# Pareamento do WhatsApp: mais estrito ainda, restrito a uma única conta.
SO_SUPER_ADMIN = [
    ("GET", "/api/v1/bot/qr", {}),
    ("GET", "/api/v1/bot/status", {}),
]

# Ações de atendente e gestão de usuários.
SO_ADMIN_ACAO = [
    ("GET", "/api/v1/message-evaluations", {}),
    ("PUT", "/api/v1/messages/1/evaluation", {}),
    ("POST", "/api/v1/conversations/1/takeover", {}),
    ("POST", "/api/v1/conversations/1/agent-message", {}),
    ("POST", "/api/v1/conversations/1/release", {}),
    ("GET", "/api/v1/admin-users", {}),
    ("POST", "/api/v1/admin-users", {}),
    ("PUT", "/api/v1/admin-users/qualquer-id", {}),
    ("DELETE", "/api/v1/admin-users/qualquer-id", {}),
]

TODAS_PROTEGIDAS = ADMIN_OU_BOT + SO_ADMIN_LEITURA + SO_ADMIN_ACAO + SO_SUPER_ADMIN


def _rotas_sem_seguranca() -> set[tuple[str, str]]:
    schema = app.openapi()
    return {
        (metodo, caminho)
        for caminho, item in schema["paths"].items()
        for metodo, operacao in item.items()
        if "security" not in operacao
    }


def test_nenhuma_rota_nova_nasce_sem_autenticacao():
    """Trava de regressão do inventário inteiro."""
    atual = _rotas_sem_seguranca()

    novas = atual - ABERTAS
    assert not novas, (
        "Rota(s) publicada(s) sem autenticação: "
        f"{sorted(novas)}. Se for intencional, acrescente a ABERTAS com justificativa."
    )


def test_as_duas_rotas_abertas_continuam_abertas():
    """Health e login não podem exigir credencial — um é monitoração, o outro é
    como a credencial nasce."""
    assert ABERTAS <= _rotas_sem_seguranca()


@pytest.mark.parametrize("metodo,rota,corpo", TODAS_PROTEGIDAS)
def test_rota_protegida_recusa_chamada_sem_credencial(api, metodo: str, rota: str, corpo: dict):
    resposta = api.request(metodo, rota, json=corpo)

    assert resposta.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ), f"{metodo} {rota} respondeu {resposta.status_code} sem credencial"


@pytest.mark.parametrize("metodo,rota,corpo", TODAS_PROTEGIDAS)
def test_rota_protegida_recusa_token_invalido(api, metodo: str, rota: str, corpo: dict):
    resposta = api.request(
        metodo, rota, json=corpo, headers={"Authorization": "Bearer token-falso"}
    )

    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("metodo,rota,corpo", TODAS_PROTEGIDAS)
def test_rota_protegida_recusa_segredo_de_bot_errado(api, metodo: str, rota: str, corpo: dict):
    resposta = api.request(metodo, rota, json=corpo, headers={"X-Bot-Secret": "errado"})

    assert resposta.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.parametrize("metodo,rota,corpo", ADMIN_OU_BOT)
def test_bot_passa_nas_rotas_do_atendimento(api, metodo: str, rota: str, corpo: dict):
    """Com o segredo certo o bot entra — o que falhar aqui quebra o atendimento."""
    resposta = api.request(metodo, rota, json=corpo, headers=SEGREDO_BOT)

    assert resposta.status_code not in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ), f"{metodo} {rota} barrou o bot ({resposta.status_code})"


@pytest.mark.parametrize("metodo,rota,corpo", ADMIN_OU_BOT)
def test_analista_tambem_passa_nas_rotas_do_atendimento(api, analista, metodo: str, rota: str, corpo: dict):
    resposta = api.request(metodo, rota, json=corpo, headers=auth(analista))

    assert resposta.status_code not in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.parametrize("metodo,rota,corpo", SO_ADMIN_LEITURA + SO_ADMIN_ACAO + SO_SUPER_ADMIN)
def test_segredo_do_bot_nao_abre_o_que_e_do_painel(api, metodo: str, rota: str, corpo: dict):
    """Least privilege: o bot nunca lê o cadastro inteiro nem age como atendente.

    Se o segredo vazar, ele não serve para varrer PII."""
    resposta = api.request(metodo, rota, json=corpo, headers=SEGREDO_BOT)

    assert resposta.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ), f"{metodo} {rota} aceitou o segredo do bot ({resposta.status_code})"


@pytest.mark.parametrize(
    "metodo,rota",
    [
        ("GET", "/api/v1/admin-users"),
        ("POST", "/api/v1/admin-users"),
        ("PUT", "/api/v1/admin-users/qualquer-id"),
        ("DELETE", "/api/v1/admin-users/qualquer-id"),
    ],
)
def test_gestao_de_usuarios_e_exclusiva_do_gestor(api, analista, metodo: str, rota: str):
    resposta = api.request(metodo, rota, json={}, headers=auth(analista))

    assert resposta.status_code == status.HTTP_403_FORBIDDEN
    assert resposta.json() == {"detail": "Acesso restrito a gestores"}


def test_health_check_responde_sem_credencial(api):
    resposta = api.get("/api/v1/health")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json() == {"status": "ok", "service": "service-manager"}


def test_dados_pessoais_nao_vazam_sem_autenticacao(api, cliente_zap):
    """Era a brecha do relatório (C1): a listagem devolvia nome e telefone de todos
    os consumidores sem nenhuma credencial."""
    assert api.get("/api/v1/users").status_code == status.HTTP_401_UNAUTHORIZED


def test_historico_de_conversas_nao_vaza_sem_autenticacao(api_bot, api, conversa):
    """Idem para a transcrição completa dos atendimentos."""
    api_bot.post(
        "/api/v1/messages",
        json={"conversation_id": conversa.id, "role": "user", "content": "meu CPF é ..."},
    )

    assert api.get("/api/v1/messages").status_code == status.HTTP_401_UNAUTHORIZED


def test_cpf_nunca_volta_em_claro_na_listagem(api_bot, analista, cliente_zap):
    """O que a API devolve é o token Fernet, não o CPF — o painel mascara o resto.

    A criptografia em si está coberta em `test_cpf_encryption.py`.
    """
    usuarios = api_bot.get("/api/v1/users", headers=auth(analista)).json()

    assert usuarios[0]["cpf"].startswith("gAAAA")
    assert "52998224725" not in usuarios[0]["cpf"]
