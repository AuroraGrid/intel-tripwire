from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import time
import uuid

from app import AGGREGATOR
from operations import Operations
from platform_wsgi import configured_store
from worker_delivery import DeliveryQueue
from worker_state import WorkerState


class AuroraWorker:
    def __init__(self, store=None, worker_id=None, collector=None):
        self.store = store or configured_store()
        self.ops = Operations(self.store)
        self.state = WorkerState(self.store)
        self.queue = DeliveryQueue(
            self.ops,
            max_attempts=int(os.getenv("AURORA_DELIVERY_MAX_ATTEMPTS", "5")),
            base_backoff=int(os.getenv("AURORA_DELIVERY_BACKOFF_SECONDS", "30")),
            max_backoff=int(os.getenv("AURORA_DELIVERY_MAX_BACKOFF_SECONDS", "3600")),
        )
        self.worker_id = worker_id or os.getenv("AURORA_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.collector = collector or AGGREGATOR.collect
        self.refresh_interval = max(10, int(os.getenv("AURORA_REFRESH_INTERVAL_SECONDS", "300")))
        self.delivery_interval = max(1, int(os.getenv("AURORA_DELIVERY_INTERVAL_SECONDS", "10")))
        self.lease_seconds = max(10, int(os.getenv("AURORA_WORKER_LEASE_SECONDS", "120")))
        self.failure_retry = max(5, int(os.getenv("AURORA_WORKER_FAILURE_RETRY_SECONDS", "30")))
        self.poll_seconds = max(1, float(os.getenv("AURORA_WORKER_POLL_SECONDS", "2")))
        self.stopping = False
        self.state.ensure_job("source_refresh", self.refresh_interval)
        self.state.ensure_job("webhook_delivery", self.delivery_interval)

    def stop(self, *_):
        self.stopping = True

    def _new_alerts(self, before):
        with self.store.db() as connection:
            rows = connection.execute("SELECT id,user_id FROM alerts ORDER BY created_at").fetchall()
        return [dict(row) for row in rows if row["id"] not in before]

    def refresh(self):
        with self.store.db() as connection:
            before = {row["id"] for row in connection.execute("SELECT id FROM alerts").fetchall()}
        result = self.store.ingest(self.collector(force=True))
        queued = 0
        for alert in self._new_alerts(before):
            self.ops.queue_deliveries(alert["user_id"], alert["id"])
            queued += 1
        return {**result, "deliveries_queued_for_alerts": queued}

    def run_job(self, name, interval, callback):
        if not self.state.acquire(name, self.worker_id, self.lease_seconds):
            return None
        try:
            result = callback()
            self.state.complete(name, self.worker_id, interval, json.dumps(result, separators=(",", ":"), default=str))
            return result
        except Exception as exc:
            self.state.fail(name, self.worker_id, self.failure_retry, str(exc))
            print(json.dumps({"level": "error", "worker_id": self.worker_id, "job": name, "error": type(exc).__name__, "message": str(exc)}), flush=True)
            return {"error": str(exc)}

    def tick(self):
        self.state.heartbeat(self.worker_id, "running")
        refresh = self.run_job("source_refresh", self.refresh_interval, self.refresh)
        deliveries = self.run_job("webhook_delivery", self.delivery_interval, self.queue.deliver_due)
        return {"refresh": refresh, "deliveries": deliveries}

    def run(self, once=False):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print(json.dumps({"level": "info", "event": "worker_started", "worker_id": self.worker_id}), flush=True)
        try:
            while not self.stopping:
                self.tick()
                if once:
                    break
                time.sleep(self.poll_seconds)
        finally:
            self.state.heartbeat(self.worker_id, "stopped")
            print(json.dumps({"level": "info", "event": "worker_stopped", "worker_id": self.worker_id}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--database")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    store = configured_store(args.database) if args.database else None
    AuroraWorker(store=store, worker_id=args.worker_id).run(once=args.once)


if __name__ == "__main__":
    main()
