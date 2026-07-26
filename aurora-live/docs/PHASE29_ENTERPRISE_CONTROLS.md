# Phase 29 — Enterprise deployment controls

Phase 29 adds a workspace-scoped enterprise control plane without replacing the operational qualification and accuracy systems from earlier phases.

## Policy model

Enterprise policies are append-only versions identified by a stable policy key. The latest active version controls compliance evaluation. Policies may declare deployment-field requirements, attested controls, and allowed data-residency regions.

## Deployment registry

Each deployment records its environment, region, data-residency boundary, encryption state, audit logging, external-AI state, owner, and metadata. Deployment registration is idempotent by workspace and deployment name, and every mutation is audited.

## Attestations

Controls that cannot be inferred from deployment configuration require durable evidence. Attestations are append-only, expire after a bounded validity period, and use the newest current result. Expired or absent attestations remain `NOT_VERIFIED`; they never silently pass.

## Compliance

Compliance evaluates the latest active policies against each registered deployment. A deployment is:

- `FAIL` when any required control or region fails;
- `NOT_VERIFIED` when evidence is absent, expired, or no checks exist;
- `PASS` only when every applicable check passes.

`enterprise_ready` requires at least one deployment and no failed or unverified deployment.

## API

- `GET|POST /api/platform/enterprise/policies`
- `GET|POST /api/platform/enterprise/deployments`
- `GET|POST /api/platform/enterprise/attestations`
- `GET /api/platform/enterprise/compliance`
- `GET /.well-known/aurora-enterprise.json`

All private records are workspace-scoped. Writes require administrator permissions. No external AI provider is required.

## Qualification boundary

Phase 29 supplies governance and evidence machinery. It does not self-certify regulatory compliance, security review, contractual readiness, or production suitability. Those conclusions require current independent evidence and operator review.
