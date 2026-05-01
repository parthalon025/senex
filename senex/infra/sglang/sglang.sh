#!/usr/bin/env bash
# SGLang management script — run from Windows via: wsl bash infra/sglang/sglang.sh <cmd>
# Or from within WSL2: bash infra/sglang/sglang.sh <cmd>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f ${SCRIPT_DIR}/docker-compose.yml --env-file ${SCRIPT_DIR}/.env"

usage() {
  echo "Usage: $0 {up|down|restart|logs|status|pull|shell}"
  echo
  echo "  up       Start SGLang (detached)"
  echo "  down     Stop and remove container"
  echo "  restart  Restart the container"
  echo "  logs     Tail server logs (Ctrl+C to stop)"
  echo "  status   Show container health and GPU memory usage"
  echo "  pull     Pull the latest SGLang image"
  echo "  shell    Open a bash shell inside the running container"
  exit 1
}

case "${1:-}" in
  up)
    echo "Starting SGLang..."
    $COMPOSE up -d
    echo "Waiting for health check..."
    # Poll /health until the server is ready (model load can take ~60s on first run).
    for i in $(seq 1 60); do
      if curl -sf http://localhost:30000/health &>/dev/null; then
        echo "SGLang is ready at http://localhost:30000/v1"
        break
      fi
      sleep 3
      if [[ $i -eq 60 ]]; then
        echo "Server did not become healthy in 3 minutes — check logs: $0 logs"
      fi
    done
    ;;
  down)
    $COMPOSE down
    ;;
  restart)
    $COMPOSE restart
    ;;
  logs)
    $COMPOSE logs -f --tail=100
    ;;
  status)
    echo "=== Container status ==="
    $COMPOSE ps
    echo
    echo "=== GPU memory ==="
    nvidia-smi --query-gpu=name,memory.used,memory.free,memory.total \
      --format=csv,noheader,nounits 2>/dev/null \
      | awk -F', ' '{printf "%-30s used: %sMiB  free: %sMiB  total: %sMiB\n", $1, $2, $3, $4}' \
      || echo "(nvidia-smi not available in this shell)"
    ;;
  pull)
    $COMPOSE pull
    ;;
  shell)
    $COMPOSE exec sglang bash
    ;;
  *)
    usage
    ;;
esac
