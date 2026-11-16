"""图片原始字节、OCR 检索、动图引用及短期预览授权的回归验证。"""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from rag_center.app import create_app
from rag_center.auth import Principal
from rag_center.errors import DomainError
from rag_center.ingestion import parse_document
from rag_center.media import validate_image
from rag_center.ocr import frame_indices

FIXTURE = Path(__file__).parent / "fixtures" / "ocr-note.png"


def image_bytes(fmt="PNG", color="white"):
    target = io.BytesIO()
    Image.new("RGB", (80, 40), color).save(target, format=fmt)
    return target.getvalue()


def upload(service, principal, base, name, body, document_id=None):
    ticket = service.run(
        principal,
        "prepare_document_upload",
        {
            "knowledge_base_id": base["id"],
            "filename": name,
            "size": len(body),
            **({"document_id": document_id} if document_id else {}),
        },
    )
    service.receive_upload(ticket["upload_url"].rsplit("/", 1)[1], body)
    return ticket["upload_id"]


def create_image(service, principal, name, body):
    base = service.run(principal, "create_knowledge_base", {"name": "学习图片"})
    ticket = upload(service, principal, base, name, body)
    doc = service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": name,
            "upload_id": ticket,
        },
    )
    return base, doc


def test_real_local_ocr_recognizes_chinese_and_english():
    parsed = parse_document(str(FIXTURE), "学习图片.png")
    text = parsed.text.replace(" ", "")
    assert "学习资料" in text and "TCP" in text and "2026" in text
    assert parsed.page_count == 0


@pytest.mark.parametrize(
    ("extension", "fmt"),
    [
        ("png", "PNG"),
        ("jpg", "JPEG"),
        ("jpeg", "JPEG"),
        ("gif", "GIF"),
        ("webp", "WEBP"),
        ("bmp", "BMP"),
    ],
)
def test_image_ocr_is_searchable_and_original_bytes_are_preserved(system, monkeypatch, extension, fmt):
    service, worker, principal, stranger, settings = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", lambda image: "学习资料 TCP 重传机制")
    body = image_bytes(fmt)
    base, created = create_image(service, principal, "笔记." + extension, body)
    ticket = service.run(principal, "download_document", {"document_id": created["id"]})
    with TestClient(create_app(settings)) as client:
        path = "/api/transfers/download/" + ticket["url"].rsplit("/", 1)[1]
        before = client.get(path + "?inline=true")
        assert before.status_code == 200 and before.content == body
        assert before.headers["content-type"].startswith("image/")
        assert before.headers["content-disposition"].startswith("inline")
        assert before.headers["cache-control"] == "no-store"
        assert worker.tick()
        assert client.get(path).content == body
    doc = service.run(principal, "get_document", {"document_id": created["id"]})
    assert doc["status"] == "ready" and doc["is_image"] and doc["chunk_count"] == 1
    assert service.run(principal, "read_document", {"document_id": doc["id"]})["source"] == "ocr"
    for mode in ("keyword", "semantic", "hybrid"):
        result = service.run(principal, "search", {"query": "TCP", "mode": mode})
        assert result["results"][0]["document_id"] == doc["id"]
    with pytest.raises(DomainError):
        service.run(stranger, "download_document", {"document_id": doc["id"]})
    assert base["id"] == doc["knowledge_base_id"]


