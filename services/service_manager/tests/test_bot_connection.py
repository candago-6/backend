"""Pareamento do WhatsApp pelo painel — restrito ao administrador do sistema.

O bot não é publicado no host, então estas duas rotas são a única porta de
entrada para o QR. Quem lê aquele QR conecta o próprio aparelho na conta de
atendimento do Procon, por isso a trava é por conta, não por cargo.
"""

import httpx
import pytest
from fastapi import status

from app import deps, main as main_module
from app.models.admin import AdminUser, Role
from conftest import SENHA_PADRAO, auth
from app import security

PNG_FALSO = b"\x89PNG\r\n\x1a\n" + b"conteudo do qr"


class BotHTTPFalso:
    """Substitui o httpx.get que o service-manager usa para falar com o bot."""

    def __init__(self, status_code=200, content=PNG_FALSO, payload=None, erro=None):
        self.status_code, self.content, self.payload, self.erro = status_code, content, payload, erro
        self.chamadas = []

    def __call__(self, url, **kwargs):
        if self.erro:
            raise self.erro
        self.chamadas.append((url, kwargs.get("headers", {})))
        return httpx.Response(
            self.status_code,
            content=self.content,
            json=self.payload if self.payload is not None else None,
            request=httpx.Request("GET", url),
        )


@pytest.fixture()
def bot_http(monkeypatch):
    def instalar(**kwargs):
        falso = BotHTTPFalso(**kwargs)
        monkeypatch.setattr(main_module.httpx, "get", falso)
        return falso

    return instalar


@pytest.fixture()
def super_admin(session) -> AdminUser:
    admin = AdminUser(
        name="Administrador",
        email=deps.SUPER_ADMIN_EMAIL,
        hashed_password=security.hash_password(SENHA_PADRAO),
        role=Role.gestor,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


# --- quem pode entrar -------------------------------------------------------


def test_super_admin_ve_o_qr(api, super_admin, bot_http):
    falso = bot_http(content=PNG_FALSO)

    resposta = api.get("/api/v1/bot/qr", headers=auth(super_admin))

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.headers["content-type"] == "image/png"
    assert resposta.content == PNG_FALSO


def test_o_qr_nao_pode_ser_cacheado(api, super_admin, bot_http):
    """O QR expira em segundos; servir de cache entrega um código morto."""
    bot_http()

    resposta = api.get("/api/v1/bot/qr", headers=auth(super_admin))

    assert "no-store" in resposta.headers.get("cache-control", "")


def test_gestor_comum_nao_ve_o_qr(api, gestor, bot_http):
    """Mais estrito que require_gestor: cargo não basta, tem que ser a conta."""
    bot_http()

    resposta = api.get("/api/v1/bot/qr", headers=auth(gestor))

    assert resposta.status_code == status.HTTP_403_FORBIDDEN
    assert resposta.json() == {"detail": "Acesso restrito ao administrador do sistema"}


def test_analista_nao_ve_o_qr(api, analista, bot_http):
    bot_http()

    assert api.get("/api/v1/bot/qr", headers=auth(analista)).status_code == 403


def test_sem_token_nao_ve_o_qr(api, bot_http):
    bot_http()

    assert api.get("/api/v1/bot/qr").status_code == status.HTTP_401_UNAUTHORIZED


def test_segredo_do_bot_nao_abre_a_rota(api, bot_http):
    """A credencial do bot serve para o atendimento, não para o pareamento."""
    bot_http()

    resposta = api.get("/api/v1/bot/qr", headers={"X-Bot-Secret": deps.BOT_SECRET})

    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_comparacao_do_email_ignora_caixa_e_espaco(api, session, bot_http):
    bot_http()
    admin = AdminUser(
        name="Admin",
        email=f"  {deps.SUPER_ADMIN_EMAIL.upper()}  ".strip(),
        hashed_password="x",
        role=Role.gestor,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)

    assert api.get("/api/v1/bot/qr", headers=auth(admin)).status_code == 200


# --- o que a rota devolve ---------------------------------------------------


def test_credencial_do_bot_vai_no_header(api, super_admin, bot_http):
    falso = bot_http()

    api.get("/api/v1/bot/qr", headers=auth(super_admin))

    url, headers = falso.chamadas[0]
    assert url.endswith("/qr")
    assert headers["X-Bot-Secret"] == main_module.BOT_SECRET


def test_sem_qr_pendente_devolve_404_explicando(api, super_admin, bot_http):
    bot_http(status_code=404, content=b"")

    resposta = api.get("/api/v1/bot/qr", headers=auth(super_admin))

    assert resposta.status_code == status.HTTP_404_NOT_FOUND
    assert "já está conectado" in resposta.json()["detail"]


def test_bot_fora_do_ar_devolve_502(api, super_admin, bot_http):
    bot_http(erro=httpx.ConnectError("sem rota para o host"))

    resposta = api.get("/api/v1/bot/qr", headers=auth(super_admin))

    assert resposta.status_code == status.HTTP_502_BAD_GATEWAY
    assert "Bot indisponível" in resposta.json()["detail"]


def test_segredo_divergente_entre_servicos_da_mensagem_util(api, super_admin, bot_http):
    """Erro provável em produção: BOT_SECRET trocado num serviço e não no outro."""
    bot_http(status_code=401, content=b"")

    resposta = api.get("/api/v1/bot/qr", headers=auth(super_admin))

    assert resposta.status_code == status.HTTP_502_BAD_GATEWAY
    assert "BOT_SECRET" in resposta.json()["detail"]


# --- status -----------------------------------------------------------------


@pytest.mark.parametrize(
    "estado,conectado",
    [("conectado", True), ("aguardando_leitura", False), ("iniciando", False)],
)
def test_status_repassa_o_estado_do_pareamento(api, super_admin, bot_http, estado, conectado):
    bot_http(content=None, payload={"estado": estado, "conectado": conectado, "qrPendente": not conectado})

    resposta = api.get("/api/v1/bot/status", headers=auth(super_admin))

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["estado"] == estado


def test_status_tambem_e_restrito(api, gestor, analista, bot_http):
    bot_http(content=None, payload={"estado": "conectado"})

    assert api.get("/api/v1/bot/status", headers=auth(gestor)).status_code == 403
    assert api.get("/api/v1/bot/status", headers=auth(analista)).status_code == 403
    assert api.get("/api/v1/bot/status").status_code == 401


def test_status_com_bot_fora_do_ar(api, super_admin, bot_http):
    bot_http(erro=httpx.ConnectError("recusada"))

    assert api.get("/api/v1/bot/status", headers=auth(super_admin)).status_code == 502
