import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase14_integrity import EvidenceIntegrity
from storage import Store


class Phase14Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase14.db")
        _, token = self.store.create_user(f"admin-{uuid.uuid4().hex}@example.com", "admin")
        self.actor = self.store.auth(token)
        self.engine = EvidenceIntegrity(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, name, tier, lineage=None, reliability=1.0):
        return self.engine.register_source(self.actor, {
            "name": name,
            "canonical_url": f"https://{name.lower().replace(' ', '')}.example",
            "source_tier": tier,
            "lineage_key": lineage or name.lower(),
            "reliability": reliability,
        })

    def test_independent_origins_support_claim(self):
        claim = self.engine.create_claim(self.actor, {"statement": "Port operations were suspended.", "claim_type": "FACT", "falsifier": "Official port notice showing normal operations."})
        first = self.source("Port Authority", 1)
        second = self.source("Wire Service", 2)
        for source, text in ((first, "Port authority suspends all operations"), (second, "Independent reporting confirms port closure")):
            self.engine.add_evidence(self.actor, claim["id"], {"source_origin_id": source["id"], "title": text, "text": text, "relation": "SUPPORTS"})
        result = self.engine.assessment(self.actor, claim["id"])
        self.assertEqual(result["status"], "SUPPORTED")
        self.assertEqual(result["origin_count"], 2)
        self.assertIn(result["grade"], {"G2", "G3"})

    def test_copied_reports_count_once(self):
        claim = self.engine.create_claim(self.actor, {"statement": "A convoy crossed the border.", "claim_type": "UNVERIFIED_CLAIM"})
        source = self.source("Original Reporter", 3, "origin-a")
        mirror = self.source("Mirror Outlet", 3, "origin-a")
        text = "A convoy crossed the border at dawn"
        first = self.engine.add_evidence(self.actor, claim["id"], {"source_origin_id": source["id"], "title": text, "text": text})
        second = self.engine.add_evidence(self.actor, claim["id"], {"source_origin_id": mirror["id"], "title": text, "text": text})
        result = self.engine.assessment(self.actor, claim["id"])
        self.assertIsNone(first["duplicate_of"])
        self.assertEqual(second["relation"], "DUPLICATES")
        self.assertEqual(result["origin_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertNotEqual(result["status"], "SUPPORTED")

    def test_contradiction_forces_review_queue(self):
        claim = self.engine.create_claim(self.actor, {"statement": "The airport is open.", "claim_type": "FACT"})
        official = self.source("Civil Aviation Authority", 1)
        evidence = self.engine.add_evidence(self.actor, claim["id"], {"source_origin_id": official["id"], "title": "Official closure notice", "text": "Airport closed until further notice", "relation": "CONTRADICTS", "severity": "critical"})
        result = self.engine.assessment(self.actor, claim["id"])
        queue = self.engine.contradictions(self.actor)
        self.assertEqual(result["status"], "DISPUTED")
        self.assertEqual(queue[0]["evidence_id"], evidence["id"])
        resolved = self.engine.resolve_contradiction(self.actor, queue[0]["id"], "Validated official correction")
        self.assertEqual(resolved["state"], "RESOLVED")

    def test_revision_ledger_is_append_only(self):
        claim = self.engine.create_claim(self.actor, {"statement": "Power was restored.", "claim_type": "FACT"})
        source = self.source("Grid Operator", 1)
        self.engine.add_evidence(self.actor, claim["id"], {"source_origin_id": source["id"], "title": "Restoration bulletin", "text": "Power restored", "relation": "SUPPORTS"})
        self.engine.assessment(self.actor, claim["id"])
        revisions = self.engine.revisions(self.actor, claim["id"])
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["from_status"], "NOT_PROVEN")
        self.assertIn(revisions[0]["to_status"], {"PLAUSIBLE", "SUPPORTED"})


if __name__ == "__main__":
    unittest.main()
