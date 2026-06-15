"""Populate the database with synthetic demo data so the dashboard has something to show.

Run inside the service-manager container:

    docker compose exec service-manager python -m app.seed_demo_data
    docker compose exec service-manager python -m app.seed_demo_data --reset  # wipe and reseed
"""

import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app import security
from app.database import create_db_and_tables, engine
from app.models.admin import AdminUser, Role
from app.models.entities import Conversation, Feedback, Message, MessageEvaluation, User

DEMO_PROTOCOL_PREFIX = "PROCON-DEMO-"
DEMO_PHONE_PREFIX = "551299900"
DEMO_ADMIN_DOMAIN = "demo.procon.sp.gov.br"
DAYS = 14
CONVERSATIONS_PER_DAY = (2, 4)

DEMO_ADMINS = [
    {"name": "Ana Souza", "email": f"ana.souza@{DEMO_ADMIN_DOMAIN}"},
    {"name": "Carlos Lima", "email": f"carlos.lima@{DEMO_ADMIN_DOMAIN}"},
]

NAMES = [
    "Maria Silva", "João Pereira", "Ana Souza", "Carlos Oliveira", "Juliana Santos",
    "Pedro Costa", "Fernanda Lima", "Lucas Almeida", "Patrícia Rocha", "Rafael Carvalho",
    "Camila Ferreira", "Bruno Martins", "Beatriz Gomes", "Thiago Ribeiro", "Larissa Nunes",
    "Eduardo Barros", "Vanessa Dias", "Marcelo Teixeira",
]

# (user message, bot answer)
TOPICS = [
    (
        "Comprei uma geladeira e ela veio com defeito de fábrica, a loja recusa a troca. O que eu faço?",
        "Você tem direito à troca, reparo ou devolução do valor em até 30 dias para produtos com defeito, conforme o Código de Defesa do Consumidor. Posso registrar uma reclamação contra a loja para você.",
    ),
    (
        "Minha operadora de celular está cobrando um serviço que eu nunca contratei.",
        "Cobranças por serviços não contratados podem ser contestadas, com direito a cancelamento e devolução em dobro do valor cobrado indevidamente. Vou abrir um registro para acompanhamento.",
    ),
    (
        "Quero cancelar meu plano de internet, mas estão me cobrando uma multa contratual muito alta.",
        "A multa por cancelamento deve ser proporcional ao tempo restante do contrato. Vou registrar sua solicitação para que possamos avaliar se o valor cobrado é abusivo.",
    ),
    (
        "Paguei por um curso online e a empresa não entrega o conteúdo prometido.",
        "Quando o serviço não é prestado conforme anunciado, você pode solicitar o cancelamento e o reembolso integral. Vou registrar sua reclamação.",
    ),
    (
        "Recebi uma cobrança duplicada no cartão de crédito referente a uma compra online.",
        "Cobranças duplicadas podem ser contestadas junto à administradora do cartão. Também vou registrar aqui para acompanharmos o seu caso.",
    ),
    (
        "A companhia aérea cancelou meu voo e não ofereceu nenhuma assistência.",
        "Em caso de cancelamento de voo, a empresa deve oferecer reacomodação, reembolso integral ou assistência. Vou abrir uma reclamação para apurar o ocorrido.",
    ),
    (
        "Comprei um produto pela internet e ele nunca chegou.",
        "Produtos não entregues dão direito ao cancelamento da compra e reembolso integral, incluindo o frete. Vou registrar sua reclamação contra a loja.",
    ),
    (
        "Meu plano de saúde negou a cobertura de um procedimento que está no rol da ANS.",
        "Negativas de cobertura para procedimentos previstos no rol da ANS podem ser contestadas. Vou registrar sua reclamação para análise do caso.",
    ),
    (
        "A loja não está cumprindo a garantia do produto que comprei há 3 meses.",
        "Produtos dentro do prazo de garantia têm direito a reparo gratuito. Caso a loja se recuse, vou formalizar uma reclamação para você.",
    ),
    (
        "Fiz um financiamento e o banco está cobrando juros diferentes do que foi combinado no contrato.",
        "Divergências entre o contrato assinado e os valores cobrados podem ser contestadas. Vou registrar sua reclamação para que o banco seja notificado.",
    ),
]

