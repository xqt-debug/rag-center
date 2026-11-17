#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

image=${1:?Usage: bash deploy.sh ghcr.io/owner/repo@sha256:digest}
[[ "$image" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || {
  echo 'Expected an immutable GHCR image digest.' >&2; exit 1;
}
deploy_dir=${RAG_DEPLOY_DIR:-/home/deck/rag-center-deploy}
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$source_dir/docker-env.sh"
[[ "$deploy_dir" = /* && -d "$deploy_dir" ]] || {
  echo 'Existing absolute deployment directory required.' >&2; exit 1;
}
deploy_dir=$(cd -- "$deploy_dir" && pwd)
exec 9>"$deploy_dir/.deploy.lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
for config in .env app.env compose.yaml; do
  [[ -f "$deploy_dir/$config" && ! -L "$deploy_dir/$config" ]] || {
    echo "Missing regular deployment file: $config" >&2; exit 1;
  }
done
chmod 600 "$deploy_dir/.env" "$deploy_dir/app.env"
[[ -d "$deploy_dir/postgres" && -d "$deploy_dir/files" ]] || {
  echo 'Existing postgres and files directories required.' >&2; exit 1;
}
docker info >/dev/null
docker compose version >/dev/null
export RAG_IMAGE="$image"
compose=(docker compose --project-name rag-center --project-directory "$deploy_dir"
  --env-file "$deploy_dir/.env" -f "$source_dir/compose.yaml")
https=false
if [[ -e "$deploy_dir/caddy.env" ]]; then
  [[ -f "$deploy_dir/caddy.env" && ! -L "$deploy_dir/caddy.env" ]] || exit 1
  chmod 600 "$deploy_dir/caddy.env"
  export CADDY_IMAGE=${2:?HTTPS requires the Caddy GHCR digest as argument 2}
  [[ "$CADDY_IMAGE" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || {
    echo 'Expected an immutable Caddy GHCR digest.' >&2; exit 1;
  }
  https=true
  export CADDYFILE_PATH="$source_dir/Caddyfile"
  compose+=(-f "$source_dir/compose.https.yaml")
fi
"${compose[@]}" config --quiet
if [[ "$https" = true ]]; then
  # Validate before stopping writers; no credentials or rendered config in logs.
  "${compose[@]}" pull caddy
  # Expand these variables inside the container, where caddy.env is loaded.
  # shellcheck disable=SC2016
  "${compose[@]}" run --rm --no-deps --entrypoint sh caddy -ec \
    'test -n "$ALIYUN_ACCESS_KEY_ID" && test -n "$ALIYUN_ACCESS_KEY_SECRET"'
  docker run --rm --env-file "$deploy_dir/caddy.env" \
    -v "$source_dir/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$CADDY_IMAGE" validate --config /etc/caddy/Caddyfile --adapter caddyfile
  # Exercise the actual configured host port (including RootlessKit permissions).
  if [[ -z "$("${compose[@]}" ps --status running -q caddy)" ]]; then
    "${compose[@]}" run --rm --no-deps --service-ports caddy version
  fi
  "${compose[@]}" run --rm --no-deps migrate python -c '
import os
from rag_center.config import get_settings
s = get_settings()
port = int(os.environ["RAG_HTTPS_PORT"])
expected = "https://" + os.environ["RAG_HTTPS_DOMAIN"] + (f":{port}" if port != 443 else "")
assert s.public_url.rstrip("/") == expected, "RAG_PUBLIC_URL must match the configured HTTPS domain and port"
assert s.env == "production", "Set RAG_ENV=production for Secure session cookies"
'
fi
# Refuse a new/foreign database. Never recreate the existing database here.
db_id=$("${compose[@]}" ps -q db)
[[ -n "$db_id" && "$(docker inspect -f '{{.State.Health.Status}}' "$db_id")" = healthy ]]
[[ "$(docker inspect -f '{{.Name}}' "$db_id")" = /rag-center-db-1 ]]
[[ "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}' "$db_id")" = "$deploy_dir/postgres" ]]
"${compose[@]}" --profile tools pull api worker migrate
# Check settings and rootless bind permissions before stopping writers.
"${compose[@]}" run --rm --no-deps migrate python -c '
from pathlib import Path
from rag_center.config import get_settings
s = get_settings(); s.validate_deployment()
p = Path(s.storage_dir) / ".deploy-write-check"; p.write_text("ok"); p.unlink()
'

phase=stopping
old_ids=$("${compose[@]}" ps --status running -q api worker)
on_exit() {
  code=$?
  trap - EXIT INT TERM
  if (( code == 0 )); then return; fi
  if [[ "$phase" = stopping || "$phase" = backup ]]; then
    # Restart original IDs, never recreate with the new image.
    if [[ -n "$old_ids" ]]; then
      while IFS= read -r container; do docker start "$container" >/dev/null || true; done <<< "$old_ids"
    fi
    echo 'Pre-migration failure; attempted to restart original writers.' >&2
  else
    "${compose[@]}" stop api worker >/dev/null 2>&1 || true
    echo 'Migration/start/acceptance failed; writers stopped. Database and backups retained.' >&2
  fi
  exit "$code"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"${compose[@]}" stop -t 60 api worker
phase=backup
backup="$deploy_dir/backups/$(date -u +%Y%m%dT%H%M%SZ)-${GITHUB_RUN_ID:-manual}-$$"
mkdir -p -- "$backup"
"${compose[@]}" exec -T db pg_dump -U rag -d rag -Fc > "$backup/database.dump"
test -s "$backup/database.dump"
"${compose[@]}" exec -T db pg_restore --list < "$backup/database.dump" > "$backup/database.list"
test -s "$backup/database.list"
tar -C "$deploy_dir" -czf "$backup/files.tar.gz" files
tar -tzf "$backup/files.tar.gz" >/dev/null
cp -- "$deploy_dir/.env" "$deploy_dir/app.env" "$deploy_dir/compose.yaml" "$backup/"
for config in release.env deploy.sh docker-env.sh Caddyfile compose.https.yaml caddy.env; do
  if [[ -f "$deploy_dir/$config" ]]; then cp -- "$deploy_dir/$config" "$backup/"; fi
done
(cd -- "$backup" && sha256sum database.dump files.tar.gz .env app.env compose.yaml > SHA256SUMS && sha256sum --check --status SHA256SUMS)
touch "$backup/COMPLETE"

phase=migration
"${compose[@]}" run --rm --no-deps migrate
phase=start
"${compose[@]}" up -d --no-deps --wait --wait-timeout 180 api worker
"${compose[@]}" exec -T api python -c '
import urllib.request
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from rag_center.config import get_settings
with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as r:
    assert r.status == 200
with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=5) as r:
    assert "text/html" in r.headers.get("Content-Type", "")
    assert "<html" in r.read().decode().lower()
engine = create_engine(get_settings().database_url)
with engine.connect() as c:
    assert c.execute(text("SELECT count(*) FROM users")).scalar() >= 0
    assert set(MigrationContext.configure(c).get_current_heads()) == set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
engine.dispose()
'
worker=$("${compose[@]}" ps -q worker)
[[ -n "$worker" ]]
initial=$(docker inspect -f '{{.State.StartedAt}} {{.RestartCount}}' "$worker")
sleep "${RAG_WORKER_STABILITY_SECONDS:-30}"
[[ "$(docker inspect -f '{{.State.Running}}' "$worker")" = true ]]
[[ "$(docker inspect -f '{{.State.StartedAt}} {{.RestartCount}}' "$worker")" = "$initial" ]]
"${compose[@]}" exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5)'
if [[ "$https" = true ]]; then
  # The project directory owns the bind mount, independent of the temporary bundle.
  cp -- "$source_dir/Caddyfile" "$deploy_dir/Caddyfile.tmp"
  mv -- "$deploy_dir/Caddyfile.tmp" "$deploy_dir/Caddyfile"
  export CADDYFILE_PATH="$deploy_dir/Caddyfile"
  "${compose[@]}" up -d --no-deps --force-recreate caddy
  # Verify a publicly trusted certificate, SNI and proxy health without LAN DNS dependency.
  "${compose[@]}" exec -T api python -c '
import http.client, socket, ssl, time
from urllib.parse import urlsplit
from rag_center.config import get_settings
host = urlsplit(get_settings().public_url).hostname
class ProxyConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("caddy", 443), timeout=5)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
for attempt in range(60):
    try:
        c = ProxyConnection(host, timeout=5)
        c.request("GET", "/api/health")
        r = c.getresponse()
        assert r.status == 200
        r.read()
        c.close()
        break
    except (OSError, AssertionError, http.client.HTTPException):
        if attempt == 59:
            raise
        time.sleep(5)
'
fi
if [[ "$source_dir" != "$deploy_dir" ]]; then
  cp -- "$source_dir/compose.yaml" "$deploy_dir/compose.yaml"
  cp -- "$source_dir/deploy.sh" "$source_dir/docker-env.sh" "$deploy_dir/"
  cp -- "$source_dir/compose.https.yaml" "$deploy_dir/compose.https.yaml"
fi
printf 'RAG_IMAGE=%s\n' "$image" > "$deploy_dir/release.env.tmp"
if [[ "$https" = true ]]; then
  printf 'CADDY_IMAGE=%s\n' "$CADDY_IMAGE" >> "$deploy_dir/release.env.tmp"
fi
mv -- "$deploy_dir/release.env.tmp" "$deploy_dir/release.env"
trap - EXIT INT TERM
echo "Deployed $image; coordinated backup: $backup"
