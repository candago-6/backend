"""Smoke test ponta a ponta contra a stack no ar (Postgres real, containers de pé).

A suíte pytest roda em SQLite e cobre as regras. Este script cobre o que só
aparece com tudo ligado: migração das tabelas no Postgres, o seed do admin, CORS,
e a ligação service-manager -> whatsapp-bot.

    docker compose up -d
    python services/service_manager/tests/e2e_smoke.py
    python services/service_manager/tests/e2e_smoke.py --api http://localhost:8002 \
        --email admin@procon.sp.gov.br --senha admin123

Nada de real é enviado no WhatsApp: o consumidor de teste usa um número
inexistente, então o envio falha de propósito e o script espera esse 502.
O que ficar no banco é impresso no fim, com o SQL para remover.
"""

import argparse
import os
import sys
import time
from typing import Any

import httpx

VERDE, VERMELHO, AMARELO, CINZA, FIM = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"

falhas: list[str] = []
pulados: list[str] = []
protocolo_teste = f"PROCON-SMOKE-{int(time.time())}"
telefone_teste = f"5500{int(time.time()) % 10_000_000:07d}"
email_analista = f"smoke.{int(time.time())}@procon.sp.gov.br"


def checar(nome: str, condicao: bool, detalhe: str = "") -> bool:
    if condicao:
        print(f"  {VERDE}ok{FIM}   {nome}")
    else:
        print(f"  {VERMELHO}FALHA{FIM} {nome} {CINZA}{detalhe}{FIM}")
        falhas.append(nome)
    return condicao


def pular(nome: str, motivo: str) -> None:
    print(f"  {AMARELO}pula{FIM} {nome} {CINZA}{motivo}{FIM}")
    pulados.append(f"{nome} ({motivo})")


