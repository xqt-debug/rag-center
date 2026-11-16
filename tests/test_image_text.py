"""图片与调用方正文原子绑定，覆盖缓存、授权、重试与非法输入边界。"""

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from rag_center.auth import Principal
from rag_center.errors import DomainError
from rag_center.models import DocumentVersion, Upload
from rag_center.service import Service
from rag_center.worker import Worker


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(stream, "PNG")
    return stream.getvalue()


def payload(service, principal, text="配图讲解：TCP 重传机制。\n保留换行。"):
    kb = service.run(principal, "create_knowledge_base", {"name": "图文资料"})
    return {
        "knowledge_base_id": kb["id"],
        "name": "笔记.png",
        "text": text,
        "image_base64": base64.b64encode(picture()).decode(),
    }


def no_ocr(image):
    pytest.fail("已有配套文字时不应调用 OCR")


def test_image_and_text_survive_worker_restart_and_idempotent_replay(system, monkeypatch):
    service, _, principal, _, settings = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", no_ocr)
    args = payload(service, principal)
    created = service.run(principal, "create_image_document", args, "image-text-1")
    assert service.run(principal, "create_image_document", args, "image-text-1") == created
    # 模拟 API 提交后工作进程重启，正文必须来自持久化版本而非进程内变量。
    assert Worker(service.db, settings).tick()
    doc = service.run(principal, "get_document", {"document_id": created["id"]})
    text = service.run(principal, "read_document", {"document_id": created["id"]})
    assert text["text"] == args["text"] and text["source"] == "provided"
    assert doc["text_source"] == "provided" and doc["is_image"] and doc["status"] == "ready"
    assert doc["size"] == len(picture()) + len(args["text"].encode("utf-8"))
    ticket = service.run(principal, "download_document", {"document_id": doc["id"]})
    path, _ = service.resolve_download(ticket["url"].rsplit("/", 1)[1])
    assert Path(path).read_bytes() == picture()
    with pytest.raises(DomainError, match="其他操作"):
        service.run(principal, "create_image_document", {**args, "text": "不同文字"}, "image-text-1")
    service.run(
        principal, "delete_document", {"document_id": doc["id"], "expected_revision": doc["revision"]}
    )
    with pytest.raises(DomainError) as error:
        service.run(principal, "create_image_document", args, "image-text-1")
    assert error.value.status == 404


def test_same_image_with_different_text_never_reuses_wrong_vectors(system, monkeypatch):
    service, worker, principal, _, _ = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", no_ocr)
    args = payload(service, principal, "ALPHA-2026 合同解释")
    received = []
    original_embed = worker.embedder.embed

    def embed(texts):
        received.extend(texts)
        return original_embed(texts)

    monkeypatch.setattr(worker.embedder, "embed", embed)
    first = service.run(principal, "create_image_document", args)
    worker.tick()
    second = service.run(principal, "create_image_document", {**args, "text": "BETA-2026 学习笔记"})
    worker.tick()
    assert received == [args["text"], "BETA-2026 学习笔记"]
    result = service.run(principal, "search", {"query": "BETA-2026", "mode": "keyword"})
    assert [r["document_id"] for r in result["results"]] == [second["id"]]
    assert first["id"] != second["id"]


def test_upload_id_binds_image_and_text_atomically(system, monkeypatch):
    service, worker, principal, stranger, _ = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", no_ocr)
    args = payload(service, principal)
    args.pop("image_base64")
    ticket = service.run(
        principal,
        "prepare_document_upload",
        {
            "knowledge_base_id": args["knowledge_base_id"],
            "filename": args["name"],
            "size": len(picture()),
        },
    )
    service.receive_upload(ticket["upload_url"].rsplit("/", 1)[1], picture())
    args["upload_id"] = ticket["upload_id"]
    with pytest.raises(DomainError):
        service.run(stranger, "create_image_document", args)
    with pytest.raises(DomainError):
        service.run(principal, "create_image_document", {**args, "name": "伪装.gif"})
    with service.db.sessions() as session:
        assert not session.get(Upload, ticket["upload_id"]).consumed
    created = service.run(principal, "create_image_document", args)
    worker.tick()
    assert service.run(principal, "read_document", {"document_id": created["id"]})["text"] == args["text"]
    with pytest.raises(DomainError):
        service.run(principal, "create_image_document", args)


def test_provided_text_preserved_on_failure_retry_and_model_rebuild(system, monkeypatch):
    service, worker, principal, _, settings = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", no_ocr)
    args = payload(service, principal)
    created = service.run(principal, "create_image_document", args)

    def unavailable(texts):
        raise DomainError("embedding_not_configured", "测试配置缺失", 503)

    monkeypatch.setattr(worker.embedder, "embed", unavailable)
    worker.tick()
    service.run(principal, "retry_document_processing", {"document_id": created["id"]})
    Worker(service.db, settings).tick()
    real = settings.model_copy(
        update={"embedding_provider": "openai-compatible", "embedding_api_key": "test-only"}
    )
    service, worker = Service(service.db, real), Worker(service.db, real)
    monkeypatch.setattr(
        worker.embedder, "embed", lambda texts: [[1.0] * real.embedding_dimensions for _ in texts]
    )
    rebuilt = service.run(principal, "retry_document_processing", {"document_id": created["id"]})
    worker.tick()
    assert service.run(principal, "read_document", {"document_id": created["id"]})["text"] == args["text"]
    with service.db.sessions() as session:
        assert session.get(DocumentVersion, rebuilt["version_id"]).provided_text == args["text"]


@pytest.mark.parametrize(
    "changes",
    [
        {"image_base64": "not-base64!"},
        {"image_base64": "data:image/png;base64,AAAA"},
        {"image_base64": base64.b64encode(b"not an image").decode()},
        {"text": " \n "},
        {"text": "无效\x00文字"},
        {"text": "x" * 200001},
        {"name": "wrong.txt"},
        {"upload_id": "both-sources"},
        {"image_base64": None},
    ],
)
def test_invalid_image_text_payload_rejected_without_creating_document(system, changes):
    service, _, principal, _, _ = system
    args = payload(service, principal)
    with pytest.raises(DomainError):
        service.run(principal, "create_image_document", {**args, **changes})
    assert (
        service.run(principal, "list_documents", {"knowledge_base_id": args["knowledge_base_id"]})["total"]
        == 0
    )


def test_create_permission_and_text_storage_quota_enforced(system):
    service, _, principal, _, settings = system
    args = payload(service, principal)
    key = service.run(
        principal,
        "create_api_key",
        {
            "name": "只读",
            "scopes": ["read"],
            "knowledge_base_ids": [args["knowledge_base_id"]],
        },
    )
    with pytest.raises(DomainError) as error:
        service.run(
            Principal(principal.user_id, "key:" + key["id"], key["id"]), "create_image_document", args
        )
    assert error.value.status == 403
    limited = Service(service.db, settings.model_copy(update={"storage_quota_mb": 1}))
    limited.run(principal, "create_image_document", {**args, "text": "学" * 190000})
    with pytest.raises(DomainError, match="存储空间不足"):
        limited.run(principal, "create_image_document", {**args, "text": "习" * 190000})
