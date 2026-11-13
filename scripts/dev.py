"""启动本地网页服务与开发模式下的文件处理进程。"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from rag_center.config import get_settings  # noqa: E402
from rag_center.db import Database  # noqa: E402
from rag_center.worker import Worker  # noqa: E402


def main():
    settings = get_settings()
    if settings.env == "production":
        raise SystemExit("Production: run migrations, API and worker separately; see README.md")
    db = Database(settings.database_url)
    db.initialize()
    worker = Worker(db, settings)
    stop = threading.Event()

    thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
    thread.start()
    try:
        uvicorn.run("rag_center.app:app", host="127.0.0.1", port=8000, access_log=False)
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
