# Phase 37 — Evidence-Aware Capability Reconciliation

Phase 37 prevents the canonical product registry from becoming stale as later operational phases ship. It also converts the regional webcam registry into durable operations with an explicit 70-slot qualification matrix.

## Status model

Every capability now exposes two statuses:

- `declared_status` — the canonical implementation expectation from the Phase 32 product contract
- `status` — the effective status supported by current qualified runtime evidence

Runtime evidence may promote or constrain a capability, but it cannot bypass its qualification gate. Registration, fixtures, UI labels and planned endpoints never qualify a capability as `LIVE`.

The reconciled product endpoints are:

- `GET /.well-known/aurora-product.json`
- `GET /api/public/product/capabilities`
- `GET /api/public/product/gaps`
- `GET /api/public/product/gaps?priority=P0`

## Durable webcam operations

Webcam sources and append-only health observations are persisted in SQLite or PostgreSQL. The storage target resolves in this order:

1. `AURORA_WEBCAM_DB`
2. `AURORA_OPERATIONAL_DB`
3. `AURORA_DATABASE_URL`
4. `AURORA_INGESTION_DB`
5. in-memory storage for deterministic tests

The 70-camera requirement remains seven regions multiplied by ten independently health-verified online cameras. The matrix endpoint exposes every required slot without filling missing slots with placeholders:

- `GET /api/public/webcams/matrix`
- `GET /api/public/webcams/{webcam_id}/history`
- `POST /api/platform/webcams/bulk`
- `POST /api/platform/webcams/health/run`

## Conservative health proof

Reachability alone does not prove a live camera.

- HLS requires a valid playlist marker.
- MJPEG requires a multipart stream or JPEG frame signature.
- YouTube requires a live-state marker in the returned page data.
- Ordinary provider pages remain `DEGRADED` unless direct stream evidence is present.
- HTTP failure, resolution failure, private-network targets and probe errors become `OFFLINE` evidence.

Three consecutive degraded observations force a camera to `OFFLINE`, preserving the Phase 33 health semantics.

## Capability gates

- `webcams` becomes `LIVE` only when all seven regions have at least ten online cameras.
- `live-imagery` and `satellite-imagery` become `LIVE` only when all seven official imagery regions have successful validated, hashed and durably persisted observations.
- `source-health` is `LIVE` only when the unified health engine has configured feeds to evaluate.

## Proof boundary

Phase 37 provides the durable source registry, evidence history, probing controls, qualification matrix and runtime-aware status model. It does not claim that 70 real cameras are already curated. The live webcam claim remains false until independently verified sources satisfy every regional slot.