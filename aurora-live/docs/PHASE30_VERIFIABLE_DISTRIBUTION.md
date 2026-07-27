# Phase 30 — Verifiable intelligence distribution

Phase 30 adds deterministic packaging and controlled distribution records without making outbound network requests.

## Channels

Distribution channels are workspace-scoped and administrator-managed. Each channel declares a type, destination, active state, and maximum classification clearance.

## Packages

Packages are immutable for a given workspace, package key, and canonical payload. The payload is serialized as sorted canonical JSON and assigned a SHA-256 digest. The manifest records the digest, classification, and payload size.

## Classification gate

The supported levels are `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, and `RESTRICTED`. A package cannot be queued to a channel whose clearance is lower than the package classification.

## Delivery ledger

Queueing requires an idempotency key. Repeated requests with the same workspace and key resolve to the same delivery record. Delivery outcomes are recorded separately with durable evidence such as a receipt, checksum, report, or run identifier.

AURORA records delivery intent and evidence. It does not contact arbitrary destinations, execute webhooks, send email, or perform other outbound delivery from this module.

## API

- `GET|POST /api/platform/distribution/channels`
- `GET|POST /api/platform/distribution/packages`
- `GET|POST /api/platform/distribution/deliveries`
- `POST /api/platform/distribution/deliveries/status`
- `GET /.well-known/aurora-distribution.json`

## Qualification boundary

A queued record is not proof of receipt. `DELIVERED` requires a separately recorded durable receipt. Package integrity does not prove the truth of every claim in the package; it proves only that the packaged payload matches its recorded SHA-256 manifest.
