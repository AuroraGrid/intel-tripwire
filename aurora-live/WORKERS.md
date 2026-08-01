# Background workers

AURORA runs scheduled collection and webhook delivery in a separate `worker.py` process. The API and worker share the same SQLite file or PostgreSQL database.

## Jobs

`source_refresh` collects configured feeds, ingests incidents, creates watchlist alerts, and queues webhook deliveries for newly created alerts.

`webhook_delivery` sends due deliveries. Failures use exponential backoff. After `AURORA_DELIVERY_MAX_ATTEMPTS`, a delivery is marked `dead` and receives a `dead_lettered_at` timestamp.

## Coordination

Job schedules, leases, completion state, and errors are stored in `worker_jobs`. Worker process heartbeats are stored in `worker_heartbeats`.

A job can be acquired only when it is due and its prior lease is absent or expired. This prevents duplicate execution across worker replicas. A worker crash leaves a bounded lease that another worker can recover after `AURORA_WORKER_LEASE_SECONDS`.

## Running

One cycle:

```bash
AURORA_OFFLINE=1 python worker.py --once
```

Continuous worker:

```bash
python worker.py
```

Docker Compose starts `aurora-worker` automatically:

```bash
docker compose up --build
```

## Phase 38–40 (transport / infrastructure / markets)

These screens go stale unless dedicated ingestion runs. Two supported modes:

1. **Local / single-process (default):** `python release_worker.py` starts Phase 22 **and** spawns Phase 38–40 layer workers (`scripts/start_layer_workers.py`). Disable with `AURORA_START_LAYER_WORKERS=0`.
2. **Compose:** dedicated services own the layers (`aurora-transport-worker`, `aurora-infrastructure-worker`, `aurora-markets-worker`). Compose sets `AURORA_START_LAYER_WORKERS=0` on `aurora-worker` so they are not double-started.

Manual one-shot (optional):

```bash
python phase38_worker.py --loop
python phase39_worker.py --loop --interval 300
python phase40_worker.py --loop --interval 300
```

Readiness (`GET /api/platform/ready`) requires a healthy core worker heartbeat and a recent successful `source_refresh` job (cold-start grace via `AURORA_INGESTION_GRACE_SECONDS`).

## Status

An authenticated administrator can query:

```text
GET /api/platform/workers
Authorization: Bearer <admin-token>
```

The response contains all scheduled jobs, current lease ownership, last result, run count, worker heartbeats, and `healthy_workers`. A worker is healthy when its latest heartbeat is within `AURORA_WORKER_STALE_SECONDS` and its status is `running`.

## Important settings

- `AURORA_REFRESH_INTERVAL_SECONDS`: source refresh period; default 300.
- `AURORA_DELIVERY_INTERVAL_SECONDS`: delivery sweep period; default 10.
- `AURORA_WORKER_LEASE_SECONDS`: stale-job recovery window; default 120.
- `AURORA_WORKER_FAILURE_RETRY_SECONDS`: retry delay for failed scheduled jobs; default 30.
- `AURORA_DELIVERY_MAX_ATTEMPTS`: maximum webhook attempts; default 5.
- `AURORA_DELIVERY_BACKOFF_SECONDS`: initial delivery retry delay; default 30.
- `AURORA_DELIVERY_MAX_BACKOFF_SECONDS`: maximum retry delay; default 3600.
- `AURORA_WORKER_STALE_SECONDS`: heartbeat health threshold exposed by the API; default 120.

## Recovery

A stopped worker does not require manual lease cleanup. Start a replacement worker and allow the active lease to expire. Dead-letter deliveries are retained for investigation and are not retried automatically.

Before changing retry or scheduling intervals in production, verify webhook provider limits and expected feed collection volume.
