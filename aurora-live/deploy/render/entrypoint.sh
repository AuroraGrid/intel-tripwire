#!/bin/sh
# Free Render: bind $PORT immediately so Render's port scan succeeds,
# then start the background worker.
set -eu
PORT="${PORT:-10000}"
export GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
export GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
export AURORA_REQUIRE_WORKER="${AURORA_REQUIRE_WORKER:-0}"
export AURORA_OPEN_ACCESS="${AURORA_OPEN_ACCESS:-1}"

mkdir -p /data 2>/dev/null || true

start_worker() {
  # Give Gunicorn a few seconds to bind before worker CPU load.
  sleep 8
  echo "starting embedded release_worker..."
  exec python release_worker.py
}

if [ "${AURORA_EMBED_WORKER:-1}" = "1" ]; then
  start_worker &
  WORKER_PID=$!
  trap 'kill $WORKER_PID 2>/dev/null || true' EXIT INT TERM
fi

echo "starting gunicorn on 0.0.0.0:${PORT} (workers=${GUNICORN_WORKERS})"
exec gunicorn release_wsgi:application \
  --bind "0.0.0.0:${PORT}" \
  --workers "${GUNICORN_WORKERS}" \
  --timeout "${GUNICORN_TIMEOUT}" \
  --access-logfile - \
  --error-logfile -
