from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from .models import Base


class Database:
    def __init__(self, url: str):
        if url.startswith("sqlite") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}}
            if url.startswith("sqlite")
            else {"connect_args": {"connect_timeout": 5}}
        )
        if url.endswith(":memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, pool_pre_ping=True, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        if self.engine.dialect.name == "postgresql":
            with self.engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(self.engine)
