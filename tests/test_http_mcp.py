import asyncio
import json
import socket
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from rag_center.app import create_app


def test_browser_login_csrf_file_transfer_and_logout(system):
    _, worker, _, _, settings = system
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/actions/list_knowledge_bases", json={}).status_code == 401
        sent = client.post("/api/auth/code", json={"phone": "13900000008"})
        assert sent.status_code == 200
        result = client.post(
            "/api/auth/login", json={"phone": "13900000008", "code": sent.json()["development_code"]}
        )
        csrf = result.json()["csrf"]
        assert "HttpOnly" in result.headers["set-cookie"]
        assert client.post("/api/actions/create_knowledge_base", json={"name": "网页"}).status_code == 403
        headers = {"X-CSRF-Token": csrf}
        rejected = client.post(
            "/api/actions/create_knowledge_base",
            json={"name": "网页"},
            headers={**headers, "Origin": "https://other.example"},
        )
        assert rejected.status_code == 403
        base = client.post(
            "/api/actions/create_knowledge_base", json={"name": "网页"}, headers=headers
        ).json()
        data = "真实上传和下载".encode()
        ticket = client.post(
            "/api/actions/prepare_document_upload",
            headers=headers,
            json={"knowledge_base_id": base["id"], "filename": "资料.txt", "size": len(data)},
        ).json()
        token = ticket["upload_url"].rsplit("/", 1)[1]
        assert client.put(f"/api/transfers/upload/{token}", content=data).status_code == 200
        created = client.post(
            "/api/actions/create_document",
            headers=headers,
            json={"knowledge_base_id": base["id"], "name": "资料.txt", "upload_id": ticket["upload_id"]},
        ).json()
        worker.tick()
        receipt = client.post(
            "/api/actions/download_document", headers=headers, json={"document_id": created["id"]}
        ).json()
        downloaded = client.get("/api/transfers/download/" + receipt["url"].rsplit("/", 1)[1])
        assert downloaded.content == data
        assert downloaded.headers["cache-control"] == "no-store"
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_real_mcp_client_complete_crud_and_revocation(system):
    service, worker, p, _, settings = system
    key = service.run(
        p,
        "create_api_key",
        {"name": "MCP 集成", "scopes": ["read", "create", "update", "delete", "download", "kb:create"]},
    )
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started

    async def scenario():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + key["key"]}) as http:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    assert len(tools) == 17
                    assert "ctx" not in next(t for t in tools if t.name == "search").inputSchema["properties"]

                    async def call(name, payload):
                        result = await client.call_tool(name, payload)
                        assert not result.isError, result
                        return json.loads(result.content[0].text)

                    base = await call("create_knowledge_base", {"name": "Agent 资料", "request_id": "kb-1"})
                    assert (await call("list_knowledge_bases", {}))["total"] == 1
                    base = await call(
                        "update_knowledge_base",
                        {"knowledge_base_id": base["id"], "name": "Agent 文档", "expected_revision": 1},
                    )
                    doc = await call(
                        "create_document",
                        {
                            "knowledge_base_id": base["id"],
                            "name": "MCP.txt",
                            "text": "MCP 可以检索合同 HT-2026-018。",
                            "request_id": "doc-1",
                        },
                    )
                    repeated = await call(
                        "create_document",
                        {
                            "knowledge_base_id": base["id"],
                            "name": "MCP.txt",
                            "text": "MCP 可以检索合同 HT-2026-018。",
                            "request_id": "doc-1",
                        },
                    )
                    assert repeated["id"] == doc["id"]
                    assert worker.tick()
                    current = await call("get_document", {"document_id": doc["id"]})
                    assert current["status"] == "ready"
                    found = await call("search", {"query": "HT-2026-018", "mode": "hybrid"})
                    assert found["results"][0]["document_id"] == doc["id"]
                    assert (await call("read_document", {"document_id": doc["id"]}))["text"]
                    changed = await call(
                        "update_document",
                        {
                            "document_id": doc["id"],
                            "expected_revision": current["revision"],
                            "text": "修改后的文件内容。",
                        },
                    )
                    assert changed["status"] == "queued"
                    assert worker.tick()
                    current = await call("get_document", {"document_id": doc["id"]})
                    await call(
                        "delete_document",
                        {"document_id": doc["id"], "expected_revision": current["revision"]},
                    )
                    assert (await call("list_documents", {"knowledge_base_id": base["id"]}))["total"] == 0
                    await call(
                        "delete_knowledge_base",
                        {"knowledge_base_id": base["id"], "expected_revision": base["revision"]},
                    )
                    assert (await call("list_knowledge_bases", {}))["total"] == 0
        service.run(p, "revoke_api_key", {"key_id": key["id"]})
        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "Bearer " + key["key"]}, json={}
            )
            assert response.status_code == 401

    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()
