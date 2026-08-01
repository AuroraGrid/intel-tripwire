# AURORA LIVE production deployment

The default production stack uses PostgreSQL 16. SQLite remains supported for local development and controlled single-host fallback deployments.

## Start

```bash
cd aurora-live
cp .env.example .env
# Replace every placeholder in .env with a strong random value.
docker compose up --build -d
```

Open `http://localhost:8090/platform`.

## Create the first administrator

`AURORA_BOOTSTRAP_SECRET` is required for **every** user creation, including the first administrator. Set it in `.env` before starting the stack.

```bash
curl -sS -X POST http://localhost:8090/api/platform/users \
  -H 'Content-Type: application/json' \
  -H "X-Bootstrap-Secret: $AURORA_BOOTSTRAP_SECRET" \
  -d '{"email":"admin@example.com","role":"admin"}'
```

Store the returned bearer token immediately; the server stores only its SHA-256 hash.

Additional accounts use the same bootstrap header:

```bash
curl -sS -X POST http://localhost:8090/api/platform/users \
  -H 'Content-Type: application/json' \
  -H "X-Bootstrap-Secret: $AURORA_BOOTSTRAP_SECRET" \
  -d '{"email":"analyst@example.com","role":"analyst"}'
```

Allowed roles are `viewer`, `analyst`, and `admin`.

## Database selection

`DATABASE_URL` selects PostgreSQL and takes precedence over `DATABASE_PATH`. The default Compose stack configures PostgreSQL automatically. See `POSTGRESQL.md` for migration, rollback, and backup procedures.

For SQLite fallback:

```bash
docker compose -f docker-compose.sqlite.yml up --build -d
```

## Backup

For PostgreSQL:

```bash
docker compose exec -T postgres pg_dump -U aurora -d aurora -Fc > aurora-postgres.dump
```

Test restoration into a separate database regularly. Backups are not verified until they have been restored.

## Reverse proxy

Terminate TLS at a reverse proxy and forward these headers:

- `Host`
- `X-Forwarded-Host`
- `X-Forwarded-Proto: https`

Set `AURORA_CORS_ORIGIN` to the exact public origin. Do not leave it as `*` for an authenticated production deployment.

## Security controls

- The application container runs as an unprivileged user.
- The application root filesystem is read-only.
- Webhook destinations must use HTTPS and cannot be literal local or private IP addresses.
- Webhook bodies may be signed with `X-Aurora-Signature` using HMAC-SHA256.
- The analyst token is stored only in the browser's local storage. Use a dedicated browser profile and clear the token on shared machines.

## Operational limits

- PostgreSQL removes the SQLite single-replica database constraint, but the built-in Python HTTP server remains a Phase 2 production gate.
- Webhook delivery is currently initiated through `POST /api/platform/deliveries/run`; automatic background scheduling is Phase 3.
- Token revocation, organization workspaces, granular RBAC, and immutable audit logs are Phase 4.
