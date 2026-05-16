#!/usr/bin/env bash
# Bring the book_alerter container up and wait for it to report healthy.
#
# Usage:
#   scripts/start.sh           # build (if needed) + up -d + wait + smoke-test
#   scripts/start.sh logs      # follow container logs (Ctrl-C to detach)
#   scripts/start.sh down      # stop + remove the container
#   scripts/start.sh restart   # down then up
#   scripts/start.sh status    # show health + recent log tail
#
# Reads .env from the repo root if present (compose handles this); no .env is
# fine, defaults are in docker-compose.yml.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PORT="${PORT:-8000}"
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-60}"

cmd="${1:-up}"

_wait_for_health() {
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
  printf '[start] waiting for %s ' "$HEALTH_URL"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -sf -o /dev/null "$HEALTH_URL"; then
      printf '\n[start] healthy\n'
      return 0
    fi
    printf '.'
    sleep 1
  done
  printf '\n[start] NOT healthy within %ss — recent logs:\n' "$HEALTH_TIMEOUT_S"
  docker compose logs --tail 50 book_alerter || true
  return 1
}

_smoke() {
  echo "[start] /api/health:"
  curl -s "$HEALTH_URL" | head -c 500
  echo
  echo "[start] SPA shell HEAD:"
  curl -sI "http://127.0.0.1:${PORT}/" | head -1
}

case "$cmd" in
  up|"")
    docker compose up -d --build
    _wait_for_health
    _smoke
    echo
    echo "[start] App is up. Open http://127.0.0.1:${PORT}/"
    echo "[start] Tail logs:    scripts/start.sh logs"
    echo "[start] Stop the app: scripts/start.sh down"
    ;;
  down)
    docker compose down
    ;;
  restart)
    docker compose down
    "$0" up
    ;;
  logs)
    docker compose logs -f --tail 100 book_alerter
    ;;
  status)
    docker compose ps
    echo
    echo "[start] last 30 log lines:"
    docker compose logs --tail 30 book_alerter || true
    echo
    if curl -sf "$HEALTH_URL" >/dev/null; then
      echo "[start] /api/health: OK"
    else
      echo "[start] /api/health: DOWN"
    fi
    ;;
  *)
    echo "usage: $0 [up|down|restart|logs|status]" >&2
    exit 2
    ;;
esac
