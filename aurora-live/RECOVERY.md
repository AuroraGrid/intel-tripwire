# AURORA LIVE recovery and operations

## Telemetry

The production application emits one JSON object per request and propagates `X-Trace-ID`. Supply a safe `X-Trace-ID` to correlate requests across a reverse proxy, application instances, and external log storage. Unhandled exceptions are logged without bearer tokens or request bodies.

`GET /api/platform/metrics` exposes Prometheus text metrics when `AURORA_METRICS_ENABLED=1`. The endpoint includes request totals and durations, readiness state, healthy-worker count, and worker job outcomes. Restrict this endpoint at the reverse proxy or scrape it only from a private monitoring network.

`GET /api/platform/ready` verifies the database and reports worker health. Set `AURORA_REQUIRE_WORKER=1` when the deployment must have at least one current worker heartbeat before receiving traffic. `GET /api/platform/live` remains a process-only liveness check.

## Backup policy

Use encrypted storage with retention appropriate to the deployment. A backup is not considered usable until its checksum is verified and it has passed an isolated restore test.

SQLite:

```bash
python backup.py create --database data/aurora-live.db --output backups/aurora-$(date -u +%Y%m%dT%H%M%SZ).db
python backup.py verify --backup backups/aurora-YYYYMMDDTHHMMSSZ.db
```

PostgreSQL:

```bash
python backup.py create --database "$DATABASE_URL" --output backups/aurora-$(date -u +%Y%m%dT%H%M%SZ).dump
createdb aurora_restore_verify
python backup.py verify --backup backups/aurora-YYYYMMDDTHHMMSSZ.dump --target-database postgresql:///aurora_restore_verify
dropdb aurora_restore_verify
```

Each backup has a sibling `.manifest.json` containing backend, timestamp, size, and SHA-256. Copy the backup and manifest together. PostgreSQL verification deliberately restores with `--clean --if-exists --no-owner` into a separate target database.

## Recovery sequence

1. Declare the incident and stop writes by removing the application and worker from service.
2. Preserve logs, the failed database volume, deployment image digest, configuration, and the latest backup manifest.
3. Select the newest backup that has passed checksum verification.
4. Restore into an isolated database and run the verification command.
5. Start one application replica against the restored target with the worker disabled.
6. Check `/api/platform/live`, `/api/platform/ready`, authentication, incident reads, audit history, and workspace isolation.
7. Start one worker and confirm a current heartbeat and successful scheduled jobs.
8. Shift traffic gradually and monitor error rate, readiness, worker failures, and webhook retries.
9. Record recovery point, recovery time, data loss window, and follow-up actions.

## Deployment rollback

Rollback application code independently from data when the schema remains backward compatible:

1. Keep the previous immutable image tag or digest.
2. Stop new workers first, then remove application replicas from traffic.
3. Deploy the previous image against a restored copy or confirmed-compatible current database.
4. Run health, authentication, workspace, ingest, and worker smoke tests.
5. Return traffic only after readiness and error metrics stabilize.

Do not run destructive reverse migrations against the only production database. Restore a verified pre-change backup when a data rollback is required.

## Alert thresholds

Investigate immediately when readiness is zero, no healthy worker exists while workers are required, HTTP 5xx responses increase, worker jobs repeatedly fail, backup creation fails, or restore verification fails. Treat a successful dump without a successful restore verification as an unverified backup.
