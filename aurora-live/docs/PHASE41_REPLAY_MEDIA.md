# Phase 41 — Unified replay and media verification

Phase 41 adds:

1. A durable multi-domain replay ledger
2. Media asset intake with content hash and average-hash lineage
3. Explicit verification states that never claim forensic authenticity from hashing alone

## Domains

`events`, `transport`, `infrastructure`, `markets`, `webcams`, `media`

## Replay APIs

```text
GET /api/public/replay
GET /api/public/replay?from=&to=&domains=markets,transport&limit=250
GET /api/public/replay/coverage
POST /api/platform/replay/sync
```

`POST /api/platform/replay/sync` merges recent durable observations from transport, infrastructure, markets, webcams, and media into the replay store.

## Media APIs

```text
GET /api/public/media
GET /api/public/media/coverage
POST /api/platform/media/verify
```

Verification payload fields:

- `content_base64` (required)
- `source_url`
- `content_type`
- `license_note`
- `parent_event_id`
- `captured_at`

## Verification states

- `HASHED` — unique content digest stored
- `DUPLICATE_OF` — exact SHA-256 or near average-hash match
- `REJECTED` — size/policy rejection
- `FAILED` / `UNVERIFIED` — reserved for incomplete probes

## Integrity boundary

Hash and average-hash checks support lineage and duplicate detection only. They do **not** prove that imagery is authentic, unmanipulated, correctly geolocated, or free of rights restrictions.