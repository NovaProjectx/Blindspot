#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-gpu}"
# docker-compose v1 sometimes gets stuck if the previous image digest was pruned.
for id in $(docker ps -a --format '{{.ID}} {{.Names}}' | grep -E 'nova-blindspot|blindspot' | cut -d' ' -f1 || true); do
  docker rm -f "$id" >/dev/null 2>&1 || true
done
if [ "$MODE" = "gpu" ]; then
  docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build --force-recreate
else
  docker-compose up -d --build --force-recreate
fi
curl -fsS http://127.0.0.1:8080/health && echo
curl -fsS http://127.0.0.1:8080/status && echo
