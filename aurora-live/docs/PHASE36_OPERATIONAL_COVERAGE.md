# Phase 36 — Operational Coverage

Phase 36 moves AURORA LIVE from a successful one-source ingestion proof to recurring multi-provider operations.

## Operational pipeline

`provider policy → due check → circuit gate → discover → fetch → allowlist → validate → hash → timestamp → freshness → durable persistence → provider health → regional qualification → Global Operating Picture`

The worker supports one-shot and continuous execution. Each provider has a persisted interval, failure counter, circuit state, cooldown, next-due time, last-success time, error record and response telemetry.

## Official source baseline

The initial seven-region imagery baseline uses official or intergovernmental sources:

- Oceania — JMA Himawari-9 Australia B13
- Africa — EUMETSAT MTG Africa IR10.5 through EUMETView WMS
- Asia — JMA Himawari-9 Southeast Asia B13
- Middle East — EUMETSAT MTG custom Middle East WMS view
- Europe — EUMETSAT MTG Europe IR10.5 through EUMETView WMS
- North America — NOAA GOES-19 CONUS GeoColor
- South America — NOAA GOES-19 southern South America GeoColor

NASA DSCOVR EPIC remains an additional global source. It does not substitute for the seven regional observations.

Source registration is not evidence. A region qualifies only after its assigned adapter completes a successful fetch, image validation, hashing, timestamp/freshness qualification and durable observation write.

## Primary source documentation

- NASA EPIC API and image archive: `https://epic.gsfc.nasa.gov/about/api`
- NOAA GOES imagery: `https://www.star.nesdis.noaa.gov/goes/`
- JMA Himawari real-time imagery: `https://www.data.jma.go.jp/mscweb/data/himawari/`
- EUMETSAT fixed EUMETView WMS URLs: `https://user.eumetsat.int/resources/user-guides/eumetview-image-download-by-using-fixed-urls-guide`

Attribution and terms are preserved in every registered source record.

## Durable storage

`OperationalStore` accepts either:

- a SQLite path for local development and deterministic qualification
- a PostgreSQL DSN for production history and provider state

It stores append-only ingestion runs and observations plus provider circuit state, rate-limit/response telemetry and scheduler tick history.

## Circuit breaker

A provider opens after three consecutive failed runs. While open, it is skipped until the persisted cooldown expires. The next attempt runs half-open; a successful qualified observation closes the circuit and resets the failure count.

## Public APIs

- `GET /.well-known/aurora-operations.json`
- `GET /api/public/operations/providers`
- `GET /api/public/operations/ticks`
- `GET /api/public/source-health/unified`
- `GET /api/public/imagery/regional-baseline`
- `GET /api/public/global-operating-picture`

## Authenticated API

- `POST /api/platform/operations/run`
  - optional `adapter=<name>`
  - optional `force=true`

## Worker

One due cycle:

```bash
python phase36_worker.py --database var/aurora_operations.sqlite3
```

Continuous production loop:

```bash
python phase36_worker.py --loop --sleep-seconds 60 --database "$AURORA_DATABASE_URL"
```

## Proof boundary

The deterministic suite proves scheduling, persistence, circuit behavior, telemetry, seven-region mapping and qualification semantics. Live CI separately proves NOAA ingestion and the complete seven-region official-source baseline. A live proof must contain successful provider records and non-empty validated, hashed, dimensioned observations. No placeholder or registration-only source counts.
