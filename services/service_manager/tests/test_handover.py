"""Atendimento humano ao vivo: assumir, responder pelo painel e encerrar.

O envio ao WhatsApp é substituído pela fixture `bot`, então nada sai de verdade.
"""

from fastapi import status
from sqlmodel import Session, select

from app.models.entities import Conversation, Message, User
from conftest import auth

# --- Assumir atendimento (takeover) ----------------------------------------


def test_takeover_exige_autenticacao(api, conversa: Conversation):
    assert api.post(f"/api/v1/conversations/{conversa.id}/takeover").status_code == 401


def test_analista_assume_conversa_na_fila(api, api_bot, session, conversa: Conversation, analista):
    api_bot.post(f"/api/v1/conversations/{conversa.id}/update-status?status=waiting_human")

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista)
    )

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["status"] == "human_handover"
    assert corpo["assigned_admin_id"] == analista.id


def test_analista_assume_conversa_que_ainda_esta_com_o_bot(api, conversa: Conversation, analista):
    """O painel permite interceptar um atendimento `open`, sem esperar a escalada."""
    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista)
    )

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["status"] == "human_handover"


def test_takeover_de_conversa_encerrada_e_bloqueado(api, api_bot, conversa: Conversation, analista):
    api_bot.post(f"/api/v1/conversations/{conversa.id}/close")

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista)
    )

    assert resposta.status_code == status.HTTP_409_CONFLICT
    assert resposta.json() == {"detail": "Conversation already closed"}


def test_takeover_de_conversa_ja_assumida_por_outro_analista(
    api, conversa: Conversation, analista, outro_analista
):
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(outro_analista)
    )

    assert resposta.status_code == status.HTTP_409_CONFLICT
    assert resposta.json() == {"detail": "Conversation already taken by another agent"}


def test_takeover_repetido_pelo_mesmo_analista_e_idempotente(api, conversa: Conversation, analista):
    primeira = api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))
    segunda = api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    assert primeira.status_code == segunda.status_code == status.HTTP_200_OK
    assert segunda.json()["assigned_admin_id"] == analista.id


def test_takeover_de_conversa_inexistente_devolve_404(api, analista):
    assert api.post("/api/v1/conversations/999999/takeover", headers=auth(analista)).status_code == 404


# --- Responder pelo painel --------------------------------------------------


def test_mensagem_do_analista_vai_ao_whatsapp_e_e_gravada(
    api, session: Session, conversa: Conversation, analista, bot, cliente_zap: User
):
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/agent-message",
        json={"content": "Boa tarde, já estou vendo seu caso."},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["role"] == "agent"
    assert resposta.json()["content"] == "Boa tarde, já estou vendo seu caso."
    # saiu pelo bot, para o chat certo
    assert bot.enviados == [(cliente_zap.whatsapp_id, "Boa tarde, já estou vendo seu caso.")]
    # e ficou no histórico com papel `agent` (é o que pinta de verde no painel)
    gravadas = session.exec(select(Message).where(Message.conversation_id == conversa.id)).all()
    assert [(m.role, m.content) for m in gravadas] == [
        ("agent", "Boa tarde, já estou vendo seu caso.")
    ]


def test_sem_whatsapp_id_a_mensagem_vai_para_o_jid_do_telefone(
    api, session: Session, cliente_zap: User, analista, bot
):
    cliente_zap.whatsapp_id = None
    conv = Conversation(user_id=cliente_zap.id, protocol="P-SEM-LID", status="human_handover")
    session.add_all([cliente_zap, conv])
    session.commit()
    session.refresh(conv)

    api.post(
        f"/api/v1/conversations/{conv.id}/agent-message",
        json={"content": "oi"},
        headers=auth(analista),
    )

    assert bot.enviados[0][0] == f"{cliente_zap.phone}@c.us"


