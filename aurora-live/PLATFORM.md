# AURORA LIVE Platform API

The platform service adds durable analyst workflows to the evidence browser using only Python's standard library.

## Included

- SQLite persistence for users, watchlists, incidents, evidence, timelines, notes and alerts
- bearer-token authentication with only token hashes stored
- incident ingestion from the existing AURORA evidence engine
- material-change timeline entries
- watchlist matching by query, category, severity and confidence threshold
- deduplicated alerts
- evidence graph API
- analyst notes

## Run

```bash
cd aurora-live
python platform_api.py --host 127.0.0.1 --port 8090
```

Optional environment variables:

```bash
DATABASE_PATH=/absolute/path/aurora-live.db
AURORA_BOOTSTRAP_SECRET=replace-with-a-long-random-secret
AURORA_CORS_ORIGIN=https://your-frontend.example
```

## First user

```bash
curl -X POST http://127.0.0.1:8090/api/platform/users \
  -H 'Content-Type: application/json' \
  -H 'X-Bootstrap-Secret: replace-with-a-long-random-secret' \
  -d '{"email":"analyst@example.com","role":"admin"}'
```

Store the returned bearer token securely. It is not recoverable.

## API

- `GET /api/platform/health`
- `POST /api/platform/users`
- `GET /api/platform/me`
- `GET|POST /api/platform/watchlists`
- `DELETE /api/platform/watchlists/{id}`
- `POST /api/platform/refresh`
- `POST /api/platform/ingest`
- `GET /api/platform/incidents`
- `GET /api/platform/incidents/{id}`
- `GET /api/platform/incidents/{id}/timeline`
- `GET /api/platform/incidents/{id}/graph`
- `POST /api/platform/incidents/{id}/notes`
- `GET /api/platform/alerts`

SQLite is suitable for local and persistent single-host deployments. Managed PostgreSQL remains the production gate for stateless/serverless hosting.
