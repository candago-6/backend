"""CORS: quais origens o navegador pode usar para falar com a API.

Não é regra de negócio nem autenticação — é a configuração que decide se o
painel abre para alguém além de quem está na própria VM. Fica em teste porque as
duas formas de errar são silenciosas: uma barra a mais no .env, ou `http` onde o
painel serve `https`, e todas as chamadas morrem no preflight sem erro no log da
API.
"""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import parse_cors_origins

PAINEL = "http://localhost:3000"


def preflight(client: TestClient, origem: str) -> str | None:
    resposta = client.options(
        "/auth/login",
        headers={"Origin": origem, "Access-Control-Request-Method": "POST"},
    )
    return resposta.headers.get("access-control-allow-origin")


def app_com_origens(bruto: str) -> TestClient:
    """Sobe uma API mínima com a mesma configuração de CORS do service-manager.

    `app.main` lê CORS_ORIGINS no import, então não dá para variar a lista na
    aplicação real sem reimportá-la; o que este teste precisa cobrir é o caminho
    do .env até o header, e ele é idêntico aqui.
    """
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_origins(bruto),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app)


@pytest.mark.parametrize(
    "bruto, esperado",
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("http://a:3000,http://b:3000", ["http://a:3000", "http://b:3000"]),
        ("  http://a:3000 ,\thttp://b:3000  ", ["http://a:3000", "http://b:3000"]),
        ("http://a:3000/", ["http://a:3000"]),
        ("http://a:3000,,", ["http://a:3000"]),
        ("", []),
    ],
)
def test_parse_cors_origins(bruto: str, esperado: list[str]):
    assert parse_cors_origins(bruto) == esperado


def test_painel_local_continua_aceito(api):
    """O padrão não mudou: quem já desenvolvia contra localhost não sente nada."""
    assert preflight(api, PAINEL) == PAINEL


def test_origem_desconhecida_nao_recebe_permissao(api):
    assert preflight(api, "http://intruso.example.com") is None


def test_barra_final_no_env_nao_derruba_o_painel():
    """`CORS_ORIGINS=http://10.0.0.5:3000/` — o erro de digitação mais provável.

    O navegador manda a origem sem barra; sem o rstrip, nada casaria.
    """
    client = app_com_origens("http://10.0.0.5:3000/")

    assert preflight(client, "http://10.0.0.5:3000") == "http://10.0.0.5:3000"


def test_varias_origens_convivem():
    """Caso real do deploy: localhost para desenvolver + IP da VM para o cliente."""
    client = app_com_origens(f"{PAINEL},http://10.0.0.5:3000")

    assert preflight(client, PAINEL) == PAINEL
    assert preflight(client, "http://10.0.0.5:3000") == "http://10.0.0.5:3000"


def test_esquema_errado_nao_casa():
    """https != http para o navegador. Vale como aviso: quando o painel passar a
    servir por TLS, a origem daqui precisa mudar junto."""
    client = app_com_origens("http://10.0.0.5:3000")

    assert preflight(client, "https://10.0.0.5:3000") is None
