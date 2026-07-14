# AURORA LIVE public-beta release gate

Phase 6 makes the platform a controlled public-beta candidate. A release is not approved solely because the application starts.

## Data gate

- The release engine registers GDELT, USGS, NASA EONET, GDACS, CISA KEV, and ReliefWeb.
- Every source reports capability, tier, official status, latency, record count, reliability score, and degraded state.
- Tracking parameters are removed from canonical URLs.
- Shared origins and near-identical same-family records are collapsed before corroboration is counted.
- Fixtures are used only when no live source returns usable evidence, and the API reports `offline_fallback`.

## Performance gate

Run against the release image:

```bash
python load_test.py --base-url https://aurora.example.com --requests 500 --concurrency 25 --max-p95 2.0 --min-success-rate 0.99
```

The beta target is at least 99% successful cached reads with p95 latency below two seconds. Do not load-test public upstream providers without authorization.

## Deployment gate

1. Use PostgreSQL and immutable application image tags.
2. Terminate TLS at a controlled reverse proxy or managed ingress.
3. Restrict the metrics endpoint to the monitoring network.
4. Require a current worker heartbeat for readiness.
5. Generate independent secrets with at least 32 random bytes.
6. Run `python release_check.py --env .env` and resolve every error.
7. Create and restore-verify a backup before shifting traffic.
8. Run authentication, workspace-isolation, ingest, alert, webhook, readiness, and rollback smoke tests.

## Staged rollout

Begin invite-only. Shift traffic gradually while watching HTTP 5xx rate, p95 latency, readiness, worker failures, source degradation, duplicate suppression, and webhook dead letters. Stop when readiness fails, error rate exceeds 1%, restore verification is stale, or fallback behavior is not clearly labeled.

## Rollback

Stop workers first, remove application replicas from traffic, deploy the prior immutable image, verify database compatibility, and restore a verified backup when data rollback is required. Never run destructive reverse migrations against the only production database.
