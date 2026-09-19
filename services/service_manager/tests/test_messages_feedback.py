"""Histórico de mensagens e a nota de 1 a 5 que o cliente dá no fim."""

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.models.entities import Conversation, Feedback, Message
from conftest import auth

PAPEIS = ["user", "bot", "agent", "system"]


@pytest.mark.parametrize("papel", PAPEIS)
def test_gravar_mensagem_de_cada_papel(api_bot, conversa: Conversation, papel: str):
    resposta = api_bot.post(
        "/api/v1/messages",
        json={"conversation_id": conversa.id, "role": papel, "content": f"texto {papel}"},
    )

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["role"] == papel
    assert corpo["timestamp"]


def test_gravar_mensagem_atualiza_a_atividade_da_conversa(api_bot, session: Session, conversa: Conversation
):
    """`updated_at` é o relógio do Vigilante: é dele que sai o silêncio de 5 min."""
    antes = api_bot.get(f"/api/v1/conversations/{conversa.id}").json()["updated_at"]

    api_bot.post(
        "/api/v1/messages",
        json={"conversation_id": conversa.id, "role": "user", "content": "oi"},
    )

    depois = api_bot.get(f"/api/v1/conversations/{conversa.id}").json()["updated_at"]
    assert depois > antes


def test_listar_mensagens(api_bot, analista, conversa: Conversation):
    """A listagem completa é do painel: exige token de admin, não o segredo do bot."""
    api_bot.post("/api/v1/messages", json={"conversation_id": conversa.id, "role": "user", "content": "1"})
    api_bot.post("/api/v1/messages", json={"conversation_id": conversa.id, "role": "bot", "content": "2"})

    resposta = api_bot.get("/api/v1/messages", headers=auth(analista))

    assert resposta.status_code == status.HTTP_200_OK
    assert [m["content"] for m in resposta.json()] == ["1", "2"]


def test_mensagem_em_conversa_inexistente_e_aceita_sem_erro(api_bot, session: Session):
    """Comportamento atual, documentado: não há checagem de FK no endpoint e o
    SQLite/Postgres só reclama na hora do flush se a FK estiver ativa.
    Ver relatório: item "endpoints sem validação de vínculo"."""
    resposta = api_bot.post(
        "/api/v1/messages",
        json={"conversation_id": 999999, "role": "user", "content": "órfã"},
    )

    assert resposta.status_code == status.HTTP_200_OK


# --- Avaliação do atendimento (nota 1 a 5) ----------------------------------


def test_cliente_avalia_o_atendimento(api_bot, conversa: Conversation):
    resposta = api_bot.post(
        "/api/v1/feedback",
        json={"conversation_id": conversa.id, "rating": 5, "is_best_answer": True},
    )

    assert resposta.status_code == status.HTTP_200_OK
    corpo = resposta.json()
    assert corpo["rating"] == 5
    assert corpo["is_best_answer"] is True
    assert corpo["comment"] is None


def test_segunda_avaliacao_substitui_a_primeira(api_bot, session: Session, conversa: Conversation):
    """Uma nota por protocolo: reavaliar atualiza em vez de duplicar."""
    primeira = api_bot.post("/api/v1/feedback", json={"conversation_id": conversa.id, "rating": 2})
    segunda = api_bot.post(
        "/api/v1/feedback",
        json={"conversation_id": conversa.id, "rating": 5, "comment": "resolveram depois"},
    )

    assert primeira.json()["id"] == segunda.json()["id"]
    assert segunda.json()["rating"] == 5
    assert segunda.json()["comment"] == "resolveram depois"
    assert len(session.exec(select(Feedback)).all()) == 1


def test_avaliacao_de_conversa_inexistente_devolve_404(api_bot):
    resposta = api_bot.post("/api/v1/feedback", json={"conversation_id": 999999, "rating": 5})

    assert resposta.status_code == status.HTTP_404_NOT_FOUND
    assert resposta.json() == {"detail": "Conversation not found"}


def test_listar_avaliacoes(api_bot, analista, conversa: Conversation):
    """Idem: quem lê as notas em bloco é o dashboard."""
    api_bot.post("/api/v1/feedback", json={"conversation_id": conversa.id, "rating": 4})

    resposta = api_bot.get("/api/v1/feedback", headers=auth(analista))

    assert resposta.status_code == status.HTTP_200_OK
    assert [f["rating"] for f in resposta.json()] == [4]


@pytest.mark.parametrize("nota", [1, 2, 3, 4, 5])
def test_todas_as_notas_do_intervalo_sao_aceitas(api_bot, conversa: Conversation, nota: int):
    resposta = api_bot.post("/api/v1/feedback", json={"conversation_id": conversa.id, "rating": nota})

    assert resposta.status_code == status.HTTP_200_OK
    assert resposta.json()["rating"] == nota


@pytest.mark.parametrize("nota", [0, -3, 6, 99])
@pytest.mark.xfail(
    reason=(
        "BUG: Feedback é modelo de tabela (table=True), então o ge=1/le=5 do Field "
        "não é aplicado na entrada e nota fora do intervalo entra no banco, "
        "distorcendo o % de satisfação do dashboard. "
        "Ver relatório: 'modelos de tabela como schema de entrada'."
    ),
)
def test_nota_fora_do_intervalo_deveria_ser_rejeitada(api_bot, conversa: Conversation, nota: int):
    resposta = api_bot.post("/api/v1/feedback", json={"conversation_id": conversa.id, "rating": nota})

    assert resposta.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