FOLLOW_UPS = [
    "Entendi, obrigado. E quanto tempo demora pra ter uma resposta?",
    "Certo, e que documentos eu preciso apresentar?",
    "Tudo bem, pode registrar então.",
    "Ok, muito obrigado pela ajuda!",
]

CLOSING_LINES = [
    "O prazo médio de resposta é de até 10 dias úteis. Seu protocolo já está registrado, qualquer novidade avisaremos por aqui.",
    "Você vai precisar de RG, CPF, comprovantes de pagamento e, se houver, o contrato ou mensagens com a empresa. Pode trazer ou enviar esses documentos quando puder.",
    "Perfeito, já está registrado. Se precisar de algo mais, estou por aqui!",
    "Disponha! Qualquer dúvida, pode falar comigo novamente.",
]

ESCALATION_MESSAGE = (
    "Desculpe, não consegui entender sua solicitação. Vou transferir você para um de nossos atendentes."
)

AGENT_GREETING = "Olá, sou um dos atendentes do Procon Jacareí. Vou continuar seu atendimento por aqui."

POSITIVE_COMMENTS = [
    None, None,
    "Atendimento rápido e claro, obrigado!",
    "Resolveu minha dúvida certinho.",
    "Muito bom, fui bem orientado.",
]

NEGATIVE_COMMENTS = [
    None, None,
    "Respostas um pouco genéricas.",
    "Demorou para entender minha pergunta.",
    "Esperava uma solução mais rápida.",
]


def _wipe_demo_data(session: Session) -> None:
    demo_conversations = session.exec(
        select(Conversation).where(Conversation.protocol.like(f"{DEMO_PROTOCOL_PREFIX}%"))
    ).all()
    conversation_ids = [c.id for c in demo_conversations]

    if conversation_ids:
        messages = session.exec(
            select(Message).where(Message.conversation_id.in_(conversation_ids))
        ).all()
        message_ids = [m.id for m in messages]

        if message_ids:
            for evaluation in session.exec(
                select(MessageEvaluation).where(MessageEvaluation.message_id.in_(message_ids))
            ).all():
                session.delete(evaluation)

        for feedback in session.exec(
            select(Feedback).where(Feedback.conversation_id.in_(conversation_ids))
        ).all():
            session.delete(feedback)

        for message in messages:
            session.delete(message)

        for conversation in demo_conversations:
            session.delete(conversation)

    for user in session.exec(select(User).where(User.phone.like(f"{DEMO_PHONE_PREFIX}%"))).all():
        session.delete(user)

    for admin in session.exec(
        select(AdminUser).where(AdminUser.email.like(f"%@{DEMO_ADMIN_DOMAIN}"))
    ).all():
        session.delete(admin)

    session.commit()


def _get_or_create_demo_admins(session: Session) -> list[AdminUser]:
    admins = []
    for spec in DEMO_ADMINS:
        admin = session.exec(select(AdminUser).where(AdminUser.email == spec["email"])).first()
        if not admin:
            admin = AdminUser(
                name=spec["name"],
                email=spec["email"],
                hashed_password=security.hash_password("demo12345"),
                role=Role.analista,
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)
        admins.append(admin)
    return admins


def _get_or_create_demo_users(session: Session, count: int = len(NAMES)) -> list[User]:
    users = []
    for i in range(count):
        phone = f"{DEMO_PHONE_PREFIX}{i:04d}"
        user = session.exec(select(User).where(User.phone == phone)).first()
        if not user:
            cpf = f"{random.randint(0, 99_999_999_999):011d}"
            user = User(name=NAMES[i % len(NAMES)], cpf=cpf, phone=phone)
            session.add(user)
            session.commit()
            session.refresh(user)
        users.append(user)
    return users


