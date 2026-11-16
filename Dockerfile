FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS dependencies
WORKDIR /app
RUN pip install --no-cache-dir "uv>=0.9,<1"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.11-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAG_STORAGE_DIR=/app/data/files
COPY --from=dependencies /app/.venv /app/.venv
COPY rag_center/ ./rag_center/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY --from=frontend /web/dist ./frontend/dist/
RUN mkdir -p /app/data/files && chown -R app:app /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "rag_center.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
