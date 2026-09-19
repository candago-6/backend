"""Popula o banco com históricos de conversa mocados, prontos para teste manual.

Diferente do `seed_demo_data.py` (que sorteia volume para as métricas), aqui os
diálogos são roteiros fixos, escritos para serem lidos: cada um exercita um
caminho do produto e termina num status diferente, de forma que o painel mostre
todos os estados de uma vez.

    docker compose exec service-manager python -m app.seed_mock_conversations
    docker compose exec service-manager python -m app.seed_mock_conversations --reset
    docker compose exec service-manager python -m app.seed_mock_conversations --repeat 3
    docker compose exec service-manager python -m app.seed_mock_conversations --phone 5512991234567

Tudo que este script cria usa o prefixo PROCON-MOCK- (protocolos), 5512900XX
(telefones) e @mock.procon.sp.gov.br (logins), então `--reset` remove só o que é
dele e não encosta em dado real nem no que o seed_demo_data gerou.

Sobre o Vigilante: as conversas ativas nascem com `updated_at` de mais de 12h
atrás justamente para ficarem fora da janela do monitor do bot (index.js:185).
Sem isso o bot tentaria mandar "seu atendimento acabou?" para números que não
existem, e um erro de envio derruba a varredura inteira daquele ciclo.
"""

import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app import security
from app.database import create_db_and_tables, engine
from app.models.admin import AdminUser, Role
from app.models.entities import Conversation, Feedback, Message, MessageEvaluation, User
from app.utils.security import encrypt_data

PREFIXO_PROTOCOLO = "PROCON-MOCK-"
PREFIXO_TELEFONE = "551290012"
DOMINIO_LOGIN = "mock.procon.sp.gov.br"
SENHA_LOGIN = "mock12345"
NOME_TESTE_AO_VIVO = "Teste de Fogo"

# Fora da janela de 12h do Vigilante (index.js: `if (diff > 720) continue`).
HORAS_DE_SILENCIO = 30

LOGINS = [
    {"name": "Gestora Mock", "email": f"gestora@{DOMINIO_LOGIN}", "role": Role.gestor},
    {"name": "Analista Mock", "email": f"analista@{DOMINIO_LOGIN}", "role": Role.analista},
]

CONSUMIDORES = [
    ("Maria Aparecida Souza", "52998224725"),
    ("João Batista Pereira", "11144477735"),
    ("Cláudia Regina Alves", "39053344705"),
    ("Roberto Carlos Nunes", "16899535009"),
    ("Fernanda Lima Rocha", "64592624476"),
    ("Anderson Silva Matos", "22233344405"),
]

ONBOARDING = [
    ("bot", "Olá! Sou o assistente virtual do Procon Jacareí. Antes de continuar, preciso confirmar seus dados.\n\nQual o seu nome completo?"),
    ("user", "{nome}"),
    ("bot", "Obrigado! Agora informe seu CPF (apenas números)."),
    ("user", "{cpf}"),
    ("bot", "Confirma seus dados?\n\n*Nome:* {nome}\n*CPF:* {cpf}\n\nResponda *Sim* para confirmar ou *Não* para corrigir."),
    ("user", "Sim"),
    ("bot", "Dados confirmados! Seu protocolo é: {protocolo}\n\nComo posso ajudar?"),
]

VERIFICACAO = "\n\n---\n_Resolvi o seu problema?_ Responda *Sim* ou *Não*."


