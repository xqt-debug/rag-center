import io
import threading

import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from rag_center.config import Settings
from rag_center.errors import DomainError
from rag_center.ingestion import ParsedDocument
from rag_center.models import Chunk, Document, Job
from tests.helpers import pdf_bytes


def queued_document(service, principal):
    base = service.run(principal, "create_knowledge_base", {"name": "任务边界"})
    return service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": "任务.txt",
            "text": "工作进程边界测试",
        },
    )


def test_incomplete_embedding_response_never_publishes_partial_index(system, monkeypatch):
    service, worker, principal, _, _ = system
    created = queued_document(service, principal)
    monkeypatch.setattr(worker.embedder, "embed", lambda texts: [])
    assert worker.tick()
    document = service.run(principal, "get_document", {"document_id": created["id"]})
    assert document["status"] == "failed"
    assert document["current_version"] is None
    with service.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Chunk)) == 0


def test_replaced_worker_cannot_publish_or_record_failure(system):
    service, worker, principal, _, _ = system
    created = queued_document(service, principal)
    claim = worker._claim_next()
    with service.db.sessions() as session:
        job = session.get(Job, created["job_id"])
        job.claim = "replacement-worker"
        session.commit()
    pieces = [(None, "工作进程边界测试")]
    worker._publish(claim, ParsedDocument(pieces, 0), pieces, worker.embedder.embed([pieces[0][1]]))
    worker._record_failure(claim, DomainError("old_failure", "旧进程的错误"))
    with service.db.sessions() as session:
        job = session.get(Job, created["job_id"])
        assert job.status == "processing" and job.error == ""
        assert session.get(Document, created["id"]).current_version is None


def test_worker_loop_recovers_transient_database_failure(system, monkeypatch, caplog):
    _, worker, _, _, _ = system
    stop = threading.Event()
    calls = []

    def tick():
        calls.append(True)
        if len(calls) == 1:
            raise SQLAlchemyError("sensitive provider payload")
        stop.set()
        return False

    monkeypatch.setattr(worker, "tick", tick)
    monkeypatch.setattr(stop, "wait", lambda seconds: None)
    worker.run(stop)
    assert len(calls) == 2
    assert "sensitive provider payload" not in caplog.text


def test_pdf_page_count_includes_trailing_blank_pages(system):
    service, worker, principal, _, _ = system
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(pdf_bytes("Page-count regression"))))
    writer.add_blank_page(width=612, height=792)
    body = io.BytesIO()
    writer.write(body)
    base = service.run(principal, "create_knowledge_base", {"name": "页码"})
    ticket = service.run(
        principal,
        "prepare_document_upload",
        {
            "knowledge_base_id": base["id"],
            "filename": "pages.pdf",
            "size": len(body.getvalue()),
        },
    )
    service.receive_upload(ticket["upload_url"].rsplit("/", 1)[1], body.getvalue())
    created = service.run(
        principal,
        "create_document",
        {
            "knowledge_base_id": base["id"],
            "name": "pages.pdf",
            "upload_id": ticket["upload_id"],
        },
    )
    worker.tick()
    document = service.run(principal, "get_document", {"document_id": created["id"]})
    assert document["page_count"] == 2 and document["chunk_count"] == 1


@pytest.mark.parametrize(
    "override", [{"sms_provider": "typo"}, {"job_lease_seconds": 0}, {"max_file_mb": -1}]
)
def test_invalid_settings_fail_at_startup(override):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **override)


@pytest.mark.parametrize("operation", ["create_document", "update_document"])
def test_empty_upload_identifier_is_rejected_before_processing(system, operation):
    service, _, principal, _, _ = system
    args = {"upload_id": ""}
    if operation == "create_document":
        args.update(knowledge_base_id="any-id", name="test.txt")
    else:
        args.update(document_id="any-id", expected_revision=1)
    with pytest.raises(DomainError) as error:
        service.run(principal, operation, args)
    assert error.value.code == "invalid_input" and error.value.status == 422
