#!/usr/bin/env bash
# VPS-PANEL start script (Termux local test / VPS)
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"

echo "[VPS-PANEL] starting on http://${HOST}:${PORT}"
echo "[VPS-PANEL] driver: ${VPSPANEL_DRIVER:-local}  db: data/panel.db"

exec python3 -m uvicorn main:app --host "$HOST" --port "$PORT" "$@"