def secao(titulo: str) -> None:
    print(f"\n{titulo}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8002", help="URL do service-manager")
    parser.add_argument("--pln", default="http://localhost:8001", help="URL do pln-pipeline")
    parser.add_argument("--bot", default="http://localhost:8003", help="URL do whatsapp-bot")
    parser.add_argument("--email", default="admin@procon.sp.gov.br")
    parser.add_argument("--senha", default="admin123")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--bot-secret",
        default=os.environ.get("BOT_SECRET", "dev-bot-secret-change-me"),
        help="Segredo com que o whatsapp-bot autentica nas rotas de atendimento",
    )
    args = parser.parse_args()

    c = httpx.Client(base_url=args.api, timeout=args.timeout)
    # Como o whatsapp-bot: sem login, autenticando por X-Bot-Secret.
    b = httpx.Client(
        base_url=args.api,
        timeout=args.timeout,
        headers={"X-Bot-Secret": args.bot_secret},
    )

    # --- 1. serviços no ar ---------------------------------------------------
    secao("1. Health checks")
    try:
        r = c.get("/api/v1/health")
        checar("service-manager responde", r.status_code == 200 and r.json()["status"] == "ok")
    except httpx.HTTPError as e:
        print(f"  {VERMELHO}FALHA{FIM} service-manager inacessível em {args.api}: {e}")
        print("\n  Suba a stack antes:  docker compose up -d")
        return 1

    for nome, url in (("pln-pipeline", f"{args.pln}/api/health"), ("whatsapp-bot", f"{args.bot}/api/v1/health")):
        try:
            r = httpx.get(url, timeout=args.timeout)
            checar(f"{nome} responde", r.status_code == 200, r.text[:60])
        except httpx.HTTPError as e:
            pular(f"{nome} responde", f"inacessível: {type(e).__name__}")

    # --- 2. login ------------------------------------------------------------
    secao("2. Login e token")
    r = c.post("/auth/login", json={"email": args.email, "password": args.senha})
    if not checar("login do gestor", r.status_code == 200, f"{r.status_code} {r.text[:120]}"):
        print(f"\n  Confira ADMIN_EMAIL/ADMIN_PASSWORD do compose (--email/--senha).")
        return 1
    token_gestor = r.json()["token"]
    h_gestor = {"Authorization": f"Bearer {token_gestor}"}
    checar("perfil devolvido é gestor", r.json()["user"]["role"] == "gestor", str(r.json()["user"]))

    checar(
        "senha errada é recusada",
        c.post("/auth/login", json={"email": args.email, "password": "senha-errada"}).status_code == 401,
    )
    checar("rota protegida sem token é barrada", c.get("/api/v1/admin-users").status_code == 401)
    checar(
        "token inválido é barrado",
        c.get("/api/v1/admin-users", headers={"Authorization": "Bearer xxx"}).status_code == 401,
    )

    # --- 3. cadastro de analista --------------------------------------------
    secao("3. Cadastro de analista e permissões")
    novo = {"name": "Analista Smoke", "email": email_analista, "password": "SmokeTest123", "role": "analista"}
    r = c.post("/api/v1/admin-users", json=novo, headers=h_gestor)
    if not checar("gestor cadastra analista", r.status_code == 201, f"{r.status_code} {r.text[:120]}"):
        return 1
    id_analista = r.json()["id"]

    checar(
        "e-mail duplicado é recusado",
        c.post("/api/v1/admin-users", json=novo, headers=h_gestor).status_code == 409,
    )

    r = c.post("/auth/login", json={"email": novo["email"], "password": novo["password"]})
    checar("analista novo consegue entrar", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    h_analista = {"Authorization": f"Bearer {r.json()['token']}"} if r.status_code == 200 else {}

    if h_analista:
        checar(
            "analista é barrado na gestão de usuários (403: autenticado, sem permissão)",
            c.get("/api/v1/admin-users", headers=h_analista).status_code == 403,
        )
        checar(
            "analista acessa o painel de atendimento",
            c.get("/api/v1/message-evaluations", headers=h_analista).status_code == 200,
        )

    r = c.put(
        f"/api/v1/admin-users/{id_analista}",
        json={"name": "Analista Smoke II", "email": novo["email"], "role": "analista"},
        headers=h_gestor,
    )
    checar("gestor edita o analista", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    checar("listagem de usuários", c.get("/api/v1/admin-users", headers=h_gestor).status_code == 200)

    # --- 4. consumidor e conversa -------------------------------------------
    secao("4. Consumidor, conversa e mensagens (fluxo do bot)")
    r = b.post(
        "/api/v1/users",
        json={"name": "Consumidor Smoke", "cpf": "52998224725", "phone": telefone_teste,
              "whatsapp_id": f"{telefone_teste}@c.us"},
    )
    if not checar("cria consumidor", r.status_code == 200, f"{r.status_code} {r.text[:120]}"):
        return 1
    id_usuario = r.json()["id"]
    checar("CPF é gravado cifrado", r.json()["cpf"].startswith("gAAAA"), r.json()["cpf"][:20])

    checar("busca por telefone", b.get(f"/api/v1/users/phone/{telefone_teste}").status_code == 200)
    checar("busca por whatsapp-id", b.get(f"/api/v1/users/whatsapp-id/{telefone_teste}@c.us").status_code == 200)
    checar("busca por id", b.get(f"/api/v1/users/{id_usuario}").status_code == 200)
    checar("telefone inexistente devolve 404", b.get("/api/v1/users/phone/5599999999999").status_code == 404)
    checar("atualiza consumidor", b.put(f"/api/v1/users/{id_usuario}", json={"name": "Consumidor Smoke II"}).status_code == 200)
    checar("listagem de consumidores (painel)", c.get("/api/v1/users", headers=h_gestor).status_code == 200)

    r = b.post("/api/v1/conversations", json={"user_id": id_usuario, "protocol": protocolo_teste, "status": "open"})
    if not checar("abre conversa", r.status_code == 200, f"{r.status_code} {r.text[:120]}"):
        return 1
    id_conversa = r.json()["id"]

    checar("conversa ativa é encontrada", b.get(f"/api/v1/conversations/active/{id_usuario}").status_code == 200)
    checar("busca conversa por id", b.get(f"/api/v1/conversations/{id_conversa}").status_code == 200)
    checar("listagem de conversas", b.get("/api/v1/conversations").status_code == 200)
    checar("marca onboarding", b.post(f"/api/v1/conversations/{id_conversa}/mark-onboarded").json()["is_onboarded"] is True)

    r = b.post("/api/v1/messages", json={"conversation_id": id_conversa, "role": "user", "content": "pergunta do smoke test"})
    checar("grava mensagem do cliente", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    r = b.post("/api/v1/messages", json={"conversation_id": id_conversa, "role": "bot", "content": "resposta do smoke test"})
    checar("grava resposta do bot", r.status_code == 200)
    id_mensagem_bot = r.json()["id"] if r.status_code == 200 else None
    checar("listagem de mensagens (painel)", c.get("/api/v1/messages", headers=h_gestor).status_code == 200)

    # --- 5. máquina de estados ----------------------------------------------
    secao("5. Máquina de estados e escalada")
    for i, esperado in enumerate([(1, "open"), (2, "open"), (3, "waiting_human")], start=1):
        r = b.post(f"/api/v1/conversations/{id_conversa}/increment-failures")
        checar(
            f"falha {i} -> {esperado[1]}",
            (r.json()["failed_attempts"], r.json()["status"]) == esperado,
            str((r.json()["failed_attempts"], r.json()["status"])),
        )
    checar("marca mensagem de paciência", b.post(f"/api/v1/conversations/{id_conversa}/mark-patience-sent").json()["patience_msg_sent"] is True)
    checar("zera falhas", b.post(f"/api/v1/conversations/{id_conversa}/reset-failures").json()["failed_attempts"] == 0)
    for estado in ("open", "confirming_closure", "awaiting_feedback", "waiting_human"):
        r = b.post(f"/api/v1/conversations/{id_conversa}/update-status?status={estado}")
        checar(f"status -> {estado}", r.status_code == 200 and r.json()["status"] == estado)

    # --- 6. handover ---------------------------------------------------------
    secao("6. Atendimento humano (handover)")
    checar("takeover sem token é barrado", c.post(f"/api/v1/conversations/{id_conversa}/takeover").status_code == 401)
    r = c.post(f"/api/v1/conversations/{id_conversa}/takeover", headers=h_gestor)
    checar("gestor assume o atendimento", r.status_code == 200 and r.json()["status"] == "human_handover", f"{r.status_code} {r.text[:120]}")

    if h_analista:
        r = c.post(f"/api/v1/conversations/{id_conversa}/takeover", headers=h_analista)
        checar("outro usuário não rouba o atendimento", r.status_code == 409, f"{r.status_code} {r.text[:80]}")

    r = c.post(
        f"/api/v1/conversations/{id_conversa}/agent-message",
        json={"content": "mensagem do smoke test (número inexistente, não deve chegar a ninguém)"},
        headers=h_gestor,
    )
    if r.status_code == 502:
        checar("envio ao WhatsApp falha e NÃO grava mensagem fantasma", True, "502 esperado: número de teste não existe")
        r2 = c.get("/api/v1/messages", headers=h_gestor)
        agentes = [m for m in r2.json() if m["conversation_id"] == id_conversa and m["role"] == "agent"]
        checar("nenhuma mensagem de atendente gravada após a falha", agentes == [], f"{len(agentes)} gravada(s)")
    elif r.status_code == 200:
        checar("mensagem do atendente gravada", r.json()["role"] == "agent")
        print(f"  {AMARELO}nota{FIM} o bot aceitou o envio para {telefone_teste} {CINZA}(número de teste){FIM}")
    else:
        checar("agent-message", False, f"{r.status_code} {r.text[:120]}")

    r = c.post(f"/api/v1/conversations/{id_conversa}/release?to_bot=true", headers=h_gestor)
    checar("devolve ao bot", r.status_code == 200 and r.json()["status"] == "open", f"{r.status_code} {r.text[:80]}")

    # --- 7. avaliações -------------------------------------------------------
    secao("7. Avaliações")
    if id_mensagem_bot:
        checar(
            "avaliar resposta sem token é barrado",
            c.put(f"/api/v1/messages/{id_mensagem_bot}/evaluation", json={"rating": "positive"}).status_code == 401,
        )
        r = c.put(f"/api/v1/messages/{id_mensagem_bot}/evaluation", json={"rating": "positive"}, headers=h_gestor)
        checar("analista avalia resposta do bot", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        r = c.put(f"/api/v1/messages/{id_mensagem_bot}/evaluation", json={"rating": "negative"}, headers=h_gestor)
        checar("troca da avaliação atualiza o mesmo registro", r.status_code == 200 and r.json()["rating"] == "negative")
        checar(
            "rating fora do domínio é recusado",
            c.put(f"/api/v1/messages/{id_mensagem_bot}/evaluation", json={"rating": "otimo"}, headers=h_gestor).status_code == 422,
        )
    checar("listagem de avaliações", c.get("/api/v1/message-evaluations", headers=h_gestor).status_code == 200)

    r = b.post("/api/v1/feedback", json={"conversation_id": id_conversa, "rating": 5, "is_best_answer": True})
    checar("cliente avalia o atendimento", r.status_code == 200 and r.json()["rating"] == 5, f"{r.status_code} {r.text[:120]}")
    r = b.post("/api/v1/feedback", json={"conversation_id": id_conversa, "rating": 3})
    checar("reavaliação substitui a nota", r.status_code == 200 and r.json()["rating"] == 3)
    checar("nota de conversa inexistente devolve 404", b.post("/api/v1/feedback", json={"conversation_id": 99999999, "rating": 5}).status_code == 404)
    checar("listagem de notas (painel)", c.get("/api/v1/feedback", headers=h_gestor).status_code == 200)

    checar("encerra a conversa", b.post(f"/api/v1/conversations/{id_conversa}/close").json()["status"] == "closed")

    # --- 8. CORS -------------------------------------------------------------
    secao("8. CORS (origem do painel)")
    r = c.options(
        "/auth/login",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    checar(
        "preflight do painel é aceito",
        r.headers.get("access-control-allow-origin") == "http://localhost:3000",
        f"allow-origin={r.headers.get('access-control-allow-origin')!r} — em produção a origem do painel precisa entrar na lista",
    )

    # --- 9. superfície de autenticação (C1) ----------------------------------
    secao("9. Rotas fechadas (C1)")
    for metodo, rota in (
        ("GET", "/api/v1/users"),
        ("GET", "/api/v1/messages"),
        ("GET", "/api/v1/feedback"),
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/messages"),
    ):
        r = c.request(metodo, rota, json={})
        checar(f"{metodo} {rota} recusa sem credencial", r.status_code in (401, 403), f"respondeu {r.status_code}")

    for rota in ("/api/v1/users", "/api/v1/messages", "/api/v1/feedback"):
        r = b.get(rota)
        checar(
            f"segredo do bot NÃO abre GET {rota}",
            r.status_code in (401, 403),
            f"respondeu {r.status_code} — leitura em bloco deve ser só do painel",
        )

    r = b.get("/api/v1/conversations")
    checar("segredo do bot abre GET /api/v1/conversations (o Vigilante usa)", r.status_code == 200, f"{r.status_code}")

    r = c.request("GET", "/api/v1/conversations", headers={"X-Bot-Secret": "errado"})
    checar("segredo de bot errado é recusado", r.status_code in (401, 403), f"respondeu {r.status_code}")

    # --- 10. limpeza ---------------------------------------------------------
    secao("10. Limpeza")
    r = c.delete(f"/api/v1/admin-users/{id_analista}", headers=h_gestor)
    checar("remove o analista criado", r.status_code == 204, f"{r.status_code} {r.text[:80]}")

    print(
        f"\n{CINZA}A API não expõe remoção de conversa/consumidor. Para apagar o que ficou:\n"
        f"  docker compose exec db psql -U admin -d assistente_whatsapp -c \\\n"
        f"    \"DELETE FROM message_evaluations WHERE message_id IN (SELECT id FROM message WHERE conversation_id IN "
        f"(SELECT id FROM conversation WHERE protocol='{protocolo_teste}'));\"\n"
        f"  docker compose exec db psql -U admin -d assistente_whatsapp -c \\\n"
        f"    \"DELETE FROM feedback WHERE conversation_id IN (SELECT id FROM conversation WHERE protocol='{protocolo_teste}');\"\n"
        f"  docker compose exec db psql -U admin -d assistente_whatsapp -c \\\n"
        f"    \"DELETE FROM message WHERE conversation_id IN (SELECT id FROM conversation WHERE protocol='{protocolo_teste}');\"\n"
        f"  docker compose exec db psql -U admin -d assistente_whatsapp -c \\\n"
        f"    \"DELETE FROM conversation WHERE protocol='{protocolo_teste}'; DELETE FROM \\\"user\\\" WHERE phone='{telefone_teste}';\"{FIM}"
    )

    # --- resultado -----------------------------------------------------------
    print("\n" + "=" * 70)
    if falhas:
        print(f"{VERMELHO}{len(falhas)} verificação(ões) falharam:{FIM}")
        for f in falhas:
            print(f"  - {f}")
    else:
        print(f"{VERDE}Todas as verificações passaram.{FIM}")
    if pulados:
        print(f"{AMARELO}Puladas:{FIM} " + "; ".join(pulados))
    print("=" * 70)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
