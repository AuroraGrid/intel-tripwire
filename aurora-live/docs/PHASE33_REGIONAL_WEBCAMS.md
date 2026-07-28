# Phase 33 — Regional Webcam Registry

Phase 33 implements the first concrete P0 surface from the canonical AURORA LIVE product contract: a curated global webcam registry with explicit feed-health qualification.

## Operational rule

A registered URL is not evidence that a camera is live. Registration begins in `UNKNOWN`. A camera counts toward the regional requirement only after a successful health observation changes it to `ONLINE`.

AURORA LIVE requires at least 10 independently health-verified online webcams in each region:

- Oceania
- Africa
- Asia
- Middle East
- Europe
- North America
- South America

The coverage endpoint reports registered, online, degraded, offline, unknown, remaining gap, and qualification state for every region.

## Public endpoints

- `GET /.well-known/aurora-webcams.json`
- `GET /api/public/webcams`
- `GET /api/public/webcams?region=Europe&health=ONLINE`
- `GET /api/public/webcams/coverage`
- `GET /api/public/source-health/webcams`

## Authenticated operational endpoints

- `POST /api/platform/webcams`
- `POST /api/platform/webcams/{webcam_id}/health`

Registration requires region, country, city, title, source type, source URL, coordinates, provider, attribution, and a license note. Supported source types are YouTube, HLS, MJPEG, and ordinary provider pages.

## Health semantics

- `UNKNOWN`: registered but never successfully checked
- `ONLINE`: successful current observation
- `DEGRADED`: reachable but impaired, stale, blocked, or intermittently available
- `OFFLINE`: unavailable; three consecutive degraded observations also force this state

Health observations retain last-check and last-success timestamps plus consecutive failure counts.

## Integrity boundary

Phase 33 creates the registry and qualification controls. It does not claim that the system already has 70 live cameras. Regional qualification remains false until curated cameras are added and independently checked. Provider terms, attribution requirements, embedding restrictions, geolocation accuracy, and privacy considerations remain mandatory for every source.
