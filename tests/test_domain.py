import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from rag_center.auth import Auth
from rag_center.config import Settings
from rag_center.errors import DomainError
from rag_center.models import ApiKey, Audit, Chunk, Document, DocumentVersion, Job, Operation


def kb(service, p, name="研究资料"):
    return service.run(p, "create_knowledge_base", {"name": name})


def document(service, worker, p, base, content="员工申请年假需要提前三天提交。合同编号 HT-2026-018。"):
    result = service.run(
        p, "create_document", {"knowledge_base_id": base["id"], "name": "员工手册.md", "text": content}
    )
    assert worker.tick()
    return service.run(p, "get_document", {"document_id": result["id"]})


def test_knowledge_base_crud_and_revision(system):
    service, _, p, _, _ = system
    base = kb(service, p)
    assert service.run(p, "list_knowledge_bases")["total"] == 1
    changed = service.run(
        p,
        "update_knowledge_base",
        {"knowledge_base_id": base["id"], "name": "新名称", "expected_revision": 1},
    )
    assert changed["revision"] == 2
    with pytest.raises(DomainError, match="重新读取"):
        service.run(
            p,
            "update_knowledge_base",
            {"knowledge_base_id": base["id"], "name": "旧写入", "expected_revision": 1},
        )
    service.run(p, "delete_knowledge_base", {"knowledge_base_id": base["id"], "expected_revision": 2})
    assert service.run(p, "list_knowledge_bases")["total"] == 0


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_retrieval_with_sources(system, mode):
    service, worker, p, _, _ = system
    base = kb(service, p)
    doc = document(service, worker, p, base)
    assert doc["status"] == "ready", doc
    result = service.run(p, "search", {"query": "HT-2026-018", "mode": mode})
    hit = result["results"][0]
    assert hit["document_id"] == doc["id"]
    assert "员工手册" in hit["citation"]
    expanded = service.run(p, "get_document_excerpt", {"document_id": doc["id"], "chunk_id": hit["chunk_id"]})
    assert "年假" in expanded["chunks"][0]["text"]


def test_document_update_publishes_atomically(system):
    service, worker, p, _, _ = system
    base = kb(service, p)
    doc = document(service, worker, p, base)
    old_hit = service.run(p, "search", {"query": "年假", "mode": "keyword"})["results"][0]
    update = service.run(
        p,
        "update_document",
        {"document_id": doc["id"], "expected_revision": doc["revision"], "text": "新规定：报销需要发票。"},
    )
    assert update["status"] == "queued"
    assert "年假" in service.run(p, "read_document", {"document_id": doc["id"]})["text"]
    assert worker.tick()
    assert "发票" in service.run(p, "read_document", {"document_id": doc["id"]})["text"]
    assert service.run(p, "search", {"query": "年假", "mode": "keyword"})["results"] == []
    with pytest.raises(DomainError) as exc:
        service.run(p, "get_document_excerpt", {"document_id": doc["id"], "chunk_id": old_hit["chunk_id"]})
    assert exc.value.code == "version_changed"


def test_rename_does_not_reindex(system):
    service, worker, p, _, _ = system
    doc = document(service, worker, p, kb(service, p))
    changed = service.run(
        p,
        "update_document",
        {"document_id": doc["id"], "expected_revision": doc["revision"], "name": "手册新版.md"},
    )
    assert changed["current_version"] == doc["current_version"]
    assert not worker.tick()


def test_owner_isolation_all_read_paths(system):
    service, worker, p, stranger, _ = system
    base = kb(service, p)
    doc = document(service, worker, p, base)
    for action, payload in [
        ("get_document", {"document_id": doc["id"]}),
        ("read_document", {"document_id": doc["id"]}),
        ("download_document", {"document_id": doc["id"]}),
        ("get_ingestion_job", {"job_id": doc["job_id"]}),
        ("list_documents", {"knowledge_base_id": base["id"]}),
    ]:
        with pytest.raises(DomainError) as exc:
            service.run(stranger, action, payload)
        assert exc.value.status == 404
    assert service.run(stranger, "search", {"query": "年假"})["results"] == []


def make_key(service, p, bases, scopes):
    result = service.run(
        p, "create_api_key", {"name": "测试 Agent", "knowledge_base_ids": bases, "scopes": scopes}
    )
    auth = Auth(service.db, service.settings)
    return result, auth.authenticate(bearer=result["key"])


