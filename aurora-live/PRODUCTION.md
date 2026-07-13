# AURORA LIVE production deployment

This release supports a persistent single-host deployment using Docker and a named volume. SQLite remains the active database, so do not run multiple application replicas against the same file.

## Start

```bash
cd aurora-live
cp .env.example .env
# Replace every placeholder in .env with a strong random value.
docker compose up --build -d
```

Open `http://localhost:8090/platform`.

## Create the first administrator

```bash
curl -sS -X POST http://localhost:8090/api/platform/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","role":"admin"}'
```

The first account can be created without the bootstrap header. Store the returned bearer token immediately; the server stores only its SHA-256 hash.

Additional accounts require:

```bash
curl -sS -X POST http://localhost:8090/api/platform/users \
  -H 'Content-Type: application/json' \
  -H "X-Bootstrap-Secret: $AURORA_BOOTSTRAP_SECRET" \
  -d '{"email":"analyst@example.com","role":"analyst"}'
```

## Backup

Stop the application before copying the SQLite file:

```bash
docker compose stop aurora-platform
docker run --rm -v aurora-live_aurora-data:/data -v "$PWD":/backup alpine \
  cp /data/aurora-live.db /backup/aurora-live-backup.db
docker compose start aurora-platform
```

Test restoration regularly. Backups are not verified until they have been restored into a separate environment.

## Reverse proxy

Terminate TLS at a reverse proxy and forward these headers:

- `Host`
- `X-Forwarded-Host`
- `X-Forwarded-Proto: https`

Set `AURORA_CORS_ORIGIN` to the exact public origin. Do not leave it as `*` for an authenticated production deployment.

## Security controls

- The container runs as an unprivileged user.
- The root filesystem is read-only; only `/data` is persistent and writable.
- Webhook destinations must use HTTPS and cannot be literal local or private IP addresses.
- Webhook bodies may be signed with `X-Aurora-Signature` using HMAC-SHA256.
- The analyst token is stored only in the browser's local storage. Use a dedicated browser profile and clear the token on shared machines.

## Operational limits

- SQLite supports a single application replica in this architecture.
- The built-in Python HTTP server is suitable for controlled single-host use, but a PostgreSQL and production application-server migration remains the next scale gate.
- Webhook delivery is currently initiated through `POST /api/platform/deliveries/run`; schedule this endpoint from a trusted internal job runner if automatic delivery is required.
