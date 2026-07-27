# Phase 31 — Continuous Competitive Benchmarking

Phase 31 adds an immutable, workspace-scoped benchmark ledger for recurring comparisons against named external products or operating baselines.

## Controls

- Benchmark targets are administrator-managed and workspace isolated.
- Every run has a stable idempotency key and is immutable after creation.
- Every competitor measurement requires a durable external evidence reference.
- Numeric inputs must be finite. NaN and infinity are rejected.
- Metric direction is explicit: higher-is-better or lower-is-better.
- Tolerance bands produce PARITY instead of false precision.
- Any BEHIND result creates a high-severity benchmark alert.
- A superiority claim is allowed only when every measured row is AHEAD or PARITY.
- Missing evidence is rejected rather than converted into a favorable comparison.

## API

- `GET|POST /api/platform/benchmarks/targets`
- `GET|POST /api/platform/benchmarks/runs`
- `GET /api/platform/benchmarks/latest?target_id=...`
- `GET /.well-known/aurora-benchmarking.json`

## Qualification boundary

This module records and evaluates supplied benchmark evidence. It does not independently reproduce vendor claims, generate competitor measurements, or certify market superiority. A durable reference proves where a measurement came from; it does not automatically prove that the methodology was sound. Independent reproduction remains required for strong public comparative claims.
