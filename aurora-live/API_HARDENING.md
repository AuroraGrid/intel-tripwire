# AURORA LIVE production API hardening

Phase 2 runs the platform through Gunicorn and the WSGI entrypoint in `platform_wsgi.py`.

## Required production settings

Set these values before starting Docker Compose:

- `AURORA_CORS_ORIGIN`: exact browser origin, such as `https://aurora.example.com`
- `AURORA_ALLOWED_HOSTS`: comma-separated accepted host names
- `AURORA_TRUSTED_PROXIES`: only reverse-proxy IP addresses or networks controlled by the operator
- `AURORA_BOOTSTRAP_SECRET`: random bootstrap secret
- `AURORA_WEBHOOK_SECRET`: random HMAC secret

Forwarded host, protocol, and client-address headers are ignored unless the direct peer belongs to `AURORA_TRUSTED_PROXIES`.

## Gunicorn

The container starts:

```text
gunicorn platform_wsgi:application --bind 0.0.0.0:8090
```

Tune `GUNICORN_WORKERS` and `GUNICORN_TIMEOUT` for the host. Begin with two workers on a small deployment and increase only after observing database capacity and request latency.

## Probes

- `GET /api/platform/live` confirms that the process can answer HTTP requests without touching the database.
- `GET /api/platform/ready` verifies database connectivity and schema availability.
- `GET /api/platform/health` remains available for compatibility.

Use readiness for load-balancer admission and liveness for process restart decisions.

## Error contract

API failures use this shape:

```json
{
  "error": {
    "code": "forbidden",
    "message": "insufficient role"
  },
  "request_id": "..."
}
```

Every response includes `X-Request-ID`. A valid incoming request ID is preserved; otherwise the application generates one.

Authentication failures return `401` with `WWW-Authenticate: Bearer`. Authenticated users without sufficient roles receive `403`.

## Rate limits

The application applies bounded per-worker fixed-window limits to bootstrap/authentication and write requests:

- `AURORA_AUTH_RATE_LIMIT`, default `10`
- `AURORA_WRITE_RATE_LIMIT`, default `120`
- `AURORA_RATE_WINDOW_SECONDS`, default `60`
- `AURORA_RATE_MAX_CLIENTS`, default `10000`

A rejected request receives `429`, `Retry-After`, and rate-limit headers. These controls reduce accidental and low-volume abuse. Internet-facing deployments should also enforce distributed limits at the reverse proxy or edge because each Gunicorn worker maintains its own limiter.

## Request and browser controls

- request bodies are limited by `AURORA_MAX_BODY_BYTES`
- JSON bodies must contain an object
- unknown hosts are rejected
- wildcard CORS is disabled unless `AURORA_ALLOW_WILDCARD_CORS=1`
- frame embedding is denied
- content sniffing, camera, microphone, and geolocation access are disabled
- forwarded headers are trusted only from configured proxy networks

## Upgrade

Rebuild the image and restart the application:

```bash
docker compose build aurora-platform
docker compose up -d aurora-platform
curl --fail https://aurora.example.com/api/platform/ready
```

Confirm the returned database backend, inspect Gunicorn logs, and verify that the dashboard and an authenticated API request both succeed before removing the previous container.
