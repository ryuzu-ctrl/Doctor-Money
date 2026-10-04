#!/usr/bin/env bash
set -euo pipefail

# all  = API/dashboard + bot (polling) dalam satu service (default, cocok untuk Railway)
# api  = hanya API/dashboard
# bot  = hanya bot Telegram
APP_RUNTIME="${APP_RUNTIME:-all}"
PORT="${PORT:-8080}"

if [ "$APP_RUNTIME" = "api" ]; then
  exec uvicorn backend.app:app --host 0.0.0.0 --port "$PORT"
fi

if [ "$APP_RUNTIME" = "bot" ]; then
  exec python -m bot.main
fi

if [ "$APP_RUNTIME" = "all" ]; then
  # PORT dipakai FastAPI, jadi bot wajib polling agar tidak berebut port.
  export MODE=polling
  # Buat tabel sekali sebelum dua proses start agar tidak saling balapan.
  python -c "from backend.models import init_db; init_db()"
  uvicorn backend.app:app --host 0.0.0.0 --port "$PORT" &
  python -m bot.main &
  # Jika salah satu proses berhenti, hentikan container agar Railway me-restart.
  wait -n
  status=$?
  kill 0 2>/dev/null || true
  exit "$status"
fi

echo "APP_RUNTIME harus bernilai 'all', 'bot', atau 'api'" >&2
exit 1
