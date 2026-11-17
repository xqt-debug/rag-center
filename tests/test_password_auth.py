import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from rag_center.app import create_app
from rag_center.auth import Auth, hash_password, verify_password
from rag_center.errors import DomainError
from rag_center.models import Base, LoginSession, User


def test_registration_password_hash_and_repeated_login_preserve_data(system):
    service, _, _, _, settings = system
    auth = Auth(service.db, settings)
    token, first = auth.register("tester", "test-password", "127.0.0.1")
    service.run(auth.authenticate(session_token=token), "create_knowledge_base", {"name": "private"})
    auth.logout(token)
    with pytest.raises(DomainError):
        auth.authenticate(session_token=token)
    token, second = auth.login("tester", "test-password", "127.0.0.1")
    assert first["id"] == second["id"]
    assert service.run(auth.authenticate(session_token=token), "list_knowledge_bases")["total"] == 1
    with service.db.sessions() as session:
        user = session.get(User, first["id"])
        assert user.password_hash != "test-password"
        assert verify_password("test-password", user.password_hash)
    assert "password" not in str(second)
    assert hash_password("test-password") != hash_password("test-password")


def test_unknown_and_wrong_password_never_create_accounts(system):
    service, _, _, _, settings = system
    auth = Auth(service.db, settings)
    auth.register("tester", "test-password", "127.0.0.1")
    with service.db.sessions() as session:
        before = session.scalar(select(func.count()).select_from(LoginSession))
    for username, password in [("unknown", "test-password"), ("tester", "wrong")]:
        with pytest.raises(DomainError) as error:
            auth.login(username, password, "127.0.0.1")
        assert error.value.code == "invalid_credentials"
    with service.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.username == "unknown")) == 0
        assert session.scalar(select(func.count()).select_from(LoginSession)) == before


def test_duplicate_registration_cannot_replace_password(system):
    service, _, _, _, settings = system
    auth = Auth(service.db, settings)
    _, user = auth.register("tester", "original-password", "127.0.0.1")
    with pytest.raises(DomainError) as error:
        auth.register("tester", "replacement-password", "127.0.0.1")
    assert error.value.code == "username_taken"
    assert auth.login("tester", "original-password", "127.0.0.1")[1]["id"] == user["id"]


def test_http_registration_login_cookie_csrf_and_removed_routes(system):
    _, _, _, _, settings = system
    with TestClient(create_app(settings)) as client:
        paths = {getattr(route, "path", None) for route in client.app.routes}
        assert "/api/auth/code" not in paths and "/api/auth/dev-login" not in paths
        assert client.get("/api/health").json()["login_mode"] == "password"
        for body in [
            {"username": "bad name", "password": "long-password"},
            {"username": "ok", "password": "short"},
            {"username": "ok", "password": "long-password", "phone": "13800000003"},
        ]:
            assert client.post("/api/auth/register", json=body).status_code == 422
        body = {"username": "tester", "password": "test-password"}
        result = client.post("/api/auth/register", json=body)
        assert result.status_code == 201
        assert "HttpOnly" in result.headers["set-cookie"]
        assert "SameSite=strict" in result.headers["set-cookie"]
        first = result.json()
        assert client.post("/api/auth/register", json=body).status_code == 409
        assert client.get("/api/auth/me").json()["id"] == first["id"]
        assert client.post("/api/actions/create_knowledge_base", json={"name": "CSRF"}).status_code == 403
        headers = {"X-CSRF-Token": first["csrf"]}
        assert (
            client.post(
                "/api/actions/create_knowledge_base", json={"name": "CSRF"}, headers=headers
            ).status_code
            == 200
        )
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        failed = client.post("/api/auth/login", json={**body, "password": "wrong"})
        assert failed.status_code == 401 and "set-cookie" not in failed.headers
        assert (
            client.post("/api/auth/login", json={"phone": "13800000003", "code": "123456"}).status_code == 422
        )
        assert client.post("/api/auth/login", json=body).json()["id"] == first["id"]


