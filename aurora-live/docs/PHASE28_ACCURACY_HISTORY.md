# Phase 28 — Accuracy and Historical Data Moat

Phase 28 adds the historical evidence needed to improve AURORA from resolved
outcomes without replacing the canonical systems created by earlier phases.

## Canonical ownership

- Phase 11 remains the source of truth for forecast revisions, resolutions,
  Brier score, log loss, and calibration error.
- Phase 14 remains the source of truth for source origins, claims, evidence,
  contradictions, and source reliability configuration.
- Phase 16 remains the source of truth for detections and their revisions.
- Phase 28 stores immutable operational outcome observations for sources,
  detections, analysts, rules, and triggers.

Phase 28 scorecards read the Phase 11 forecast ledger directly. They do not
copy forecast outcomes into a second table and do not silently rewrite a
source's configured reliability.

## Outcome history

Each outcome requires a durable evidence reference and records:

- workspace and subject identity;
- outcome class;
- bounded score and weight;
- domain;
- observation time;
- actor;
- structured metadata.

Rows are append-only through the application API. An identical submission is
idempotent and returns the existing row. Weighted accuracy and Laplace-smoothed
accuracy are deterministic and documented in the scorecard response.

## Historical analogues

Resolved cases have stable canonical keys, evidence, features, and outcomes.
Analogue retrieval uses deterministic token overlap and optional domain
matching. It works offline and requires no external AI or embedding service.
The result is a candidate-history aid, not proof that two events have the same
cause or outcome.

## Syndication history

Normalized content receives a SHA-256 fingerprint. Each observed source and URL
is stored as a separate immutable occurrence. The response distinguishes:

- repeated ingestion of the same occurrence;
- copied content from another source;
- the number of independent lineage keys.

Content similarity alone never establishes source independence.

## API

- `GET|POST /api/platform/accuracy/outcomes`
- `GET /api/platform/accuracy/scorecard`
- `GET|POST /api/platform/accuracy/cases`
- `GET /api/platform/accuracy/analogs`
- `GET|POST /api/platform/accuracy/fingerprints`
- `GET /.well-known/aurora-accuracy-history.json`

All private records are workspace-scoped. Writes require analyst-level `write`
permission and produce immutable audit events.

## Qualification boundary

This phase supplies trustworthy history and scoring machinery. It does not
claim that enough outcomes already exist to demonstrate superiority, source
accuracy, analyst accuracy, or calibrated forecasting. Those conclusions
remain gated by sample size, evidence quality, and Phase 27 qualification.
