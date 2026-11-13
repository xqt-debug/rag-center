from alembic import context
from sqlalchemy import create_engine, text

from rag_center.config import get_settings
from rag_center.models import Base

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:

    def migrate(connection):
        sqlite = connection.dialect.name == "sqlite"
        foreign_keys = None
        if connection.dialect.name == "postgresql":
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.commit()
        if sqlite:
            # SQLite 的 PRAGMA 必须在事务外切换，否则重建有外键引用的表会失败。
            # 只修改本次迁移连接，结束后恢复原设置并检查已有引用。
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        schema_map = connection.get_execution_options().get("schema_translate_map", {})
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=schema_map.get(None),
        )
        try:
            with context.begin_transaction():
                context.run_migrations()
            if sqlite:
                connection.commit()
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("迁移后发现外键引用异常，请检查数据库")
                connection.commit()
        finally:
            if sqlite:
                connection.rollback()
                connection.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
                connection.commit()

    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        migrate(supplied)
    else:
        url = get_settings().database_url
        engine = create_engine(
            url, connect_args={"connect_timeout": 5} if url.startswith("postgresql") else {}
        )
        with engine.connect() as connection:
            migrate(connection)
