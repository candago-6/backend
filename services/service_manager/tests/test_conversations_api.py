"""Máquina de estados da conversa — é o contrato que o whatsapp-bot consome.

Estados usados pelo produto: open, waiting_human, human_handover,
confirming_closure, awaiting_feedback, closed, archived.
"""

import pytest
from fastapi import status
from sqlmodel import Session

from app.models.entities import Conversation, User

ATIVOS = ["open", "waiting_human", "human_handover", "confirming_closure", "awaiting_feedback"]


def _nova_conversa(api_bot, user_id: int, protocolo: str, status_inicial: str = "open"):
    return api_bot.post(
        "/api/v1/conversations",
        json={"user_id": user_id, "protocol": protocolo, "status": status_inicial},
    )


def test_criar_conversa_comeca_aberta_e_sem_falhas(api_bot, cliente_zap: User):
    resposta = _nova_conversa(api_bot, cliente_zap.id, "PROCON-1700000000000")

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["status"] == "open"
    assert corpo["failed_attempts"] == 0
    assert corpo["is_onboarded"] is False
    assert corpo["patience_msg_sent"] is False
    assert corpo["assigned_admin_id"] is None
    assert corpo["created_at"] and corpo["updated_at"]


def test_listar_e_buscar_conversa_por_id(api_bot, conversa: Conversation):
    listagem = api_bot.get("/api/v1/conversations")
    individual = api_bot.get(f"/api/v1/conversations/{conversa.id}")

    assert listagem.status_code == status.HTTP_200_OK
    assert [c["id"] for c in listagem.json()] == [conversa.id]
    assert individual.json()["protocol"] == conversa.protocol


def test_buscar_conversa_inexistente_devolve_404(api_bot):
    resposta = api_bot.get("/api/v1/conversations/999999")

    assert resposta.status_code == status.HTTP_404_NOT_FOUND
    assert resposta.json() == {"detail": "Conversation not found"}


@pytest.mark.parametrize("estado", ATIVOS)
def test_conversa_ativa_e_encontrada_em_todos_os_estados_ativos(api_bot, session: Session, cliente_zap: User, estado: str
):
    conv = Conversation(user_id=cliente_zap.id, protocol=f"P-{estado}", status=estado)
    session.add(conv)
    session.commit()

    resposta = api_bot.get(f"/api/v1/conversations/active/{cliente_zap.id}")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["status"] == estado


@pytest.mark.parametrize("estado", ["closed", "archived"])
def test_conversa_encerrada_nao_conta_como_ativa(api_bot, session: Session, cliente_zap: User, estado: str
):
    """É o que faz o bot abrir um protocolo novo em vez de reaproveitar o antigo."""
    conv = Conversation(user_id=cliente_zap.id, protocol=f"P-{estado}", status=estado)
    session.add(conv)
    session.commit()

    resposta = api_bot.get(f"/api/v1/conversations/active/{cliente_zap.id}")

    assert resposta.status_code == status.HTTP_404_NOT_FOUND
    assert resposta.json() == {"detail": "No active conversation found"}


def test_usuario_sem_conversa_devolve_404(api_bot, cliente_zap: User):
    assert api_bot.get(f"/api/v1/conversations/active/{cliente_zap.id}").status_code == 404


def test_encerrar_conversa_zera_contadores(api_bot, session: Session, conversa: Conversation):
    conversa.failed_attempts = 2
    conversa.patience_msg_sent = True
    conversa.is_onboarded = True
    session.add(conversa)
    session.commit()

    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/close")

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["status"] == "closed"
    assert corpo["failed_attempts"] == 0
    assert corpo["patience_msg_sent"] is False
    # is_onboarded fica como está de propósito: é o histórico do protocolo.
    assert corpo["is_onboarded"] is True


def test_marcar_onboarding_concluido(api_bot, conversa: Conversation):
    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/mark-onboarded")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["is_onboarded"] is True


def test_marcar_mensagem_de_paciencia_enviada(api_bot, conversa: Conversation):
    """Trava que impede o Vigilante de repetir a mensagem de fila a cada minuto."""
    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/mark-patience-sent")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["patience_msg_sent"] is True


def test_tres_falhas_seguidas_escalam_para_atendente_humano(api_bot, conversa: Conversation):
    primeira = api_bot.post(f"/api/v1/conversations/{conversa.id}/increment-failures")
    segunda = api_bot.post(f"/api/v1/conversations/{conversa.id}/increment-failures")
    terceira = api_bot.post(f"/api/v1/conversations/{conversa.id}/increment-failures")

    assert (primeira.json()["failed_attempts"], primeira.json()["status"]) == (1, "open")
    assert (segunda.json()["failed_attempts"], segunda.json()["status"]) == (2, "open")
    assert (terceira.json()["failed_attempts"], terceira.json()["status"]) == (3, "waiting_human")


def test_resposta_entendida_zera_as_falhas(api_bot, conversa: Conversation):
    api_bot.post(f"/api/v1/conversations/{conversa.id}/increment-failures")
    api_bot.post(f"/api/v1/conversations/{conversa.id}/increment-failures")

    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/reset-failures")

    assert resposta.json()["failed_attempts"] == 0
    assert resposta.json()["status"] == "open"


def test_reset_de_falhas_nao_tira_a_conversa_da_fila_humana(api_bot, conversa: Conversation):
    """Documentado: quem já escalou continua em waiting_human até alguém assumir."""
    for _ in range(3):
        api_bot.post(f"/api/v1/conversations/{conversa.id}/increment-failures")

    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/reset-failures")

    assert resposta.json()["status"] == "waiting_human"


@pytest.mark.parametrize("estado", ATIVOS + ["closed", "archived"])
def test_troca_de_status_pelos_estados_do_produto(api_bot, conversa: Conversation, estado: str):
    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/update-status?status={estado}")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["status"] == estado


def test_update_status_aceita_qualquer_texto(api_bot, conversa: Conversation):
    """Comportamento atual, documentado: `status` é `str` cru no query param.

    Um valor fora da máquina de estados some do bot (não bate com os ativos) e
    aparece cru no painel. Ver relatório: item "status sem enum".
    """
    resposta = api_bot.post(f"/api/v1/conversations/{conversa.id}/update-status?status=banana")

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["status"] == "banana"
    assert api_bot.get(f"/api/v1/conversations/active/{conversa.user_id}").status_code == 404


def test_update_status_sem_o_parametro_devolve_422(api_bot, conversa: Conversation):
    assert api_bot.post(f"/api/v1/conversations/{conversa.id}/update-status").status_code == 422


@pytest.mark.parametrize(
    "rota",
    [
        "close",
        "mark-onboarded",
        "mark-patience-sent",
        "increment-failures",
        "reset-failures",
        "update-status?status=open",
    ],
)
def test_acoes_em_conversa_inexistente_devolvem_404(api_bot, rota: str):
    assert api_bot.post(f"/api/v1/conversations/999999/{rota}").status_code == 404


def test_protocolo_duplicado_e_rejeitado_pelo_banco(api_bot, session: Session, conversa: Conversation):
    """O protocolo é único: dois `PROCON-<timestamp>` iguais não coexistem."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        _nova_conversa(api_bot, conversa.user_id, conversa.protocol)
