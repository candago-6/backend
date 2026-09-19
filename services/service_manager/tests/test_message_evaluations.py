"""Joinha/deslike que o analista dá em cada resposta do bot (acurácia da LLM)."""

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.models.entities import Message, MessageEvaluation
from conftest import auth


def test_avaliar_resposta_do_bot_como_positiva(api, mensagem_bot: Message, analista):
    resposta = api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": "positive"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["rating"] == "positive"
    assert corpo["message_id"] == mensagem_bot.id
    assert corpo["admin_user_id"] == analista.id


def test_trocar_a_avaliacao_atualiza_o_mesmo_registro(
    api, session: Session, mensagem_bot: Message, analista
):
    positiva = api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": "positive"},
        headers=auth(analista),
    )
    negativa = api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": "negative"},
        headers=auth(analista),
    )

    assert positiva.json()["id"] == negativa.json()["id"]
    assert negativa.json()["rating"] == "negative"
    assert len(session.exec(select(MessageEvaluation)).all()) == 1


def test_avaliacao_de_outro_analista_sobrescreve_e_registra_o_novo_autor(
    api, session: Session, mensagem_bot: Message, analista, outro_analista
):
    """Uma avaliação por mensagem (message_id é UNIQUE); a última vale."""
    api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": "positive"},
        headers=auth(analista),
    )
    segunda = api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": "negative"},
        headers=auth(outro_analista),
    )

    assert segunda.json()["admin_user_id"] == outro_analista.id
    assert len(session.exec(select(MessageEvaluation)).all()) == 1


@pytest.mark.parametrize("valor", ["otimo", "POSITIVE", "", "1", None])
def test_rating_fora_do_dominio_devolve_422(api, mensagem_bot: Message, analista, valor):
    resposta = api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": valor},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_avaliar_mensagem_inexistente_devolve_404(api, analista):
    resposta = api.put(
        "/api/v1/messages/999999/evaluation",
        json={"rating": "positive"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_404_NOT_FOUND
    assert resposta.json() == {"detail": "Message not found"}


def test_avaliar_exige_autenticacao(api, mensagem_bot: Message):
    resposta = api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation", json={"rating": "positive"}
    )

    assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_listar_avaliacoes_exige_autenticacao(api):
    assert api.get("/api/v1/message-evaluations").status_code == status.HTTP_401_UNAUTHORIZED


def test_listar_avaliacoes_com_token(api, mensagem_bot: Message, analista):
    api.put(
        f"/api/v1/messages/{mensagem_bot.id}/evaluation",
        json={"rating": "positive"},
        headers=auth(analista),
    )

    resposta = api.get("/api/v1/message-evaluations", headers=auth(analista))

    assert resposta.status_code == status.HTTP_200_OK
    assert [e["rating"] for e in resposta.json()] == ["positive"]


def test_mensagem_do_usuario_tambem_pode_ser_avaliada(
    api, session: Session, conversa, analista
):
    """Comportamento atual, documentado: a API não checa `role == "bot"`, então
    uma mensagem do cliente pode entrar na conta de acurácia da LLM se alguém
    chamar o endpoint direto. Ver relatório: item "avaliação sem filtro de papel"."""
    msg = Message(conversation_id=conversa.id, role="user", content="minha dúvida")
    session.add(msg)
    session.commit()
    session.refresh(msg)

    resposta = api.put(
        f"/api/v1/messages/{msg.id}/evaluation",
        json={"rating": "negative"},
        headers=auth(analista),
    )

    assert resposta.status_code == status.HTTP_200_OK