def test_production_supports_password_registration_without_sms(system):
    _, _, _, _, settings = system
    production = settings.model_copy(
        update={
            "env": "production",
            "database_url": "postgresql+psycopg://unused@localhost/unused",
            "secret": "x" * 48,
            "public_url": "https://rag.example",
            "embedding_provider": "openai-compatible",
            "embedding_api_key": "test-key",
        }
    )
    app = create_app(production)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert {"/api/auth/login", "/api/auth/register"} <= paths
    assert "/api/auth/code" not in paths and "/api/auth/dev-login" not in paths
    # Exercise production cookies and auth against the isolated test database.
    app.state.auth.db = system[0].db
    with TestClient(app, base_url="https://rag.example") as client:
        result = client.post("/api/auth/register", json={"username": "prod", "password": "production-pass"})
        assert result.status_code == 201 and "Secure" in result.headers["set-cookie"]
        assert (
            client.post(
                "/api/auth/login", json={"username": "prod", "password": "production-pass"}
            ).status_code
            == 200
        )
    app.state.db.engine.dispose()


def test_login_and_registration_rate_limits(system):
    service, _, _, _, settings = system
    auth = Auth(service.db, settings)
    for _ in range(20):
        with pytest.raises(DomainError) as error:
            auth.login("missing", "wrong", "127.0.0.1")
        assert error.value.code == "invalid_credentials"
    with pytest.raises(DomainError) as error:
        auth.login("missing", "wrong", "127.0.0.1")
    assert error.value.code == "rate_limited"
    auth.register("tester", "test-password", "127.0.0.1")
    for _ in range(9):
        with pytest.raises(DomainError):
            auth.register("tester", "test-password", "127.0.0.1")
    with pytest.raises(DomainError) as error:
        auth.register("new", "test-password", "127.0.0.1")
    assert error.value.code == "rate_limited"


def test_upgrade_preserves_existing_phone_user_and_owned_data(system):
    service, _, _, _, _ = system
    engine = service.db.engine
    Base.metadata.drop_all(engine)
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        connection.execute(
            text("INSERT INTO users (id, phone, created_at) VALUES ('existing', '13800000003', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, user_id, name, description, revision, deleted, created_at) "
                "VALUES ('existing-kb', 'existing', 'existing data', '', 1, false, 1)"
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        row = connection.execute(text("SELECT phone, username FROM users WHERE id='existing'")).one()
        assert row == ("13800000003", None)
        assert (
            connection.execute(text("SELECT user_id FROM knowledge_bases WHERE id='existing-kb'")).scalar()
            == "existing"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1


def test_upgrade_preserves_legacy_password_account_and_invalidates_sessions(system, monkeypatch):
    service, _, _, _, settings = system
    engine = service.db.engine
    Base.metadata.drop_all(engine)
    monkeypatch.setenv("RAG_DEV_USERNAME", "legacy")
    monkeypatch.setenv("RAG_DEV_PASSWORD", "old-configured-password")
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0004")
        connection.execute(
            text("INSERT INTO users (id, username, created_at) VALUES ('legacy-id', 'legacy', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, user_id, name, description, revision, deleted, created_at) "
                "VALUES ('legacy-kb', 'legacy-id', 'existing data', '', 1, false, 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sessions (digest, user_id, csrf, expires_at) VALUES ('old', 'legacy-id', 'csrf', 9999999999)"
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        assert connection.execute(text("SELECT COUNT(*) FROM sessions")).scalar() == 0
    auth = Auth(service.db, settings)
    token, user = auth.login("legacy", "old-configured-password", "127.0.0.1")
    assert user["id"] == "legacy-id"
    assert service.run(auth.authenticate(session_token=token), "list_knowledge_bases")["total"] == 1
    # Legacy settings are no longer consulted after migration.
    monkeypatch.setenv("RAG_DEV_PASSWORD", "changed-config")
    assert auth.login("legacy", "old-configured-password", "127.0.0.1")[1]["id"] == "legacy-id"