def test_falha_no_bot_nao_grava_mensagem_fantasma(
    api, session: Session, conversa: Conversation, analista, bot
):
    """Entrega primeiro, persiste depois: se o WhatsApp recusou, o painel não
    pode mostrar uma resposta que o cliente nunca recebeu."""
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))
    bot.falhar = True

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/agent-message",
        json={"content": "mensagem que não sai"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_502_BAD_GATEWAY
    assert "Failed to deliver" in resposta.json()["detail"]
    assert session.exec(select(Message)).all() == []


def test_nao_da_para_responder_conversa_fora_do_handover(api, conversa: Conversation, analista, bot):
    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/agent-message",
        json={"content": "oi"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_409_CONFLICT
    assert resposta.json() == {"detail": "Conversation is not under human handover"}
    assert bot.enviados == []


def test_handover_sem_dono_e_reivindicado_por_quem_responde_primeiro(
    api, api_bot, conversa: Conversation, analista, bot
):
    """O bot joga a conversa para human_handover quando o analista responde pelo
    celular; nesse caminho não houve takeover e `assigned_admin_id` está vazio."""
    api_bot.post(f"/api/v1/conversations/{conversa.id}/update-status?status=human_handover")

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/agent-message",
        json={"content": "assumindo agora"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_200_OK
    assert api_bot.get(f"/api/v1/conversations/{conversa.id}").json()["assigned_admin_id"] == analista.id


def test_analista_nao_responde_conversa_de_outro(
    api, conversa: Conversation, analista, outro_analista, bot
):
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/agent-message",
        json={"content": "intruso"},
        headers=auth(outro_analista),
    )

    assert resposta.status_code == status.HTTP_403_FORBIDDEN
    assert resposta.json() == {"detail": "Conversation assigned to another agent"}
    assert bot.enviados == []


def test_agent_message_exige_autenticacao(api, conversa: Conversation, bot):
    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/agent-message", json={"content": "oi"}
    )

    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED
    assert bot.enviados == []


def test_agent_message_sem_conteudo_devolve_422(api, conversa: Conversation, analista):
    assert (
        api.post(
            f"/api/v1/conversations/{conversa.id}/agent-message",
            json={},
            headers=auth(analista),
        ).status_code
        == 422
    )


def test_agent_message_em_conversa_inexistente_devolve_404(api, analista):
    resposta = api.post(
        "/api/v1/conversations/999999/agent-message",
        json={"content": "oi"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_404_NOT_FOUND


# --- Encerrar / devolver ----------------------------------------------------


def test_encerrar_pede_avaliacao_ao_cliente(
    api, session: Session, conversa: Conversation, analista, bot
):
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/release", headers=auth(analista)
    )

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["status"] == "awaiting_feedback"
    assert corpo["assigned_admin_id"] is None
    assert corpo["patience_msg_sent"] is False
    # o cliente recebe o aviso de encerramento com o pedido de nota 1-5
    assert "1 a 5" in bot.ultimo_texto
    # e a mensagem fica registrada no histórico como `system`
    sistema = session.exec(select(Message).where(Message.role == "system")).all()
    assert len(sistema) == 1
    assert sistema[0].content == bot.ultimo_texto


def test_devolver_ao_bot_volta_para_open_sem_avisar_o_cliente(
    api, conversa: Conversation, analista, bot
):
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/release?to_bot=true", headers=auth(analista)
    )

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["status"] == "open"
    assert resposta.json()["assigned_admin_id"] is None
    assert resposta.json()["failed_attempts"] == 0
    assert bot.enviados == []


def test_encerramento_segue_mesmo_se_o_whatsapp_estiver_fora(
    api, session: Session, conversa: Conversation, analista, bot
):
    """Best-effort de propósito: o painel não pode ficar preso num atendimento
    porque o bot caiu."""
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))
    bot.falhar = True

    resposta = api.post(f"/api/v1/conversations/{conversa.id}/release", headers=auth(analista))

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["status"] == "awaiting_feedback"


def test_analista_nao_encerra_conversa_de_outro(
    api, conversa: Conversation, analista, outro_analista, bot
):
    api.post(f"/api/v1/conversations/{conversa.id}/takeover", headers=auth(analista))

    resposta = api.post(
        f"/api/v1/conversations/{conversa.id}/release", headers=auth(outro_analista)
    )

    assert resposta.status_code == status.HTTP_403_FORBIDDEN
    assert resposta.json() == {"detail": "Conversation assigned to another agent"}


def test_release_exige_autenticacao(api, conversa: Conversation, bot):
    assert api.post(f"/api/v1/conversations/{conversa.id}/release").status_code == 401


def test_release_de_conversa_inexistente_devolve_404(api, analista):
    assert api.post("/api/v1/conversations/999999/release", headers=auth(analista)).status_code == 404


def test_release_reabre_conversa_ja_encerrada(api, api_bot, conversa: Conversation, analista, bot):
    """Comportamento atual, documentado: não há guarda de status no release, então
    encerrar de novo tira a conversa de `closed` e pede outra nota ao cliente.
    Ver relatório: item "release sem guarda de estado"."""
    api_bot.post(f"/api/v1/conversations/{conversa.id}/close")

    resposta = api.post(f"/api/v1/conversations/{conversa.id}/release", headers=auth(analista))

    assert resposta.json()["status"] == "awaiting_feedback"
    assert len(bot.enviados) == 1
