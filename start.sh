#!/bin/sh
# One image serves both Railway services: APP_RUNTIME=bot (or SERVICE_ROLE=bot) runs the bot, anything else the API.
runtime="${APP_RUNTIME:-${SERVICE_ROLE:-api}}"
if [ "$runtime" = "bot" ]; then
  exec python -m bot.main
fi
exec uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8080}"