# Cada roteiro: (identificador, status final, nota, mensagens após o onboarding).
ROTEIROS = [
    (
        "produto-com-defeito",
        "closed",
        5,
        [
            ("user", "Comprei uma geladeira e ela veio com defeito de fábrica, a loja recusa a troca. O que eu faço?"),
            ("bot", "Produtos com vício de fabricação dão direito a reparo, troca ou devolução do valor pago. O fornecedor tem 30 dias para sanar o defeito; passado esse prazo, a escolha entre as três opções é sua (art. 18 do CDC). Guarde a nota fiscal e o protocolo de atendimento da loja."),
            ("user", "Sim"),
            ("bot", "Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento."),
            ("user", "5"),
            ("bot", "Muito obrigado! Atendimento encerrado."),
        ],
    ),
    (
        "cobranca-indevida",
        "closed",
        4,
        [
            ("user", "Minha operadora de celular está cobrando um serviço que eu nunca contratei"),
            ("bot", "Cobrança por serviço não solicitado é prática abusiva. Você pode exigir o cancelamento imediato e a devolução em dobro do valor pago indevidamente, corrigido (art. 42 do CDC). Registre o protocolo de reclamação junto à operadora antes de abrir o processo no Procon."),
            ("user", "e se eles não devolverem?"),
            ("bot", "Se a operadora não resolver em até 10 dias úteis, você pode abrir reclamação formal no Procon Jacareí levando a fatura, o protocolo de atendimento e um documento com foto."),
            ("user", "Sim"),
            ("bot", "Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento."),
            ("user", "4"),
            ("bot", "Muito obrigado! Atendimento encerrado."),
        ],
    ),
    (
        "contingencia-rag",
        "closed",
        4,
        [
            ("user", "vocês atendem caso de consórcio de imóvel?"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("user", "quero saber sobre consórcio"),
            ("bot", "*[IA Avançada - Contingência]* Consórcios são fiscalizados pelo Banco Central, mas a relação de consumo com a administradora pode ser tratada pelo Procon. Traga o contrato e os comprovantes de pagamento ao atendimento presencial."),
            ("user", "Sim"),
            ("bot", "Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento."),
            ("user", "4"),
            ("bot", "Muito obrigado! Atendimento encerrado."),
        ],
    ),
    (
        "escalado-e-resolvido-pelo-atendente",
        "closed",
        2,
        [
            ("user", "minha encomenda sumiu e a loja diz que a culpa é da transportadora"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("user", "a loja empurra pra transportadora e a transportadora empurra pra loja"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("user", "ninguém resolve nada, o que eu faço??"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("bot", "Estou te transferindo para um atendente humano. Aguarde."),
            ("agent", "Olá, aqui é do Procon Jacareí. A responsabilidade pela entrega é do vendedor, não sua — a transportadora foi contratada por ele."),
            ("user", "então eles não podem me obrigar a resolver com a transportadora?"),
            ("agent", "Não podem. Abra a reclamação contra a loja com o número do pedido e o comprovante de pagamento que damos andamento."),
            ("user", "perfeito, obrigado"),
            ("system", "Seu atendimento foi encerrado pelo nosso atendente. Para finalizar, avalie o atendimento com uma nota de *1 a 5*."),
            ("user", "2"),
            ("bot", "Muito obrigado! Atendimento encerrado."),
        ],
    ),
    (
        "na-fila-do-atendente",
        "waiting_human",
        None,
        [
            ("user", "quero reclamar de uma clínica odontológica que fechou e não devolveu meu dinheiro"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("user", "a clínica fechou as portas e eu já tinha pagado o tratamento todo"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("user", "pagamento foi parcelado no cartão, ainda tá caindo a fatura"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("bot", "Estou te transferindo para um atendente humano. Aguarde."),
            ("bot", "Nossa fila está um pouco cheia no momento, agradecemos sua paciência! Logo um atendente falará com você."),
        ],
    ),
    (
        "atendente-em-linha",
        "human_handover",
        None,
        [
            ("user", "o banco negativou meu nome por uma dívida que eu já paguei"),
            ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
            ("user", "tenho o comprovante de quitação de dois meses atrás"),
            ("agent", "Boa tarde! Sou analista do Procon Jacareí e vou assumir daqui. Você chegou a enviar o comprovante ao banco?"),
            ("user", "enviei por e-mail e ninguém respondeu"),
            ("agent", "Certo. Me envie o número do protocolo do e-mail e o comprovante de quitação que eu já registro a reclamação."),
        ],
    ),
    (
        "conversa-em-andamento",
        "confirming_closure",
        None,
        [
            ("user", "qual o prazo para desistir de uma compra feita pela internet?"),
            ("bot", "O direito de arrependimento é de 7 dias corridos contados do recebimento do produto ou da assinatura do contrato, para compras feitas fora do estabelecimento (art. 49 do CDC). O valor pago deve ser devolvido integralmente, incluindo o frete."),
        ],
    ),
    (
        "aguardando-nota",
        "awaiting_feedback",
        None,
        [
            ("user", "posso trocar um presente que ganhei sem a nota fiscal?"),
            ("bot", "A troca por arrependimento em loja física depende da política do estabelecimento — não é obrigação legal. Já a troca por defeito é direito seu e pode ser comprovada por qualquer documento que ligue o produto à loja, como o comprovante de cartão."),
            ("user", "Sim"),
            ("bot", "Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento."),
        ],
    ),
    (
        "historico-antigo",
        "archived",
        3,
        [
            ("user", "recebi uma cobrança de anuidade que o banco disse que era isenta"),
            ("bot", "Se houve promessa de isenção, ela integra o contrato e deve ser cumprida (art. 30 do CDC). Guarde o print ou a gravação em que a isenção foi oferecida e exija o estorno."),
            ("user", "Sim"),
            ("bot", "Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento."),
            ("user", "3"),
            ("bot", "Muito obrigado! Atendimento encerrado."),
        ],
    ),
]

# Conversa opcional criada para um número real (--phone), para o teste ao vivo.
ROTEIRO_AO_VIVO = [
    ("user", "procon, comprei um celular que veio com a tela trincada"),
    ("bot", "Produtos com vício aparente devem ser reclamados em até 90 dias para bens duráveis (art. 26 do CDC). O fornecedor tem 30 dias para reparar ou substituir."),
    ("user", "a loja disse que eu que trinquei e não quer trocar"),
    ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
    ("user", "eles se recusam a fazer laudo técnico"),
    ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
    ("user", "quero falar com uma pessoa de verdade"),
    ("bot", "Desculpe, não encontrei uma resposta. Tente reformular sua pergunta."),
    ("bot", "Estou te transferindo para um atendente humano. Aguarde."),
]


def validar_telefone(digitos: str) -> str | None:
    """Devolve a mensagem de erro, ou None se o numero tem cara de WhatsApp BR.

    Formato esperado: DDI 55 + DDD (2) + numero (8 ou 9) = 12 ou 13 digitos.
    Errar isso nao falha na hora: o registro entra no banco, o painel deixa
    assumir o atendimento e so o envio quebra, com 502 e sem explicacao visivel.
    """
    if not digitos:
        return "Telefone vazio."
    if not digitos.startswith("55"):
        return (
            f"'{digitos}' nao comeca com o DDI 55. "
            "Use o numero completo, ex.: 5512999998888."
        )
    if len(digitos) not in (12, 13):
        return (
            f"'{digitos}' tem {len(digitos)} digitos; o esperado e 12 ou 13 "
            "(55 + DDD + 8 ou 9 digitos). Faltou o DDD?"
        )
    return None


# --- limpeza -----------------------------------------------------------------


def limpar_dados_mock(session: Session) -> int:
    conversas = session.exec(
        select(Conversation).where(Conversation.protocol.like(f"{PREFIXO_PROTOCOLO}%"))
    ).all()
    ids = [c.id for c in conversas]

    if ids:
        mensagens = session.exec(select(Message).where(Message.conversation_id.in_(ids))).all()
        ids_msg = [m.id for m in mensagens]
        if ids_msg:
            for avaliacao in session.exec(
                select(MessageEvaluation).where(MessageEvaluation.message_id.in_(ids_msg))
            ).all():
                session.delete(avaliacao)
        for nota in session.exec(select(Feedback).where(Feedback.conversation_id.in_(ids))).all():
            session.delete(nota)
        for mensagem in mensagens:
            session.delete(mensagem)
        for conversa in conversas:
            session.delete(conversa)

    for usuario in session.exec(
        select(User).where(User.phone.like(f"{PREFIXO_TELEFONE}%"))
    ).all():
        session.delete(usuario)

    # O usuario de --phone nao tem o prefixo de telefone mocado (o numero e real),
    # entao precisa de uma regra propria: so sai se este script o criou (nome
    # NOME_TESTE_AO_VIVO) e se nao sobrou conversa nenhuma nele. Assim um numero
    # que depois conversou de verdade com o bot, ou que ja existia com outro nome,
    # nunca e removido.
    for usuario in session.exec(
        select(User).where(User.name == NOME_TESTE_AO_VIVO)
    ).all():
        restantes = session.exec(
            select(Conversation).where(Conversation.user_id == usuario.id)
        ).first()
        if not restantes:
            session.delete(usuario)

    for admin in session.exec(
        select(AdminUser).where(AdminUser.email.like(f"%@{DOMINIO_LOGIN}"))
    ).all():
        session.delete(admin)

    session.commit()
    return len(conversas)


# --- criação -----------------------------------------------------------------


def obter_logins(session: Session) -> dict[str, AdminUser]:
    criados: dict[str, AdminUser] = {}
    for spec in LOGINS:
        admin = session.exec(select(AdminUser).where(AdminUser.email == spec["email"])).first()
        if not admin:
            admin = AdminUser(
                name=spec["name"],
                email=spec["email"],
                hashed_password=security.hash_password(SENHA_LOGIN),
                role=spec["role"],
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)
        criados[spec["role"].value] = admin
    return criados


def obter_consumidores(session: Session) -> list[User]:
    usuarios = []
    for indice, (nome, cpf) in enumerate(CONSUMIDORES):
        telefone = f"{PREFIXO_TELEFONE}{indice:03d}"
        usuario = session.exec(select(User).where(User.phone == telefone)).first()
        if not usuario:
            # encrypt_data explícito: o validator do modelo não roda em table=True.
            usuario = User(
                name=nome,
                cpf=encrypt_data(cpf),
                phone=telefone,
                whatsapp_id=f"{telefone}@c.us",
            )
            session.add(usuario)
            session.commit()
            session.refresh(usuario)
        usuarios.append(usuario)
    return usuarios


def _formatar(texto: str, **campos: str) -> str:
    return texto.format(**campos)


def criar_conversa(
    session: Session,
    *,
    usuario: User,
    protocolo: str,
    status: str,
    roteiro: list[tuple[str, str]],
    nota: int | None,
    analista: AdminUser | None,
    inicio: datetime,
    com_onboarding: bool = True,
    avaliador: AdminUser | None = None,
    prob_avaliacao: float = 0.7,
    rng: random.Random | None = None,
) -> Conversation:
    rng = rng or random
    conversa = Conversation(
        user_id=usuario.id,
        protocol=protocolo,
        status=status,
        is_onboarded=True,
        created_at=inicio,
        updated_at=inicio,
    )
    if status == "human_handover" and analista:
        conversa.assigned_admin_id = analista.id
    if status == "waiting_human":
        conversa.failed_attempts = 3
        conversa.patience_msg_sent = True
    session.add(conversa)
    session.commit()
    session.refresh(conversa)

    campos = {
        "nome": usuario.name,
        "cpf": "***.***.***-**",
        "protocolo": protocolo,
    }

    falas = (ONBOARDING if com_onboarding else []) + roteiro
    instante = inicio
    ultima_bot: Message | None = None

    for indice, (papel, texto) in enumerate(falas):
        instante = instante + timedelta(seconds=rng.randint(25, 160))
        conteudo = _formatar(texto, **campos)
        # A pergunta de verificação vai anexada só no envio; o banco guarda a
        # resposta limpa (index.js:416). Aqui o mock reproduz isso.
        mensagem = Message(
            conversation_id=conversa.id,
            role=papel,
            content=conteudo,
            timestamp=instante,
        )
        session.add(mensagem)
        session.commit()
        session.refresh(mensagem)
        if papel == "bot" and indice >= len(ONBOARDING if com_onboarding else []):
            ultima_bot = mensagem
            if avaliador and rng.random() < prob_avaliacao:
                negativa = "não encontrei uma resposta" in conteudo
                session.add(
                    MessageEvaluation(
                        message_id=mensagem.id,
                        admin_user_id=avaliador.id,
                        rating="negative" if negativa else "positive",
                        created_at=instante + timedelta(minutes=5),
                    )
                )

    if nota is not None:
        session.add(
            Feedback(
                conversation_id=conversa.id,
                rating=nota,
                comment=None,
                is_best_answer=nota >= 4,
                created_at=instante + timedelta(minutes=1),
            )
        )

    conversa.updated_at = instante
    session.add(conversa)
    session.commit()
    session.refresh(conversa)
    _ = ultima_bot
    return conversa


def semear(session: Session, repeticoes: int, telefone_real: str | None, semente: int) -> None:
    rng = random.Random(semente)
    logins = obter_logins(session)
    analista = logins["analista"]
    consumidores = obter_consumidores(session)

    agora = datetime.now(timezone.utc)
    contador = 1
    resumo: dict[str, int] = {}

    for rodada in range(repeticoes):
        for indice, (identificador, status, nota, roteiro) in enumerate(ROTEIROS):
            usuario = consumidores[(indice + rodada) % len(consumidores)]
            protocolo = f"{PREFIXO_PROTOCOLO}{contador:04d}"
            contador += 1

            if status in {"closed", "archived"}:
                # Espalha o histórico pelos últimos dias para o gráfico ter curva.
                inicio = agora - timedelta(
                    days=rng.randint(1, 13), hours=rng.randint(0, 10)
                )
            else:
                # Conversa ativa: fica fora da janela do Vigilante de propósito.
                inicio = agora - timedelta(
                    hours=HORAS_DE_SILENCIO + rng.randint(0, 20), minutes=rng.randint(0, 59)
                )

            criar_conversa(
                session,
                usuario=usuario,
                protocolo=protocolo,
                status=status,
                roteiro=roteiro,
                nota=nota,
                analista=analista,
                inicio=inicio,
                avaliador=analista,
                rng=rng,
            )
            resumo[status] = resumo.get(status, 0) + 1
            _ = identificador

    if telefone_real:
        digitos = "".join(ch for ch in telefone_real if ch.isdigit())
        aviso = validar_telefone(digitos)
        if aviso:
            print(f"\n! {aviso}")
            print("  Nenhuma conversa ao vivo foi criada. Confira o numero e rode de novo.")
            return
        usuario = session.exec(select(User).where(User.phone == digitos)).first()
        if not usuario:
            usuario = User(
                name=NOME_TESTE_AO_VIVO,
                cpf=encrypt_data("52998224725"),
                phone=digitos,
                whatsapp_id=f"{digitos}@c.us",
            )
            session.add(usuario)
            session.commit()
            session.refresh(usuario)

        ativa = session.exec(
            select(Conversation).where(
                Conversation.user_id == usuario.id,
                Conversation.status.in_(
                    ["open", "waiting_human", "human_handover", "confirming_closure", "awaiting_feedback"]
                ),
            )
        ).first()
        if ativa:
            print(
                f"! {digitos} já tem a conversa ativa {ativa.protocol} ({ativa.status}); "
                "nada criado para não atrapalhar o fluxo em andamento."
            )
        else:
            conversa = criar_conversa(
                session,
                usuario=usuario,
                protocolo=f"{PREFIXO_PROTOCOLO}VIVO-{contador:04d}",
                status="waiting_human",
                roteiro=ROTEIRO_AO_VIVO,
                nota=None,
                analista=None,
                inicio=agora - timedelta(minutes=12),
                avaliador=analista,
                rng=rng,
            )
            resumo["waiting_human"] = resumo.get("waiting_human", 0) + 1
            print(
                f"\n>> Conversa ao vivo criada para {digitos}: {conversa.protocol}\n"
                "   Ela entra na fila como 'Aguardando atendente'. No painel, abra\n"
                "   Conversas > Atender, clique em 'Assumir atendimento' e responda:\n"
                "   a mensagem sai de verdade no WhatsApp desse número."
            )

    print("\nConversas criadas por status:")
    for status, quantidade in sorted(resumo.items()):
        print(f"  {status:<20} {quantidade}")
    print(f"\nLogins para o painel (senha: {SENHA_LOGIN}):")
    for spec in LOGINS:
        print(f"  {spec['role'].value:<10} {spec['email']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset", action="store_true", help="Remove o que este script criou antes de semear")
    parser.add_argument("--repeat", type=int, default=1, help="Quantas vezes repetir o conjunto de roteiros")
    parser.add_argument("--seed", type=int, default=20260918, help="Semente do gerador, para reprodutibilidade")
    parser.add_argument(
        "--phone",
        help="Número real (só dígitos, com DDI) que receberá uma conversa na fila para o teste ao vivo",
    )
    parser.add_argument("--only-reset", action="store_true", help="Apenas limpa e sai")
    args = parser.parse_args()

    create_db_and_tables()

    with Session(engine) as session:
        if args.reset or args.only_reset:
            removidas = limpar_dados_mock(session)
            print(f"Removidas {removidas} conversas mocadas anteriores.")
            if args.only_reset:
                return

        existente = session.exec(
            select(Conversation).where(Conversation.protocol.like(f"{PREFIXO_PROTOCOLO}%"))
        ).first()
        if existente and not args.reset:
            print("Já existem conversas mocadas. Use --reset para recriar.")
            return

        print("Inserindo históricos de conversa mocados...")
        semear(session, max(1, args.repeat), args.phone, args.seed)
        print("\nPronto.")


if __name__ == "__main__":
    main()