def test_key_scope_readonly_and_revocation(system):
    service, worker, p, _, _ = system
    base = kb(service, p)
    second = kb(service, p, "其他资料")
    doc = document(service, worker, p, base)
    key, agent = make_key(service, p, [base["id"]], ["read"])
    assert service.run(agent, "list_knowledge_bases")["total"] == 1
    assert service.run(agent, "search", {"query": "年假"})["results"]
    for action, payload in [
        ("create_document", {"knowledge_base_id": base["id"], "name": "x.txt", "text": "x"}),
        ("delete_document", {"document_id": doc["id"], "expected_revision": doc["revision"]}),
        ("download_document", {"document_id": doc["id"]}),
        ("get_knowledge_base", {"knowledge_base_id": second["id"]}),
    ]:
        with pytest.raises(DomainError) as exc:
            service.run(agent, action, payload)
        assert exc.value.status == 403
    service.run(p, "revoke_api_key", {"key_id": key["id"]})
    with pytest.raises(DomainError) as exc:
        service.run(agent, "list_knowledge_bases")
    assert exc.value.status == 401


def test_full_key_can_create_and_manage_own_new_base(system):
    service, worker, p, _, _ = system
    _, agent = make_key(service, p, [], ["read", "create", "update", "delete", "download", "kb:create"])
    base = kb(service, agent)
    assert service.run(agent, "list_knowledge_bases")["items"][0]["id"] == base["id"]
    doc = document(service, worker, agent, base)
    assert service.run(agent, "download_document", {"document_id": doc["id"]})["url"]
    service.run(agent, "delete_document", {"document_id": doc["id"], "expected_revision": doc["revision"]})
    assert service.run(agent, "search", {"query": "年假"})["results"] == []


def test_idempotent_creation_and_conflict(system):
    service, _, p, _, _ = system
    base = kb(service, p)
    payload = {"knowledge_base_id": base["id"], "name": "x.txt", "text": "内容"}
    first = service.run(p, "create_document", payload, "same-request")
    assert service.run(p, "create_document", payload, "same-request") == first
    assert service.run(p, "list_documents", {"knowledge_base_id": base["id"]})["total"] == 1
    with pytest.raises(DomainError) as exc:
        service.run(p, "create_document", {**payload, "text": "其他内容"}, "same-request")
    assert exc.value.code == "idempotency_conflict"


def test_plaintext_keys_never_persist_in_operation_cache(system):
    service, _, p, _, _ = system
    base = kb(service, p)
    result = service.run(
        p, "create_api_key", {"name": "once", "knowledge_base_ids": [base["id"]]}, "key-request"
    )
    with service.db.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Operation)) == 0
        row = s.get(ApiKey, result["id"])
        assert row.digest != result["key"] and result["key"] not in str(row.__dict__)


def test_upload_download_content_and_revocation(system):
    service, worker, p, _, _ = system
    base = kb(service, p)
    key, agent = make_key(service, p, [base["id"]], ["read", "create", "download"])
    body = "文件传输测试".encode()
    ticket = service.run(
        agent,
        "prepare_document_upload",
        {"knowledge_base_id": base["id"], "filename": "上传.txt", "size": len(body)},
    )
    token = ticket["upload_url"].rsplit("/", 1)[1]
    service.receive_upload(token, body)
    service.receive_upload(token, body)
    with pytest.raises(DomainError):
        service.receive_upload(token, b"different")
    created = service.run(
        agent,
        "create_document",
        {"knowledge_base_id": base["id"], "name": "上传.txt", "upload_id": ticket["upload_id"]},
    )
    assert worker.tick()
    receipt = service.run(agent, "download_document", {"document_id": created["id"]})
    download_token = receipt["url"].rsplit("/", 1)[1]
    path, _ = service.resolve_download(download_token)
    assert Path(path).read_bytes() == body
    service.run(p, "revoke_api_key", {"key_id": key["id"]})
    with pytest.raises(DomainError) as exc:
        service.resolve_download(download_token)
    assert exc.value.status == 401


def test_upload_cannot_cross_key_or_knowledge_base(system):
    service, _, p, _, _ = system
    base = kb(service, p)
    second = kb(service, p, "第二个")
    _, agent = make_key(service, p, [base["id"], second["id"]], ["read", "create"])
    body = b"abc"
    ticket = service.run(
        agent, "prepare_document_upload", {"knowledge_base_id": base["id"], "filename": "x.txt", "size": 3}
    )
    service.receive_upload(ticket["upload_url"].rsplit("/", 1)[1], body)
    for caller, target in [(p, base["id"]), (agent, second["id"])]:
        with pytest.raises(DomainError):
            service.run(
                caller,
                "create_document",
                {"knowledge_base_id": target, "name": "x.txt", "upload_id": ticket["upload_id"]},
            )


def test_deleted_document_cannot_be_republished_or_downloaded(system):
    service, worker, p, _, _ = system
    base = kb(service, p)
    created = service.run(
        p, "create_document", {"knowledge_base_id": base["id"], "name": "x.txt", "text": "删除测试"}
    )
    token = service.run(p, "download_document", {"document_id": created["id"]})["url"].rsplit("/", 1)[1]
    service.run(p, "delete_document", {"document_id": created["id"], "expected_revision": 1})
    assert worker.tick()
    with pytest.raises(DomainError):
        service.resolve_download(token)
    with service.db.sessions() as s:
        doc = s.get(Document, created["id"])
        assert doc.current_version is None
        version = s.get(DocumentVersion, created["version_id"])
        old_path = version.path
    worker.cleanup()
    assert not Path(old_path).exists()


