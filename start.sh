#!/usr/bin/env bash
set -euo pipefail

APP_RUNTIME="${APP_RUNTIME:-bot}"
PORT="${PORT:-8080}"

if [ "$APP_RUNTIME" = "api" ]; then
  exec uvicorn backend.app:app --host 0.0.0.0 --port "$PORT"
fi

if [ "$APP_RUNTIME" = "bot" ]; then
  exec python -m bot.main
fi

echo "APP_RUNTIME harus bernilai 'bot' atau 'api'" >&2
exit 1