def _pick_status(is_last_day: bool) -> str:
    if is_last_day:
        weights = {
            "open": 0.30,
            "waiting_human": 0.15,
            "human_handover": 0.15,
            "closed": 0.30,
            "awaiting_feedback": 0.10,
        }
    else:
        weights = {
            "closed": 0.70,
            "archived": 0.15,
            "open": 0.10,
            "awaiting_feedback": 0.05,
        }
    return random.choices(list(weights.keys()), weights=list(weights.values()))[0]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def seed_demo_data(session: Session) -> None:
    admins = _get_or_create_demo_admins(session)
    users = _get_or_create_demo_users(session)

    now = datetime.now(timezone.utc)
    protocol_counter = 1

    for day_index in range(DAYS):
        day_progress = day_index / (DAYS - 1)  # 0 (oldest) -> 1 (today)
        is_last_day = day_index == DAYS - 1
        day_date = now - timedelta(days=DAYS - 1 - day_index)

        # Trend the synthetic ratings upward over time so the charts show improvement.
        rating_mean = 3.2 + day_progress * 1.5
        positive_eval_prob = 0.55 + day_progress * 0.35

        for _ in range(random.randint(*CONVERSATIONS_PER_DAY)):
            user = random.choice(users)
            user_msg, bot_msg = random.choice(TOPICS)
            status = _pick_status(is_last_day)

            base_time = day_date.replace(
                hour=random.randint(8, 19), minute=random.randint(0, 59), second=0, microsecond=0
            )

            conversation = Conversation(
                user_id=user.id,
                protocol=f"{DEMO_PROTOCOL_PREFIX}{protocol_counter:04d}",
                status=status,
                created_at=base_time,
                updated_at=base_time,
            )
            protocol_counter += 1

            if status == "human_handover":
                conversation.assigned_admin_id = random.choice(admins).id
            if status == "waiting_human":
                conversation.failed_attempts = 3

            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            timestamp = base_time
            messages: list[Message] = []
            messages.append(Message(conversation_id=conversation.id, role="user", content=user_msg, timestamp=timestamp))
            timestamp += timedelta(minutes=1)

            if status == "waiting_human":
                messages.append(
                    Message(conversation_id=conversation.id, role="bot", content=ESCALATION_MESSAGE, timestamp=timestamp)
                )
            else:
                messages.append(Message(conversation_id=conversation.id, role="bot", content=bot_msg, timestamp=timestamp))
                timestamp += timedelta(minutes=random.randint(1, 4))

                if random.random() < 0.6:
                    messages.append(
                        Message(conversation_id=conversation.id, role="user", content=random.choice(FOLLOW_UPS), timestamp=timestamp)
                    )
                    timestamp += timedelta(minutes=1)
                    messages.append(
                        Message(conversation_id=conversation.id, role="bot", content=random.choice(CLOSING_LINES), timestamp=timestamp)
                    )
                    timestamp += timedelta(minutes=random.randint(1, 5))

                if status == "human_handover":
                    messages.append(
                        Message(conversation_id=conversation.id, role="agent", content=AGENT_GREETING, timestamp=timestamp)
                    )
                    timestamp += timedelta(minutes=2)

            for message in messages:
                session.add(message)
            session.commit()
            for message in messages:
                session.refresh(message)

            conversation.updated_at = timestamp
            session.add(conversation)

            # Customer satisfaction feedback for finished conversations.
            if status in ("closed", "archived"):
                rating = _clamp(round(random.gauss(rating_mean, 0.8)), 1, 5)
                comment = random.choice(POSITIVE_COMMENTS if rating >= 4 else NEGATIVE_COMMENTS)
                session.add(
                    Feedback(
                        conversation_id=conversation.id,
                        rating=rating,
                        comment=comment,
                        is_best_answer=rating == 5,
                        created_at=timestamp,
                    )
                )

            # Admin evaluations of the bot's answers.
            for message in messages:
                if message.role != "bot":
                    continue
                if random.random() > 0.7:
                    continue
                rating = "positive" if random.random() < positive_eval_prob else "negative"
                session.add(
                    MessageEvaluation(
                        message_id=message.id,
                        admin_user_id=random.choice(admins).id,
                        rating=rating,
                        created_at=message.timestamp,
                    )
                )

            session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="Delete previously seeded demo data before reseeding"
    )
    args = parser.parse_args()

    create_db_and_tables()

    with Session(engine) as session:
        existing = session.exec(
            select(Conversation).where(Conversation.protocol.like(f"{DEMO_PROTOCOL_PREFIX}%"))
        ).first()

        if existing and not args.reset:
            print("Demo data already present. Use --reset to wipe and reseed.")
            return

        if existing:
            print("Removing previously seeded demo data...")
            _wipe_demo_data(session)

        print("Seeding synthetic demo data...")
        seed_demo_data(session)
        print("Done.")


if __name__ == "__main__":
    main()
