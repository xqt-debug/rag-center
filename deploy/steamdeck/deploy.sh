#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

image=${1:?Usage: bash deploy.sh ghcr.io/owner/repo@sha256:digest}
if [[ ! "$image" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
  echo 'Expected an immutable GHCR image digest.' >&2
  exit 1
fi
deploy_dir=${RAG_DEPLOY_DIR:-"$HOME/apps/rag-center"}
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=docker-env.sh
source "$source_dir/docker-env.sh"
mkdir -p -- "$deploy_dir"
deploy_dir=$(cd -- "$deploy_dir" && pwd)
exec 9>"$deploy_dir/.deploy.lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
if [[ ! -f "$deploy_dir/.env" ]]; then
  echo "Create $deploy_dir/.env using deploy/steamdeck/.env.example before deploying." >&2
  exit 1
fi
chmod 600 "$deploy_dir/.env"
docker info >/dev/null
docker compose version >/dev/null
cp -- "$source_dir/compose.yaml" "$deploy_dir/compose.yaml"
export RAG_IMAGE="$image"
compose=(docker compose --project-name rag-center-steamdeck --project-directory "$deploy_dir"
  --env-file "$deploy_dir/.env" -f "$deploy_dir/compose.yaml")
"${compose[@]}" config --quiet
"${compose[@]}" --profile tools pull
"${compose[@]}" up -d --wait --wait-timeout 120 db

# Back up the database before changing its schema. Source images live in a separate persistent volume.
mkdir -p -- "$deploy_dir/backups"
backup="$deploy_dir/backups/$(date -u +%Y%m%dT%H%M%SZ)-${GITHUB_RUN_ID:-manual}-$$.dump"
"${compose[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
test -s "$backup"
echo "Database backup saved: $backup"

# Stop both writers before migration. A failed migration leaves them stopped for inspection.
"${compose[@]}" stop api worker
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" up -d --wait --wait-timeout 180 api worker
"${compose[@]}" exec -T api python -c \
  'from rag_center.config import get_settings; from sqlalchemy import create_engine, text; engine = create_engine(get_settings().database_url); connection = engine.connect(); connection.execute(text("SELECT 1")); connection.close(); engine.dispose()'
printf 'RAG_IMAGE=%s\n' "$image" > "$deploy_dir/release.env"
"${compose[@]}" ps
echo "Deployed $image"
