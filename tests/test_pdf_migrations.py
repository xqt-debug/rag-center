from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from rag_center.models import Base
from tests.helpers import pdf_bytes, upload_pdf


def test_text_pdf_retrieval_preserves_physical_page(system):
    service, worker, p, _, _ = system
    base = service.run(p, "create_knowledge_base", {"name": "PDF"})
    doc = upload_pdf(service, p, base["id"], pdf_bytes("Document number PDF-018 with searchable text"))
    worker.tick()
    result = service.run(p, "search", {"query": "PDF-018", "mode": "keyword"})
    assert result["results"][0]["document_id"] == doc["id"]
    assert result["results"][0]["page"] == 1
    assert "第1页" in result["results"][0]["citation"]


def test_pdf_without_text_reports_ocr_required(system):
    service, worker, p, _, _ = system
    base = service.run(p, "create_knowledge_base", {"name": "PDF"})
    doc = upload_pdf(service, p, base["id"], pdf_bytes())
    worker.tick()
    info = service.run(p, "get_document", {"document_id": doc["id"]})
    assert info["status"] == "failed"
    assert "OCR" in info["error"]
    assert service.run(p, "download_document", {"document_id": doc["id"]})["url"]


def test_frozen_migration_upgrade_downgrade_upgrade(system):
    service, _, _, _, _ = system
    engine = service.db.engine
    # 此测试环境独占一个隔离的测试数据库或命名空间。
    Base.metadata.drop_all(engine)
    config = Config("alembic.ini")
    schema = engine.get_execution_options().get("schema_translate_map", {}).get(None)
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names(schema=schema))
        command.downgrade(config, "base")
        assert "users" not in inspect(connection).get_table_names(schema=schema)
        command.upgrade(config, "head")
        assert "chunks" in inspect(connection).get_table_names(schema=schema)
