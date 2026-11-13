import time

from rag_center.models import DocumentVersion


def test_index_timestamp_is_recorded_only_after_publish(system):
    service, worker, principal, _, _ = system
    base = service.run(principal, "create_knowledge_base", {"name": "索引可见性"})
    created = service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": "资料.md",
            "text": "可用于检索的资料",
        },
    )
    pending = service.run(principal, "get_document", {"document_id": created["id"]})
    assert pending["indexed_at"] is None and pending["status"] == "queued"
    before = time.time()
    worker.tick()
    ready = service.run(principal, "get_document", {"document_id": created["id"]})
    assert ready["status"] == "ready" and ready["chunk_count"] > 0
    assert before <= ready["indexed_at"] <= time.time()
    listing = service.run(principal, "list_documents", {"knowledge_base_id": base["id"]})
    assert listing["items"][0]["indexed_at"] == ready["indexed_at"]

    service.run(
        principal,
        "update_document",
        {"document_id": ready["id"], "expected_revision": ready["revision"], "text": "   "},
    )
    worker.tick()
    failed = service.run(principal, "get_document", {"document_id": ready["id"]})
    assert failed["status"] == "failed"
    assert failed["current_version"] == ready["current_version"]
    assert failed["indexed_at"] == ready["indexed_at"]


def test_legacy_completion_time_remains_unknown(system):
    service, worker, principal, _, _ = system
    base = service.run(principal, "create_knowledge_base", {"name": "历史文档"})
    created = service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": "旧资料.txt",
            "text": "历史文本",
        },
    )
    worker.tick()
    with service.db.sessions() as session:
        session.get(DocumentVersion, created["version_id"]).indexed_at = None
        session.commit()
    document = service.run(principal, "get_document", {"document_id": created["id"]})
    assert document["status"] == "ready" and document["indexed_at"] is None
