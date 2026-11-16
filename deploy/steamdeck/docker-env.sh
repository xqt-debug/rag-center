#!/usr/bin/env bash
# Source this file: runner services do not inherit an interactive terminal's PATH or Docker context.
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

if [[ -z "${DOCKER_HOST:-}" && -z "${DOCKER_CONTEXT:-}" ]]; then
  rag_runtime_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
  if [[ -S "$rag_runtime_dir/docker.sock" ]]; then
    export DOCKER_HOST="unix://$rag_runtime_dir/docker.sock"
  fi
  unset rag_runtime_dir
fi

# GHCR credentials use a temporary config, but Compose may be installed in the user's default config.
if [[ -n "${DOCKER_CONFIG:-}" && "$DOCKER_CONFIG" != "$HOME/.docker" &&
      -x "$HOME/.docker/cli-plugins/docker-compose" &&
      ! -e "$DOCKER_CONFIG/cli-plugins/docker-compose" ]]; then
  mkdir -p -- "$DOCKER_CONFIG/cli-plugins"
  ln -s -- "$HOME/.docker/cli-plugins/docker-compose" "$DOCKER_CONFIG/cli-plugins/docker-compose"
fi
