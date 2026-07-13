import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from delivery import deliver_pending
from feeds import json_feed, rss_feed
from operations import Operations
from storage import Store


E = {
    "id": "i1",
    "title": "Major port outage",
    "category": "infrastructure",
    "severity": "high",
    "k_align_status": "PLAUSIBLE",
    "confidence_grade": "G2",
    "confidence_score": 76,
    "action_state": "PREPARE",
    "evidence": [
        {"id": "e1", "source_family": "port.example", "title": "Official notice", "url": "https://port.example/1", "official": True},
        {"id": "e2", "source_family": "news.example", "title": "Report", "url": "https://news.example/1"},
    ],
}

TABLES = [
    "worker_heartbeats", "worker_jobs", "deliveries", "webhooks", "case_notes", "case_incidents", "cases",
    "notes", "alerts", "timeline", "evidence", "incidents", "watchlists", "api_tokens", "memberships", "users",
]


class Response:
    status = 204
    def __enter__(self): return self
    def __exit__(self, *args): return False


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "x.db", database_url=os.getenv("AURORA_TEST_DATABASE_URL"))
        self.operations = Operations(self.store)
        if self.store.backend == "postgres": self._clear()
        self.user, self.token = self.store.create_user("a@b.com", "admin")
        self.workspace_id = self.store.auth(self.token)["workspace_id"]

    def tearDown(self):
        if self.store.backend == "postgres": self._clear()
        self.temp.cleanup()

    def _clear(self):
        with self.store.db() as connection:
            for table in TABLES:
                if self.store.database.table_exists(table):
                    connection.execute(f"DELETE FROM {table}")

    def ingest(self, event=None):
        result = self.store.ingest({"events": [event or E]}, workspace_id=self.workspace_id, actor_user_id=self.user["id"])
        return result, result["incident_ids"][0]

    def test_auth_and_role_validation(self):
        authenticated = self.store.auth(self.token)
        self.assertEqual(authenticated["email"], "a@b.com")
        self.assertEqual(authenticated["workspace_role"], "owner")
        self.assertIn("owner", authenticated["permissions"])
        self.assertIsNone(self.store.auth("x"))
        with self.assertRaises(ValueError): self.store.create_user("other@example.com", "owner")

    def test_ingest_graph_timeline_filters(self):
        result, incident_id = self.ingest()
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(self.store.incident(incident_id, workspace_id=self.workspace_id)["evidence"]), 2)
        self.assertEqual(len(self.store.graph(incident_id, workspace_id=self.workspace_id)["edges"]), 4)
        self.assertEqual(self.store.timeline(incident_id, self.workspace_id)[0]["event_type"], "DETECTED")
        self.assertEqual(len(self.operations.incidents(category="infrastructure", min_confidence=75, workspace_id=self.workspace_id)), 1)
        self.assertEqual(self.operations.incidents(category="conflict", workspace_id=self.workspace_id), [])

    def test_claim_id_is_accepted(self):
        event = dict(E); event.pop("id"); event["claim_id"] = "claim-1"
        _, incident_id = self.ingest(event)
        self.assertEqual(self.store.incident(incident_id, workspace_id=self.workspace_id)["payload"]["source_id"], "claim-1")

    def test_watch_alert_ack_once(self):
        self.store.add_watchlist(self.user["id"], {"name":"Ports","query":"port","categories":["infrastructure"],"severities":["high"],"min_confidence":70}, self.workspace_id)
        first, _ = self.ingest()
        second, _ = self.ingest()
        self.assertEqual(first["alerts_created"], 1)
        self.assertEqual(second["alerts_created"], 0)
        alert = self.operations.alerts(self.user["id"], workspace_id=self.workspace_id)[0]
        self.operations.acknowledge_alert(self.user["id"], alert["id"], self.workspace_id)
        self.assertEqual(self.operations.alerts(self.user["id"], True, self.workspace_id), [])

    def test_change_note_case(self):
        _, incident_id = self.ingest()
        self.ingest(dict(E, severity="critical", confidence_score=95))
        self.store.add_note(incident_id, self.user["id"], "Verify rail impact", self.workspace_id)
        case = self.operations.create_case(self.user["id"], {"title":"Port disruption","priority":"high"}, self.workspace_id)
        self.operations.add_case_incident(self.user["id"], case["id"], incident_id, self.workspace_id)
        self.operations.add_case_note(self.user["id"], case["id"], "Contact logistics desk", self.workspace_id)
        case = self.operations.case(self.user["id"], case["id"], self.workspace_id)
        self.assertEqual(len(case["incidents"]), 1)
        self.assertEqual(len(case["notes"]), 1)
        self.assertEqual([item["event_type"] for item in self.store.timeline(incident_id, self.workspace_id)], ["DETECTED", "ASSESSMENT_CHANGED", "ANALYST_NOTE"])

    def test_webhook_delivery_guard(self):
        with self.assertRaises(ValueError): self.operations.add_webhook(self.user["id"], {"name":"bad","url":"http://127.0.0.1/x"}, self.workspace_id)
        self.operations.add_webhook(self.user["id"], {"name":"Ops","url":"https://hooks.example.com/aurora"}, self.workspace_id)
        self.store.add_watchlist(self.user["id"], {"name":"Ports","query":"port"}, self.workspace_id)
        self.ingest()
        alert = self.store.alerts(self.user["id"], self.workspace_id)[0]
        self.operations.queue_deliveries(self.user["id"], alert["id"], self.workspace_id)
        seen = {}
        def opener(request, timeout=0): seen["body"] = json.loads(request.data.decode()); return Response()
        self.assertEqual(deliver_pending(self.operations, self.user["id"], opener=opener), {"attempted":1,"delivered":1,"failed":0})
        self.assertEqual(seen["body"]["alert"]["status"], "PLAUSIBLE")
        self.assertEqual(self.operations.pending_deliveries(self.user["id"], workspace_id=self.workspace_id), [])

    def test_feeds_stats(self):
        _, incident_id = self.ingest()
        items = self.operations.incidents(workspace_id=self.workspace_id)
        feed = json.loads(json_feed(items, "https://a.example"))
        self.assertEqual(feed["items"][0]["id"], incident_id)
        self.assertIn("/api/platform/incidents/", feed["items"][0]["url"])
        self.assertIn('<rss version="2.0">', rss_feed(items, "https://a.example").decode())
        self.assertEqual(self.operations.stats(self.user["id"], self.workspace_id)["incidents"], 1)


if __name__ == "__main__": unittest.main()
