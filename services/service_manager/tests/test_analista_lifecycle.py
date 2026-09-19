"""Fluxo ponta a ponta do cadastro de analista.

É o roteiro que o gestor executa no painel: entra, cadastra um analista, o
analista entra com a senha recebida, usa o painel e é barrado no que é de gestor.
"""

from fastapi import status
from sqlmodel import Session, select

from app import security
from app.models.admin import AdminUser, Role
from conftest import SENHA_PADRAO, auth

SENHA_NOVO_ANALISTA = "AnalistaNovo123"

NOVO_ANALISTA = {
    "name": "Bruno Lima",
    "email": "bruno.lima@procon.sp.gov.br",
    "password": SENHA_NOVO_ANALISTA,
    "role": "analista",
}


def _login(api, email: str, senha: str):
    return api.post("/auth/login", json={"email": email, "password": senha})


def test_gestor_cadastra_analista_que_depois_consegue_entrar(api, session: Session, gestor):
    # 1. gestor entra
    login_gestor = _login(api, gestor.email, SENHA_PADRAO)
    assert login_gestor.status_code == status.HTTP_200_OK
    h_gestor = {"Authorization": f"Bearer {login_gestor.json()['token']}"}

    # 2. gestor cadastra o analista
    criacao = api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=h_gestor)
    assert criacao.status_code == status.HTTP_201_CREATED
    criado = criacao.json()
    assert criado["role"] == "analista"
    assert criado["email"] == NOVO_ANALISTA["email"]
    assert "password" not in criado and "hashed_password" not in criado

    # 3. a senha foi para o banco com hash, nunca em claro
    persistido = session.exec(
        select(AdminUser).where(AdminUser.email == NOVO_ANALISTA["email"])
    ).one()
    assert persistido.hashed_password != SENHA_NOVO_ANALISTA
    assert security.verify_password(SENHA_NOVO_ANALISTA, persistido.hashed_password)

    # 4. o analista novo entra com a senha que recebeu
    login_analista = _login(api, NOVO_ANALISTA["email"], SENHA_NOVO_ANALISTA)
    assert login_analista.status_code == status.HTTP_200_OK
    assert login_analista.json()["user"]["id"] == criado["id"]
    assert login_analista.json()["user"]["role"] == "analista"

    # 5. o token do analista abre o painel de atendimento
    h_analista = {"Authorization": f"Bearer {login_analista.json()['token']}"}
    assert api.get("/api/v1/message-evaluations", headers=h_analista).status_code == 200

    # 6. mas não abre a gestão de usuários
    assert api.get("/api/v1/admin-users", headers=h_analista).status_code == 403


def test_analista_nao_cria_edita_nem_remove_usuarios(api, gestor, analista):
    h = auth(analista)
    alvo = api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=auth(gestor)).json()

    assert api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=h).status_code == 403
    assert api.get("/api/v1/admin-users", headers=h).status_code == 403
    assert (
        api.put(
            f"/api/v1/admin-users/{alvo['id']}",
            json={"name": "X", "email": "x@procon.sp.gov.br", "role": "gestor"},
            headers=h,
        ).status_code
        == 403
    )
    assert api.delete(f"/api/v1/admin-users/{alvo['id']}", headers=h).status_code == 403


def test_cadastro_de_usuario_exige_autenticacao(api):
    assert api.post("/api/v1/admin-users", json=NOVO_ANALISTA).status_code == 401
    assert api.get("/api/v1/admin-users").status_code == 401


def test_gestor_promove_analista_e_o_acesso_muda_no_proximo_token(api, session, gestor, analista):
    h_gestor = auth(gestor)
    assert api.get("/api/v1/admin-users", headers=auth(analista)).status_code == 403

    promocao = api.put(
        f"/api/v1/admin-users/{analista.id}",
        json={"name": analista.name, "email": analista.email, "role": "gestor"},
        headers=h_gestor,
    )
    assert promocao.status_code == status.HTTP_200_OK
    assert promocao.json()["role"] == "gestor"

    # O papel é lido do banco a cada request, então o token antigo já vale como gestor.
    assert api.get("/api/v1/admin-users", headers=auth(analista)).status_code == 200


def test_analista_removido_perde_o_acesso_imediatamente(api, gestor, analista):
    h_analista = auth(analista)
    assert api.get("/api/v1/message-evaluations", headers=h_analista).status_code == 200

    assert api.delete(f"/api/v1/admin-users/{analista.id}", headers=auth(gestor)).status_code == 204

    assert api.get("/api/v1/message-evaluations", headers=h_analista).status_code == 401


def test_senha_do_analista_nao_e_alterada_pelo_endpoint_de_edicao(api, session, gestor):
    """PUT /admin-users não tem campo de senha: editar o cadastro não invalida o login.

    Também é a evidência de que não existe caminho de troca/reset de senha na API
    (ver relatório: item "sem fluxo de senha").
    """
    criado = api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=auth(gestor)).json()

    api.put(
        f"/api/v1/admin-users/{criado['id']}",
        json={"name": "Bruno L.", "email": NOVO_ANALISTA["email"], "role": "analista"},
        headers=auth(gestor),
    )

    assert _login(api, NOVO_ANALISTA["email"], SENHA_NOVO_ANALISTA).status_code == 200


def test_email_duplicado_e_bloqueado_na_criacao_e_na_edicao(api, gestor):
    h = auth(gestor)
    api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=h)
    outro = api.post(
        "/api/v1/admin-users",
        json={**NOVO_ANALISTA, "email": "outro@procon.sp.gov.br"},
        headers=h,
    ).json()

    duplicado_na_criacao = api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=h)
    duplicado_na_edicao = api.put(
        f"/api/v1/admin-users/{outro['id']}",
        json={"name": "Outro", "email": NOVO_ANALISTA["email"], "role": "analista"},
        headers=h,
    )

    assert duplicado_na_criacao.status_code == status.HTTP_409_CONFLICT
    assert duplicado_na_edicao.status_code == status.HTTP_409_CONFLICT


def test_gestor_consegue_se_auto_remover_e_travar_a_gestao_de_usuarios(api, session, gestor):
    """Comportamento atual, documentado de propósito.

    Não há guarda de "último gestor": o único gestor pode se apagar e ninguém
    mais consegue cadastrar usuários (o seed só recria o admin se a tabela ficar
    totalmente vazia). Ver relatório: item "lockout do painel".
    """
    h = auth(gestor)
    api.post("/api/v1/admin-users", json=NOVO_ANALISTA, headers=h)

    assert api.delete(f"/api/v1/admin-users/{gestor.id}", headers=h).status_code == 204

    restantes = session.exec(select(AdminUser)).all()
    assert [u.role for u in restantes] == [Role.analista]
