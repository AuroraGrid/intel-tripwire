#!/bin/sh
# Free Render: one web dyno runs API + background worker together.
set -eu
PORT="${PORT:-8090}"
export GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
export GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"

# Soft readiness when worker is co-hosted
export AURORA_REQUIRE_WORKER="${AURORA_REQUIRE_WORKER:-0}"
export AURORA_OPEN_ACCESS="${AURORA_OPEN_ACCESS:-1}"

mkdir -p /data 2>/dev/null || true

if [ "${AURORA_EMBED_WORKER:-1}" = "1" ]; then
  echo "starting embedded release_worker..."
  python release_worker.py &
  WORKER_PID=$!
  trap 'kill $WORKER_PID 2>/dev/null || true' EXIT INT TERM
fi

echo "starting gunicorn on 0.0.0.0:${PORT}"
exec gunicorn release_wsgi:application \
  --bind "0.0.0.0:${PORT}" \
  --workers "${GUNICORN_WORKERS}" \
  --timeout "${GUNICORN_TIMEOUT}" \
  --access-logfile - \
  --error-logfile -