def test_failed_new_version_keeps_previous_current(system):
    service, worker, p, _, _ = system
    doc = document(service, worker, p, kb(service, p))
    changed = service.run(
        p, "update_document", {"document_id": doc["id"], "expected_revision": doc["revision"], "text": "   "}
    )
    worker.tick()
    fresh = service.run(p, "get_document", {"document_id": doc["id"]})
    assert fresh["status"] == "failed"
    assert fresh["current_version"] == doc["current_version"]
    assert "年假" in service.run(p, "read_document", {"document_id": doc["id"]})["text"]
    assert service.run(p, "get_ingestion_job", {"job_id": changed["job_id"]})["error"]


def test_worker_recovers_expired_claim(system):
    service, worker, p, _, _ = system
    base = kb(service, p)
    created = service.run(
        p, "create_document", {"knowledge_base_id": base["id"], "name": "x.txt", "text": "可恢复任务"}
    )
    with service.db.sessions() as s:
        job = s.get(Job, created["job_id"])
        job.status, job.claim, job.lease_until = "processing", "dead-worker", time.time() - 1
        s.commit()
    assert worker.tick()
    assert service.run(p, "get_document", {"document_id": created["id"]})["status"] == "ready"


def test_quota_and_filename_validation(system):
    service, _, p, _, settings = system
    base = kb(service, p)
    with pytest.raises(DomainError):
        service.run(
            p, "create_document", {"knowledge_base_id": base["id"], "name": "../x.txt", "text": "test"}
        )
    with pytest.raises(DomainError):
        service.run(
            p,
            "prepare_document_upload",
            {"knowledge_base_id": base["id"], "filename": "x.txt", "size": 100 * 1024**2},
        )
    settings.storage_quota_mb = 0
    with pytest.raises(DomainError) as exc:
        service.run(p, "create_document", {"knowledge_base_id": base["id"], "name": "x.txt", "text": "test"})
    assert exc.value.code == "quota_exceeded"


def test_embedding_failure_hybrid_falls_back_to_keyword(system, monkeypatch):
    service, worker, p, _, _ = system
    document(service, worker, p, kb(service, p))

    def failure(_):
        raise DomainError("embedding_unavailable", "测试故障", 503)

    monkeypatch.setattr(service.embedder, "embed", failure)
    result = service.run(p, "search", {"query": "年假", "mode": "hybrid"})
    assert result["results"] and result["degraded"]


def test_repeated_processing_does_not_duplicate_chunks(system):
    service, worker, p, _, _ = system
    doc = document(service, worker, p, kb(service, p))
    with service.db.sessions() as s:
        job = s.get(Job, doc["job_id"])
        job.status = "queued"
        s.commit()
    worker.tick()
    with service.db.sessions() as s:
        assert (
            s.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == doc["id"]))
            == doc["chunk_count"]
        )


def test_production_configuration_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None, env="production", storage_dir=tmp_path).validate_deployment()


def test_audit_contains_no_source_content(system):
    service, worker, p, _, _ = system
    document(service, worker, p, kb(service, p), "SECRET_DOCUMENT_TEXT")
    with service.db.sessions() as s:
        events = [r.__dict__ for r in s.scalars(select(Audit))]
    assert "SECRET_DOCUMENT_TEXT" not in str(events)


def test_creation_replay_cannot_expose_deleted_resources(system):
    service, _, p, _, _ = system
    base = service.run(p, "create_knowledge_base", {"name": "temporary"}, "create-kb")
    doc = service.run(
        p,
        "create_document",
        {"knowledge_base_id": base["id"], "name": "x.txt", "text": "temporary"},
        "create-doc",
    )
    service.run(p, "delete_document", {"document_id": doc["id"], "expected_revision": 1})
    with pytest.raises(DomainError):
        service.run(
            p,
            "create_document",
            {"knowledge_base_id": base["id"], "name": "x.txt", "text": "temporary"},
            "create-doc",
        )
    service.run(p, "delete_knowledge_base", {"knowledge_base_id": base["id"], "expected_revision": 1})
    with pytest.raises(DomainError):
        service.run(p, "create_knowledge_base", {"name": "temporary"}, "create-kb")


def test_rename_cannot_mislabel_file_type(system):
    service, worker, p, _, _ = system
    doc = document(service, worker, p, kb(service, p))
    with pytest.raises(DomainError) as exc:
        service.run(
            p,
            "update_document",
            {"document_id": doc["id"], "expected_revision": doc["revision"], "name": "wrong.pdf"},
        )
    assert exc.value.code == "invalid_filename"
    assert service.run(p, "get_document", {"document_id": doc["id"]})["name"] == doc["name"]
