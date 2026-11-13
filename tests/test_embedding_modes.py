"""测试索引不能冒充真实模型索引；切换模型时保留旧版并安全重建。"""

import pytest

from rag_center.config import Settings
from rag_center.errors import DomainError
from rag_center.retrieval import Embedder
from rag_center.service import Service
from rag_center.worker import Worker


def create_file(service, principal):
    base = service.run(principal, "create_knowledge_base", {"name": "模型切换"})
    return service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": "资料.md",
            "text": "模型测试资料",
        },
    )


def real_settings(settings, key="test-key"):
    return settings.model_copy(
        update={
            "embedding_provider": "openai-compatible",
            "embedding_api_key": key,
        }
    )


def test_missing_key_fails_before_network(monkeypatch):
    settings = Settings(_env_file=None, embedding_api_key="")
    assert settings.embedding_provider == "openai-compatible"

    def unexpected_network(*args, **kwargs):
        pytest.fail("缺少密钥时不应访问向量服务")

    monkeypatch.setattr("rag_center.retrieval.httpx.Client", unexpected_network)
    with pytest.raises(DomainError, match="尚未配置真实向量模型"):
        Embedder(settings).embed(["文本"])


def test_test_index_can_be_rebuilt_with_real_model(system, monkeypatch):
    service, worker, principal, _, settings = system
    created = create_file(service, principal)
    worker.tick()

    def get(svc):
        return svc.run(principal, "get_document", {"document_id": created["id"]})

    old = get(service)
    assert old["index_kind"] == "test"
    assert not old["vector_index_ready"] and old["needs_reindex"]

    configured = real_settings(settings)
    real_service, real_worker = Service(service.db, configured), Worker(service.db, configured)
    # 使用固定响应验证发布流程，不调用外部模型，也不把用户文件发送出去。
    monkeypatch.setattr(
        real_worker.embedder, "embed", lambda texts: [[1.0] * configured.embedding_dimensions for _ in texts]
    )
    queued = real_service.run(principal, "retry_document_processing", {"document_id": created["id"]})
    assert queued["version_id"] != old["current_version"]
    assert get(real_service)["current_version"] == old["current_version"]
    assert (
        real_service.run(principal, "read_document", {"document_id": created["id"]})["text"] == "模型测试资料"
    )
    real_worker.tick()
    ready = get(real_service)
    assert ready["current_version"] == queued["version_id"]
    assert ready["index_kind"] == "semantic" and ready["vector_index_ready"]
    assert not ready["needs_reindex"]
    changed = Service(service.db, configured.model_copy(update={"embedding_model": "another-model"}))
    assert get(changed)["needs_reindex"] and not get(changed)["vector_index_ready"]


def test_rebuild_without_key_keeps_original_version(system):
    service, worker, principal, _, settings = system
    created = create_file(service, principal)
    worker.tick()
    unconfigured = Service(service.db, real_settings(settings, ""))
    args = {"document_id": created["id"]}
    before = unconfigured.run(principal, "get_document", args)
    with pytest.raises(DomainError, match="尚未配置真实向量模型"):
        unconfigured.run(principal, "retry_document_processing", args)
    assert unconfigured.run(principal, "get_document", args) == before


def test_new_file_without_key_never_publishes_ready(system):
    service, _, principal, _, settings = system
    configured = real_settings(settings, "")
    service, worker = Service(service.db, configured), Worker(service.db, configured)
    created = create_file(service, principal)
    worker.tick()
    document = service.run(principal, "get_document", {"document_id": created["id"]})
    assert document["status"] == "failed"
    assert "尚未配置真实向量模型" in document["error"]
    assert document["current_version"] is None and document["indexed_at"] is None
    assert not document["vector_index_ready"]
