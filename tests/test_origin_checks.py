import pytest
from fastapi.testclient import TestClient

from rag_center.app import create_app


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)
def test_development_login_accepts_supported_loopback_origins(system, origin):
    _, _, _, _, settings = system
    # 仅在内存中调用 ASGI 应用，不启动监听端口或常驻服务。
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/api/auth/dev-login",
            headers={"Origin": origin},
            json={"username": "admin", "password": "admin123"},
        )
        assert response.status_code == 200
        assert response.json()["username"] == "admin"
        csrf = response.json()["csrf"]
        protected = client.post(
            "/api/actions/list_knowledge_bases", json={}, headers={"Origin": origin, "X-CSRF-Token": csrf}
        )
        assert protected.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "https://other.example",
        "http://localhost.evil.example:8000",
        "http://localhost:9999",
        "null",
    ],
)
def test_development_rejects_unknown_origins(system, origin):
    _, _, _, _, settings = system
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/dev-login",
            headers={"Origin": origin},
            json={"username": "admin", "password": "admin123"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "origin_rejected"
        assert "set-cookie" not in response.headers


def test_configured_public_url_accepts_trailing_slash(system):
    _, _, _, _, settings = system
    settings.public_url = "http://localhost:8000/"
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/dev-login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "admin123"},
        )
        assert response.status_code == 200


def test_production_does_not_trust_development_origins(system, monkeypatch):
    _, _, _, _, settings = system
    production = settings.model_copy(
        update={
            "env": "production",
            "database_url": "postgresql+psycopg://unused@localhost/unused",
            "secret": "x" * 48,
            "public_url": "https://rag.example/",
            "sms_provider": "webhook",
            "sms_webhook_url": "https://sms.example/send",
            "embedding_provider": "openai-compatible",
            "embedding_api_key": "test-key",
        }
    )
    app = create_app(production)
    calls = []

    def send_code(phone, ip):
        calls.append(phone)
        return {"sent": True}

    monkeypatch.setattr(app.state.auth, "send_code", send_code)
    # 仅在内存中处理请求；生产环境不执行开发建表，也不实际连接数据库或发送短信。
    with TestClient(app) as client:
        for origin in ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173"]:
            response = client.post(
                "/api/auth/code", headers={"Origin": origin}, json={"phone": "13800000003"}
            )
            assert response.status_code == 403
        response = client.post(
            "/api/auth/code", headers={"Origin": "https://rag.example"}, json={"phone": "13800000003"}
        )
        assert response.status_code == 200
    assert calls == ["13800000003"]
