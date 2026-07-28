# Phase 34 — Live Imagery

Phase 34 adds continuously refreshed still-image sources as a separate evidence class from webcams.

## Evidence boundary

Registration does not establish that an image is current. A source starts in `UNKNOWN` and can become `FRESH` only after an observation supplies:

- capture time and observation time
- SHA-256 content digest
- image MIME type
- byte length and dimensions
- provider, attribution, licensing and source URLs
- source-specific refresh interval and maximum age

An observation reported as fresh is forced to `STALE` when the capture is older than the source policy or when unchanged content exceeds the replay window. Duplicate content across independent source registrations is linked explicitly.

## Supported imagery categories

Satellite, radar, weather, wildfire, volcano, traffic, disaster, infrastructure, maritime, aviation, government, public sensor and other curated imagery.

## Public APIs

- `GET /.well-known/aurora-imagery.json`
- `GET /api/public/imagery`
- `GET /api/public/imagery/latest`
- `GET /api/public/imagery/coverage`
- `GET /api/public/source-health/imagery`
- `GET /api/public/imagery/{source_id}`

Filters include region, category and state.

## Authenticated APIs

- `POST /api/platform/imagery`
- `POST /api/platform/imagery/{source_id}/observations`

## Coverage matrix

The baseline matrix requires at least one freshness-verified image source in each of the seven canonical regions. The category matrix reports fresh-source counts for every region-category combination without falsely claiming complete category coverage.

## Health states

- `UNKNOWN`: registered but not observed
- `FRESH`: current according to capture age and replay controls
- `STALE`: capture age or unchanged-content window exceeded
- `DEGRADED`: source responds but cannot provide qualified current imagery
- `OFFLINE`: source unavailable or repeatedly degraded

The system preserves the exact source URL, current image URL, content digest, last capture, last observation, last content change, duplicate lineage and operational failure counters.
