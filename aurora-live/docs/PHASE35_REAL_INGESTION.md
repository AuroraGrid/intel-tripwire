# Phase 35 — Real Official-Source Ingestion

Phase 35 proves the complete imagery pipeline against real official sources rather than manually submitted observations.

## Implemented adapters

- NASA DSCOVR EPIC natural-color imagery through the official EPIC API and archive.
- NOAA NESDIS STAR GOES-19 CONUS GeoColor through the official current-image endpoint.

## Operational pipeline

`discover → HTTPS allowlist → bounded fetch → image validation → dimensions → SHA-256 → capture timestamp → freshness decision → SQLite history → public API`

The transport enforces HTTPS, explicit provider host allowlists, redirect validation, response-size limits, timeouts, retry with exponential backoff, and a descriptive user agent.

The ingestion engine validates PNG, JPEG, GIF and WebP signatures and dimensions without trusting a filename or MIME header alone. It records every run and every successful or failed observation in SQLite. A fetched image is submitted to the Phase 34 freshness and replay controls; ingestion cannot bypass those controls.

## Proof

The required Phase 35 CI matrix runs deterministic end-to-end tests on Python 3.10 through 3.13. A separate required live proof job contacts NASA EPIC, discovers the newest official natural-color record, downloads the corresponding archive image, validates it, hashes it, qualifies it through the imagery registry and verifies the persisted SQLite observation.

The live proof permits a `STALE` outcome because EPIC publication can legitimately lag current time. It does not permit an unvalidated, empty, missing or unpersisted image.

## APIs

- `GET /.well-known/aurora-ingestion.json`
- `GET /api/public/ingestion/adapters`
- `GET /api/public/ingestion/runs`
- `GET /api/public/ingestion/observations`
- `POST /api/platform/ingestion/run`
- `POST /api/platform/ingestion/run?adapter=nasa-epic`
- `POST /api/platform/ingestion/run?adapter=noaa-goes`

The run endpoint is authenticated. Public endpoints expose audit history but never trigger external network work.

## Worker

```bash
python phase35_worker.py --adapter nasa-epic --database var/aurora_ingestion.sqlite3
python phase35_worker.py --adapter noaa-goes --database var/aurora_ingestion.sqlite3
```

This phase proves one operational pipeline. It does not claim that the seven-region or full category matrix has been populated yet.
