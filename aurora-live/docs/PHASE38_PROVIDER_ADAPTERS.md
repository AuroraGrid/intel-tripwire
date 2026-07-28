# Phase 38 transport providers and production operations

Phase 38 includes evidence-gated aviation and maritime providers, durable provider-run telemetry, freshness-aware qualification, and a continuously supervised transport worker.

## AviationWeather.gov

- Official, keyless AviationWeather.gov Data API.
- The current adapter ingests METAR station observations.
- This is aviation-weather coverage, not a complete aircraft-position feed.
- A successful request plus fresh, durably persisted observations may qualify this provider as operational, but does not prove complete aviation coverage.

One-shot run:

```bash
python phase38_worker.py --provider aviation --database var/aurora_transport.sqlite3
```

## AISStream

- Real-time maritime AIS WebSocket provider.
- The API key is read only from `AURORA_AISSTREAM_API_KEY`.
- The application never returns, logs, stores, or commits the credential.
- AISStream is a beta provider with no assumed SLA or complete global reception.

One-shot run:

```bash
AURORA_AISSTREAM_API_KEY='<secret>' \
python phase38_worker.py --provider maritime --database var/aurora_transport.sqlite3
```

Any credential exposed in chat, an issue, a pull request, a log, or source code should be rotated before long-term production use.

## Continuous worker

The worker runs aviation and maritime on independent schedules, writes provider observations and append-only run records, maintains a persistent heartbeat, and enters `DEGRADED` when a provider fails.

```bash
export AURORA_DATABASE_URL='postgresql://aurora:<password>@postgres:5432/aurora'
export AURORA_AISSTREAM_API_KEY='<secret>'
python phase38_worker.py --loop --provider all
```

Default production intervals:

- AviationWeather.gov: 300 seconds
- AISStream: 60 seconds
- Worker heartbeat: 30 seconds
- Evidence freshness ceiling: 900 seconds

Supported environment variables:

```text
AURORA_DATABASE_URL
AURORA_TRANSPORT_DB
AURORA_AISSTREAM_API_KEY
AURORA_TRANSPORT_PROVIDERS
AURORA_AVIATION_INTERVAL_SECONDS
AURORA_MARITIME_INTERVAL_SECONDS
AURORA_TRANSPORT_HEARTBEAT_SECONDS
AURORA_TRANSPORT_HEARTBEAT_STALE_SECONDS
AURORA_TRANSPORT_STALE_SECONDS
AURORA_AIS_MAX_MESSAGES
AURORA_TRANSPORT_TIMEOUT_SECONDS
AURORA_TRANSPORT_WORKER_NAME
```

## Docker Compose deployment

The transport worker is an overlay on the existing PostgreSQL deployment. Set the required secrets outside source control, then start both files together:

```bash
export POSTGRES_PASSWORD='<database-password>'
export AURORA_BOOTSTRAP_SECRET='<bootstrap-secret>'
export AURORA_WEBHOOK_SECRET='<webhook-secret>'
export AURORA_CORS_ORIGIN='https://your-domain.example'
export AURORA_ALLOWED_HOSTS='your-domain.example'
export AURORA_AISSTREAM_API_KEY='<aisstream-secret>'

docker compose \
  -f docker-compose.yml \
  -f docker-compose.transport.yml \
  up -d --build
```

The `aurora-transport-worker` service uses PostgreSQL, restarts automatically, runs read-only except for `/tmp`, and has a database-backed heartbeat healthcheck.

## Operational APIs

Public read endpoints:

```text
GET /api/public/transport/coverage
GET /api/public/transport/health
GET /api/public/transport/providers
GET /api/public/transport/runs
GET /api/public/transport/workers
GET /api/public/transport/observations
GET /api/public/transport/configuration
GET /api/public/global-operating-picture
```

`/transport/health` reports each provider's most recent run, observation count, freshness, seconds since success, operational state, and worker heartbeat state.

## Qualification boundary

Provider registration is not live evidence. A domain qualifies only when all of the following are true:

1. A provider has a recent successful run.
2. That run produced at least one valid observation.
3. The observation was durably persisted.
4. Provider and observation freshness remain inside the configured ceiling.
5. Licensing and completeness limitations remain disclosed.

The GitHub Actions AIS workflow proves that the credential can retrieve and persist a live message in an ephemeral runner. It is not production persistence. Production status requires the continuous worker to use a durable PostgreSQL database.

Transportation and the independent 70-camera webcam matrix retain separate evidence gates.
