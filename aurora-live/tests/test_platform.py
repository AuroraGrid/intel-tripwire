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

TABLES = ["deliveries","webhooks","case_notes","case_incidents","cases","notes","alerts","timeline","evidence","incidents","watchlists","users"]


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

    def tearDown(self):
        if self.store.backend == "postgres": self._clear()
        self.temp.cleanup()

    def _clear(self):
        with self.store.db() as connection:
            for table in TABLES: connection.execute(f"DELETE FROM {table}")

    def test_auth_and_role_validation(self):
        self.assertEqual(self.store.auth(self.token)["email"], "a@b.com")
        self.assertIsNone(self.store.auth("x"))
        with self.assertRaises(ValueError): self.store.create_user("other@example.com", "owner")

    def test_ingest_graph_timeline_filters(self):
        result = self.store.ingest({"events": [E]})
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(self.store.incident("i1")["evidence"]), 2)
        self.assertEqual(len(self.store.graph("i1")["edges"]), 4)
        self.assertEqual(self.store.timeline("i1")[0]["event_type"], "DETECTED")
        self.assertEqual(len(self.operations.incidents(category="infrastructure", min_confidence=75)), 1)
        self.assertEqual(self.operations.incidents(category="conflict"), [])

    def test_claim_id_is_accepted(self):
        event = dict(E); event.pop("id"); event["claim_id"] = "claim-1"
        self.store.ingest({"events": [event]})
        self.assertEqual(self.store.incident("claim-1")["title"], E["title"])

    def test_watch_alert_ack_once(self):
        self.store.add_watchlist(self.user["id"], {"name":"Ports","query":"port","categories":["infrastructure"],"severities":["high"],"min_confidence":70})
        self.assertEqual(self.store.ingest({"events":[E]})["alerts_created"],1)
        self.assertEqual(self.store.ingest({"events":[E]})["alerts_created"],0)
        alert=self.operations.alerts(self.user["id"])[0]
        self.operations.acknowledge_alert(self.user["id"],alert["id"])
        self.assertEqual(self.operations.alerts(self.user["id"],True),[])

    def test_change_note_case(self):
        self.store.ingest({"events":[E]});self.store.ingest({"events":[dict(E,severity="critical",confidence_score=95)]})
        self.store.add_note("i1",self.user["id"],"Verify rail impact")
        case=self.operations.create_case(self.user["id"],{"title":"Port disruption","priority":"high"})
        self.operations.add_case_incident(self.user["id"],case["id"],"i1")
        self.operations.add_case_note(self.user["id"],case["id"],"Contact logistics desk")
        case=self.operations.case(self.user["id"],case["id"])
        self.assertEqual(len(case["incidents"]),1);self.assertEqual(len(case["notes"]),1)
        self.assertEqual([item["event_type"] for item in self.store.timeline("i1")],["DETECTED","ASSESSMENT_CHANGED","ANALYST_NOTE"])

    def test_webhook_delivery_guard(self):
        with self.assertRaises(ValueError): self.operations.add_webhook(self.user["id"],{"name":"bad","url":"http://127.0.0.1/x"})
        self.operations.add_webhook(self.user["id"],{"name":"Ops","url":"https://hooks.example.com/aurora"})
        self.store.add_watchlist(self.user["id"],{"name":"Ports","query":"port"});self.store.ingest({"events":[E]})
        alert=self.store.alerts(self.user["id"])[0];self.operations.queue_deliveries(self.user["id"],alert["id"]);seen={}
        def opener(request,timeout=0): seen["body"]=json.loads(request.data.decode());return Response()
        self.assertEqual(deliver_pending(self.operations,self.user["id"],opener=opener),{"attempted":1,"delivered":1,"failed":0})
        self.assertEqual(seen["body"]["alert"]["status"],"PLAUSIBLE")
        self.assertEqual(self.operations.pending_deliveries(self.user["id"]),[])

    def test_feeds_stats(self):
        self.store.ingest({"events":[E]});items=self.operations.incidents();feed=json.loads(json_feed(items,"https://a.example"))
        self.assertEqual(feed["items"][0]["id"],"i1");self.assertIn("/api/platform/incidents/i1",feed["items"][0]["url"])
        self.assertIn('<rss version="2.0">',rss_feed(items,"https://a.example").decode())
        self.assertEqual(self.operations.stats(self.user["id"])["incidents"],1)


if __name__ == "__main__": unittest.main()
