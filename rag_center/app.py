import hmac
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers

from .auth import Auth
from .config import get_settings
from .db import Database
from .errors import DomainError, require
from .mcp_server import build_mcp
from .models import User
from .service import Service


class MCPAuthMiddleware:
    def __init__(self, app, auth):
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            header = Headers(scope=scope).get("authorization", "")
            try:
                require(header.startswith("Bearer "), "unauthorized", "MCP 需要 Bearer 密钥", 401)
                principal = await anyio.to_thread.run_sync(lambda: self.auth.authenticate(bearer=header[7:]))
                scope.setdefault("state", {})["principal"] = principal
            except DomainError as exc:
                response = JSONResponse(
                    {"error": exc.payload()},
                    status_code=exc.status,
                    headers={"WWW-Authenticate": 'Bearer realm="rag-center"'},
                )
                await response(scope, receive, send)
                return
            except SQLAlchemyError:
                response = JSONResponse(
                    {"error": {"code": "database_unavailable", "message": "数据服务暂时不可用"}}, 503
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class PhoneInput(BaseModel):
    phone: str = Field(min_length=11, max_length=11, pattern=r"^1[3-9]\d{9}$")


class LoginInput(PhoneInput):
    code: str = Field(pattern=r"^\d{6}$")


class DevelopmentLoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def create_app(settings=None):
    settings = settings or get_settings()
    settings.validate_deployment()
    db = Database(settings.database_url)
    auth, service = Auth(db, settings), Service(db, settings)
    mcp = build_mcp(service)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        if settings.env != "production":
            await anyio.to_thread.run_sync(db.initialize)
        async with mcp.session_manager.run():
            yield
        db.engine.dispose()

    app = FastAPI(
        title="RAG Center",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.env != "production" else None,
    )
    app.state.db, app.state.auth, app.state.service, app.state.mcp = db, auth, service, mcp

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"error": exc.payload()}, status_code=exc.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(
            {"error": {"code": "database_unavailable", "message": "数据服务暂时不可用，请稍后重试"}}, 503
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            allowed_origins = {settings.public_url.rstrip("/")}
            if settings.env == "development":
                # 浏览器将 localhost 和 127.0.0.1 视为不同来源。
                # 开发环境明确放行网页服务及前端开发服务器的两种本地地址。
                # 不使用请求中的 Host 直接扩展信任范围，生产环境仍仅信任配置地址。
                allowed_origins |= {
                    "http://localhost:8000",
                    "http://127.0.0.1:8000",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                }
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": {"code": "origin_rejected", "message": "请求来源不受信任"}}, 403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/mcp")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def web_principal(request):
        p = auth.authenticate(session_token=request.cookies.get("rag_session"))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require(
                hmac.compare_digest(request.headers.get("x-csrf-token", ""), p.csrf or ""),
                "csrf_failed",
                "登录状态校验失败，请刷新页面",
                403,
            )
        return p

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "environment": settings.env,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_provider == "openai-compatible"
            and bool(settings.embedding_api_key.strip()),
            "database": db.engine.dialect.name,
            "max_file_mb": settings.max_file_mb,
            "login_mode": "password" if settings.env == "development" else "phone",
        }

    @app.post("/api/auth/code")
    def send_code(body: PhoneInput, request: Request):
        return auth.send_code(body.phone, request.client.host if request.client else "unknown")

    def login_response(token: str, user: dict):
        response = JSONResponse(user)
        response.set_cookie(
            "rag_session",
            token,
            httponly=True,
            secure=settings.env == "production",
            samesite="strict",
            max_age=settings.session_ttl,
            path="/",
        )
        return response

    @app.post("/api/auth/login")
    def login(body: LoginInput, request: Request):
        return login_response(
            *auth.login(body.phone, body.code, request.client.host if request.client else "unknown")
        )

    if settings.env == "development":

        @app.post("/api/auth/dev-login")
        def development_login(body: DevelopmentLoginInput, request: Request):
            return login_response(
                *auth.login_development(
                    body.username, body.password, request.client.host if request.client else "unknown"
                )
            )

    @app.get("/api/auth/me")
    def me(request: Request):
        p = web_principal(request)
        with db.sessions() as s:
            user = s.get(User, p.user_id)
            return auth.user_info(user, p.csrf)

    @app.post("/api/auth/logout")
    def logout(request: Request):
        web_principal(request)
        auth.logout(request.cookies.get("rag_session"))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("rag_session")
        return response

    @app.post("/api/actions/{action}")
    def action(action: str, body: dict, request: Request):
        p = web_principal(request)
        # 完整密钥不得写入幂等回执缓存。
        request_id = None if action == "create_api_key" else request.headers.get("idempotency-key")
        return service.run(p, action, body, request_id)

    @app.put("/api/transfers/upload/{token}")
    async def upload(token: str, request: Request):
        size = 0
        parts = []
        async for part in request.stream():
            size += len(part)
            require(size <= settings.max_file_mb * 1024**2, "file_too_large", "文件超过大小限制", 413)
            parts.append(part)
        return await anyio.to_thread.run_sync(lambda: service.receive_upload(token, b"".join(parts)))

    @app.get("/api/transfers/download/{token}")
    def download(token: str, inline: bool = False):
        path, name = service.resolve_download(token)
        if inline:
            from .media import image_media_type, is_image

            require(is_image(name), "unsupported_preview", "在线原文件预览仅支持图片")
            return FileResponse(
                path, filename=name, media_type=image_media_type(name), content_disposition_type="inline"
            )
        return FileResponse(path, filename=name, media_type="application/octet-stream")

    frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"

    @app.get("/")
    def index():
        if (frontend / "index.html").exists():
            return FileResponse(frontend / "index.html")
        return JSONResponse(
            {
                "message": "前端尚未构建，请在 frontend 中运行 npm install 和 npm run build",
                "api_docs": "/api/docs",
            }
        )

    if (frontend / "assets").exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
    app.mount("/", MCPAuthMiddleware(mcp_app, auth))
    return app


app = create_app()
