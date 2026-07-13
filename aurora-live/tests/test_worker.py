import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from operations import Operations
from storage import Store, now
from worker import AuroraWorker
from worker_delivery import DeliveryQueue
from worker_state import WorkerState

EVENT = {
    "id": "worker-i1",
    "title": "Major port outage",
    "category": "infrastructure",
    "severity": "high",
    "k_align_status": "PLAUSIBLE",
    "confidence_grade": "G2",
    "confidence_score": 80,
    "action_state": "PREPARE",
    "evidence": [{"id": "worker-e1", "source_family": "port.example", "title": "Notice", "url": "https://port.example/1"}],
}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "worker.db", database_url=os.getenv("AURORA_TEST_DATABASE_URL"))
        self.ops = Operations(self.store)
        self.state = WorkerState(self.store)
        self.tables = ["worker_heartbeats", "worker_jobs", "deliveries", "webhooks", "case_notes", "case_incidents", "cases", "notes", "alerts", "timeline", "evidence", "incidents", "watchlists", "api_tokens", "memberships", "users"]
        if self.store.backend == "postgres":
            with self.store.db() as connection:
                for table in self.tables:
                    connection.execute(f"DELETE FROM {table}")
        self.user, self.token = self.store.create_user("worker@example.com", "admin")
        self.context = self.store.auth(self.token)
        self.workspace_id = self.context["workspace_id"]

    def tearDown(self):
        if self.store.backend == "postgres":
            with self.store.db() as connection:
                for table in self.tables:
                    connection.execute(f"DELETE FROM {table}")
        self.temp.cleanup()

    def test_single_owner_and_stale_lease_recovery(self):
        self.state.ensure_job("refresh", 60)
        self.assertTrue(self.state.acquire("refresh", "worker-a", 60))
        self.assertFalse(self.state.acquire("refresh", "worker-b", 60))
        with self.store.db() as connection:
            connection.execute("UPDATE worker_jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE name='refresh'")
        self.assertTrue(self.state.acquire("refresh", "worker-b", 60))

    def test_refresh_queues_new_alert_deliveries(self):
        self.store.add_watchlist(self.user["id"], {"name": "Ports", "query": "port"}, self.workspace_id)
        self.ops.add_webhook(self.user["id"], {"name": "Ops", "url": "https://hooks.example.com/aurora"}, self.workspace_id)
        collector = lambda force=True: {"events": [EVENT]}
        with patch.dict(os.environ, {"AURORA_REFRESH_INTERVAL_SECONDS": "10", "AURORA_DELIVERY_INTERVAL_SECONDS": "10"}, clear=False):
            worker = AuroraWorker(store=self.store, worker_id="worker-test", collector=collector)
        result = worker.refresh()
        self.assertEqual(result["workspaces"], 1)
        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(result["deliveries_queued_for_alerts"], 1)
        self.assertEqual(len(worker.queue.due()), 1)

    def test_retry_backoff_and_dead_letter(self):
        self.store.add_watchlist(self.user["id"], {"name": "Ports", "query": "port"}, self.workspace_id)
        self.ops.add_webhook(self.user["id"], {"name": "Ops", "url": "https://hooks.example.com/aurora"}, self.workspace_id)
        result = self.store.ingest({"events": [EVENT]}, workspace_id=self.workspace_id)
        self.assertEqual(result["created"], 1)
        alert = self.store.alerts(self.user["id"], self.workspace_id)[0]
        self.ops.queue_deliveries(self.user["id"], alert["id"], self.workspace_id)
        queue = DeliveryQueue(self.ops, max_attempts=2, base_backoff=1, max_backoff=2)
        def failing(*args, **kwargs): raise RuntimeError("network down")
        first = queue.deliver_due(opener=failing)
        self.assertEqual(first["failed"], 1)
        with self.store.db() as connection:
            row = connection.execute("SELECT status,next_attempt_at FROM deliveries WHERE workspace_id=?", (self.workspace_id,)).fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIsNotNone(row["next_attempt_at"])
            connection.execute("UPDATE deliveries SET next_attempt_at=? WHERE workspace_id=?", (now(), self.workspace_id))
        second = queue.deliver_due(opener=failing)
        self.assertEqual(second["dead"], 1)
        with self.store.db() as connection:
            row = connection.execute("SELECT status,dead_lettered_at FROM deliveries WHERE workspace_id=?", (self.workspace_id,)).fetchone()
        self.assertEqual(row["status"], "dead")
        self.assertIsNotNone(row["dead_lettered_at"])

    def test_heartbeat_status(self):
        self.state.heartbeat("worker-a")
        status = self.state.status(120)
        self.assertEqual(status["healthy_workers"], 1)
        self.assertTrue(status["workers"][0]["healthy"])


if __name__ == "__main__":
    unittest.main()
