import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from rag_center.auth import Principal
from rag_center.config import Settings
from rag_center.db import Database
from rag_center.models import User
from rag_center.service import Service
from rag_center.worker import Worker


def pytest_addoption(parser):
    parser.addoption(
        "--postgresql", action="store_true", help="Run in isolated schemas on local compose PostgreSQL"
    )


@pytest.fixture
def system(tmp_path, request):
    default_url = (
        "postgresql+psycopg://rag:rag@127.0.0.1:5432/rag"
        if request.config.getoption("--postgresql")
        else f"sqlite:///{tmp_path / 'test.sqlite'}"
    )
    url = os.environ.get("RAG_TEST_DATABASE_URL", default_url)
    schema = None
    admin = None
    if url.startswith("postgresql"):
        # 每个测试使用新建的独立数据库命名空间，不清空或复用业务表。
        schema = "test_" + uuid.uuid4().hex
        admin = create_engine(url, connect_args={"connect_timeout": 5})
        with admin.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))

        def drop_test_schema():
            # 在初始化测试环境前注册清理回调，确保建表或填充数据失败时，
            # 也只清理本测试拥有的确切命名空间，不使用前缀通配删除。
            try:
                with admin.begin() as conn:
                    conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                admin.dispose()

        request.addfinalizer(drop_test_schema)
        url = (
            make_url(url)
            .update_query_dict({"options": f"-csearch_path={schema},public"})
            .render_as_string(hide_password=False)
        )
    settings = Settings(
        _env_file=None,
        database_url=url,
        storage_dir=tmp_path / "files",
        embedding_dimensions=32,
        # 测试显式选择替身，应用默认配置不再自动使用测试向量。
        embedding_provider="local-test",
        job_lease_seconds=15,
    )
    db = Database(url)
    if schema:
        # 开发数据库的 public 命名空间可能已有同名表，必须显式指定
        # 测试表所属命名空间，避免 create_all 误将业务表视为测试表。
        db.engine = db.engine.execution_options(schema_translate_map={None: schema})
        db.sessions.configure(bind=db.engine)
    request.addfinalizer(db.engine.dispose)
    db.initialize()
    with db.sessions() as s:
        owner = User(phone="13800000001")
        other = User(phone="13800000002")
        s.add_all([owner, other])
        s.commit()
        p = Principal(owner.id, f"user:{owner.id}")
        stranger = Principal(other.id, f"user:{other.id}")
    service, worker = Service(db, settings), Worker(db, settings)
    yield service, worker, p, stranger, settings