def test_gif_samples_frames_deduplicates_text_and_preserves_animation(system, monkeypatch):
    service, worker, principal, _, _ = system
    stream = io.BytesIO()
    frames = [Image.new("RGB", (80, 40), (i * 5, 0, 0)) for i in range(30)]
    frames[0].save(stream, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    body = stream.getvalue()
    calls = []

    def recognize(frame):
        calls.append(frame.getpixel((0, 0)))
        return "常驻字幕" if len(calls) < 12 else "TCP 最后一帧"

    monkeypatch.setattr("rag_center.ocr.recognize_image", recognize)
    base, created = create_image(service, principal, "动画.gif", body)
    worker.tick()
    assert len(calls) == 12
    result = service.run(principal, "search", {"query": "TCP", "mode": "keyword"})
    assert result["results"][0]["citation"] == "动画.gif，第30帧"
    text = service.run(principal, "read_document", {"document_id": created["id"]})["text"]
    assert text.count("常驻字幕") == 1
    ticket = service.run(principal, "download_document", {"document_id": created["id"]})
    path, _ = service.resolve_download(ticket["url"].rsplit("/", 1)[1])
    assert Path(path).read_bytes() == body
    with Image.open(path) as image:
        assert image.n_frames == 30
    assert frame_indices(300)[-1] == 299

    # 替换尚未发布时仍引用旧动画，不能根据新显示名称把帧号误标为页码。
    doc = service.run(principal, "get_document", {"document_id": created["id"]})
    upload_id = upload(service, principal, base, "新版.txt", b"new text", doc["id"])
    service.run(
        principal,
        "update_document",
        {
            "document_id": doc["id"],
            "expected_revision": doc["revision"],
            "upload_id": upload_id,
        },
    )
    pending = service.run(principal, "search", {"query": "TCP", "mode": "keyword"})
    assert pending["results"][0]["citation"] == "动画.txt，第30帧"


def test_image_without_text_is_stored_without_fake_index_or_api_call(system, monkeypatch):
    service, worker, principal, _, _ = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", lambda image: "")

    def unexpected_embedding(texts):
        pytest.fail("无文字时不应调用向量服务")

    monkeypatch.setattr(worker.embedder, "embed", unexpected_embedding)
    _, created = create_image(service, principal, "空白.png", image_bytes())
    worker.tick()
    doc = service.run(principal, "get_document", {"document_id": created["id"]})
    assert doc["status"] == "stored" and doc["current_version"]
    assert doc["indexed_at"] is None and doc["chunk_count"] == 0
    assert not doc["vector_index_ready"] and not doc["needs_reindex"]
    assert service.run(principal, "search", {"query": "空白", "mode": "keyword"})["results"] == []


def test_failed_ocr_keeps_original_downloadable_and_can_retry(system, monkeypatch):
    service, worker, principal, _, _ = system

    def failed(image):
        raise DomainError("ocr_unavailable", "本地 OCR 识别失败", 503)

    monkeypatch.setattr("rag_center.ocr.recognize_image", failed)
    body = image_bytes()
    _, created = create_image(service, principal, "资料.png", body)
    worker.tick()
    doc = service.run(principal, "get_document", {"document_id": created["id"]})
    assert doc["status"] == "failed" and doc["current_version"] is None
    ticket = service.run(principal, "download_document", {"document_id": doc["id"]})
    path, _ = service.resolve_download(ticket["url"].rsplit("/", 1)[1])
    assert Path(path).read_bytes() == body
    monkeypatch.setattr("rag_center.ocr.recognize_image", lambda image: "重试识别成功")
    service.run(principal, "retry_document_processing", {"document_id": doc["id"]})
    worker.tick()
    assert service.run(principal, "get_document", {"document_id": doc["id"]})["status"] == "ready"


def test_image_replacement_and_preview_revocation(system, monkeypatch):
    service, worker, principal, _, settings = system
    monkeypatch.setattr("rag_center.ocr.recognize_image", lambda image: "旧版笔记")
    base, created = create_image(service, principal, "笔记.png", image_bytes())
    worker.tick()
    doc = service.run(principal, "get_document", {"document_id": created["id"]})
    key = service.run(
        principal,
        "create_api_key",
        {
            "name": "图片下载",
            "scopes": ["read", "download"],
            "knowledge_base_ids": [base["id"]],
        },
    )
    actor = Principal(principal.user_id, "key:" + key["id"], key["id"])
    ticket = service.run(actor, "download_document", {"document_id": doc["id"]})
    url = "/api/transfers/download/" + ticket["url"].rsplit("/", 1)[1] + "?inline=true"
    with TestClient(create_app(settings)) as client:
        assert client.get(url).status_code == 200
        service.run(principal, "revoke_api_key", {"key_id": key["id"]})
        assert client.get(url).status_code == 401
    old_ticket = service.run(principal, "download_document", {"document_id": doc["id"]})
    body = image_bytes("GIF", "black")
    upload_id = upload(service, principal, base, "新版.gif", body, doc["id"])
    service.run(
        principal,
        "update_document",
        {
            "document_id": doc["id"],
            "expected_revision": doc["revision"],
            "upload_id": upload_id,
        },
    )
    monkeypatch.setattr("rag_center.ocr.recognize_image", lambda image: "新版笔记")
    pending = service.run(principal, "download_document", {"document_id": doc["id"]})
    assert pending["filename"].endswith(".png")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/transfers/download/" + pending["url"].rsplit("/", 1)[1] + "?inline=true")
        assert response.headers["content-type"] == "image/png"
    worker.tick()
    with pytest.raises(DomainError, match="版本已更新"):
        service.resolve_download(old_ticket["url"].rsplit("/", 1)[1])
    new = service.run(principal, "get_document", {"document_id": doc["id"]})
    assert new["name"].endswith(".gif") and new["media_type"] == "image/gif"
    assert "新版" in service.run(principal, "read_document", {"document_id": doc["id"]})["text"]


def test_reject_fake_and_oversized_images(monkeypatch):
    with pytest.raises(DomainError):
        validate_image("假图.png", b"not an image")
    with pytest.raises(DomainError):
        validate_image("损坏.png", image_bytes()[:20])
    with pytest.raises(DomainError, match="不匹配"):
        validate_image("伪装.gif", image_bytes())
    monkeypatch.setattr("rag_center.media.MAX_IMAGE_PIXELS", 10)
    with pytest.raises(DomainError, match="上限"):
        validate_image("大图.png", image_bytes())


def test_plain_text_still_supports_read_search_and_download(system):
    service, worker, principal, _, _ = system
    base = service.run(principal, "create_knowledge_base", {"name": "文本笔记"})
    text = "纯文本学习资料\nTCP 通过重传保证可靠传输。"
    doc = service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": "笔记.txt",
            "text": text,
        },
    )
    worker.tick()
    assert service.run(principal, "read_document", {"document_id": doc["id"]})["text"] == text
    assert service.run(principal, "search", {"query": "TCP", "mode": "keyword"})["results"]
    ticket = service.run(principal, "download_document", {"document_id": doc["id"]})
    path, _ = service.resolve_download(ticket["url"].rsplit("/", 1)[1])
    assert Path(path).read_bytes() == text.encode("utf-8")
