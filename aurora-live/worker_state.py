from __future__ import annotations

from datetime import datetime, timedelta, timezone

from storage import now


def iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))).isoformat().replace("+00:00", "Z")


class WorkerState:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_jobs(
                    name TEXT PRIMARY KEY,
                    interval_seconds INTEGER NOT NULL,
                    next_run_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_started_at TEXT,
                    last_finished_at TEXT,
                    last_status TEXT,
                    last_error TEXT,
                    run_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS worker_heartbeats(
                    worker_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_worker_jobs_due ON worker_jobs(next_run_at,lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_time ON worker_heartbeats(heartbeat_at);
                """
            )

    def ensure_job(self, name: str, interval_seconds: int, due_now: bool = True):
        stamp = now() if due_now else iso_after(interval_seconds)
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO worker_jobs(name,interval_seconds,next_run_at,last_status,run_count)
                VALUES(?,?,?,?,0) ON CONFLICT(name) DO NOTHING""",
                (name, max(1, int(interval_seconds)), stamp, "idle"),
            )
            connection.execute(
                "UPDATE worker_jobs SET interval_seconds=? WHERE name=?",
                (max(1, int(interval_seconds)), name),
            )

    def acquire(self, name: str, worker_id: str, lease_seconds: int) -> bool:
        stamp = now()
        expires = iso_after(lease_seconds)
        with self.store.db() as connection:
            changed = connection.execute(
                """UPDATE worker_jobs
                SET lease_owner=?,lease_expires_at=?,last_started_at=?,last_status='running',last_error=NULL
                WHERE name=? AND next_run_at<=?
                AND (lease_owner=? OR lease_expires_at IS NULL OR lease_expires_at<=?)""",
                (worker_id, expires, stamp, name, stamp, worker_id, stamp),
            ).rowcount
        return changed == 1

    def renew(self, name: str, worker_id: str, lease_seconds: int) -> bool:
        with self.store.db() as connection:
            changed = connection.execute(
                "UPDATE worker_jobs SET lease_expires_at=? WHERE name=? AND lease_owner=?",
                (iso_after(lease_seconds), name, worker_id),
            ).rowcount
        return changed == 1

    def complete(self, name: str, worker_id: str, interval_seconds: int, details: str = "") -> bool:
        stamp = now()
        with self.store.db() as connection:
            changed = connection.execute(
                """UPDATE worker_jobs SET next_run_at=?,lease_owner=NULL,lease_expires_at=NULL,
                last_finished_at=?,last_status='success',last_error=?,run_count=run_count+1
                WHERE name=? AND lease_owner=?""",
                (iso_after(interval_seconds), stamp, details[:500] or None, name, worker_id),
            ).rowcount
        return changed == 1

    def fail(self, name: str, worker_id: str, retry_seconds: int, error: str) -> bool:
        stamp = now()
        with self.store.db() as connection:
            changed = connection.execute(
                """UPDATE worker_jobs SET next_run_at=?,lease_owner=NULL,lease_expires_at=NULL,
                last_finished_at=?,last_status='failed',last_error=?,run_count=run_count+1
                WHERE name=? AND lease_owner=?""",
                (iso_after(retry_seconds), stamp, str(error)[:500], name, worker_id),
            ).rowcount
        return changed == 1

    def heartbeat(self, worker_id: str, status: str = "running", details: str = ""):
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO worker_heartbeats(worker_id,started_at,heartbeat_at,status,details)
                VALUES(?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
                heartbeat_at=excluded.heartbeat_at,status=excluded.status,details=excluded.details""",
                (worker_id, stamp, stamp, status, details[:500] or None),
            )

    def status(self, stale_after_seconds: int = 120):
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, int(stale_after_seconds)))).isoformat().replace("+00:00", "Z")
        with self.store.db() as connection:
            jobs = [dict(row) for row in connection.execute("SELECT * FROM worker_jobs ORDER BY name").fetchall()]
            # Drop dead/restart leftovers so UI does not show 1/N after redeploys.
            connection.execute(
                "DELETE FROM worker_heartbeats WHERE heartbeat_at<? OR status IS NULL OR status<>?",
                (cutoff, "running"),
            )
            workers = [dict(row) for row in connection.execute("SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC").fetchall()]
        for worker in workers:
            worker["healthy"] = str(worker.get("heartbeat_at") or "") >= cutoff and str(worker.get("status") or "") == "running"
        healthy = sum(1 for worker in workers if worker["healthy"])
        # total_workers is the active set after pruning — not historical process IDs
        return {
            "jobs": jobs,
            "workers": workers,
            "healthy_workers": healthy,
            "total_workers": len(workers),
            "active_workers": len(workers),
        }
