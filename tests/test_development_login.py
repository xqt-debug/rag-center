import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text

from rag_center.app import create_app
from rag_center.auth import Auth
from rag_center.errors import DomainError
from rag_center.models import Base, LoginSession, User


def test_password_login_reuses_account_and_knowledge_base(system):
    service, _, _, _, settings = system
    auth = Auth(service.db, settings)
    first_token, first = auth.login_development("admin", "admin123", "127.0.0.1")
    principal = auth.authenticate(session_token=first_token)
    service.run(principal, "create_knowledge_base", {"name": "测试资料"})
    auth.logout(first_token)
    second_token, second = auth.login_development("admin", "admin123", "127.0.0.1")
    assert first["id"] == second["id"] and second["username"] == "admin" and second["phone"] is None
    assert service.run(auth.authenticate(session_token=second_token), "list_knowledge_bases")["total"] == 1


@pytest.mark.parametrize(
    "username,password", [("unknown", "admin123"), ("admin", "wrong"), ("测试", "中文密码")]
)
def test_wrong_password_never_creates_user_or_session(system, username, password):
    service, _, _, _, settings = system
    with pytest.raises(DomainError) as error:
        Auth(service.db, settings).login_development(username, password, "127.0.0.1")
    assert error.value.code == "invalid_credentials"
    with service.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.username.is_not(None))) == 0
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0


def test_custom_credentials_and_production_guard(system):
    service, _, _, _, settings = system
    settings.dev_username = "tester"
    settings.dev_password = SecretStr("different-password")
    auth = Auth(service.db, settings)
    with pytest.raises(DomainError):
        auth.login_development("admin", "admin123", "127.0.0.1")
    assert auth.login_development("tester", "different-password", "127.0.0.1")[1]["username"] == "tester"
    settings.env = "production"
    with pytest.raises(DomainError) as error:
        auth.login_development("tester", "different-password", "127.0.0.1")
    assert error.value.status == 404


def test_password_login_cookie_csrf_logout_and_no_secret_in_health(system):
    _, _, _, _, settings = system
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health").json()
        assert health["login_mode"] == "password" and "admin123" not in str(health)
        failed = client.post("/api/auth/dev-login", json={"username": "admin", "password": "wrong"})
        assert failed.status_code == 401 and "set-cookie" not in failed.headers
        response = client.post("/api/auth/dev-login", json={"username": "admin", "password": "admin123"})
        assert response.status_code == 200 and "HttpOnly" in response.headers["set-cookie"]
        data = response.json()
        assert client.get("/api/auth/me").json()["username"] == "admin"
        assert client.post("/api/actions/create_knowledge_base", json={"name": "CSRF"}).status_code == 403
        headers = {"X-CSRF-Token": data["csrf"]}
        assert (
            client.post(
                "/api/actions/create_knowledge_base", json={"name": "CSRF"}, headers=headers
            ).status_code
            == 200
        )
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_production_does_not_register_password_endpoint(system):
    _, _, _, _, settings = system
    production = settings.model_copy(
        update={
            "env": "production",
            "database_url": "postgresql+psycopg://unused@localhost/unused",
            "secret": "x" * 48,
            "public_url": "https://rag.example",
            "sms_provider": "webhook",
            "sms_webhook_url": "https://sms.example/send",
            "embedding_provider": "openai-compatible",
            "embedding_api_key": "test-key",
        }
    )
    app = create_app(production)
    assert "/api/auth/dev-login" not in {getattr(route, "path", None) for route in app.routes}
    app.state.db.engine.dispose()


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
