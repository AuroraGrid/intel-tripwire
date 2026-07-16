import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase11_outputs import SystemOutputStore
from storage import Store


class Phase11OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase11b.db")
        _, token = self.store.create_user(f"analyst-{uuid.uuid4().hex}@example.com", "analyst")
        self.actor = self.store.auth(token)
        self.outputs = SystemOutputStore(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_persist_system_output_and_reviews(self):
        created = self.outputs.create(self.actor, {
            "module": "K-ALIGN",
            "subject_type": "incident",
            "subject_id": "incident-1",
            "summary": "Primary record supports the claim",
            "status": "supported",
            "payload": {"confidence": 0.82, "gate": "G2"},
            "evidence_links": [{"evidence_id": "e1", "relation": "supports"}],
            "assumptions": ["record is authentic"],
            "constraints": ["single official source"],
            "falsifiers": ["official retraction"],
        })
        self.assertEqual(created["module"], "K_ALIGN")
        self.assertEqual(created["payload"]["gate"], "G2")
        reviewed = self.outputs.review(self.actor, created["id"], {
            "review_kind": "red_team",
            "decision": "revise",
            "notes": "Add an independent source",
            "proposed_changes": {"confidence": 0.7},
        })
        self.assertEqual(len(reviewed["reviews"]), 1)
        self.assertEqual(reviewed["reviews"][0]["decision"], "revise")

    def test_all_phase11b_modules_are_supported(self):
        modules = ["AURORA_GRID", "K_ALIGN", "CRF", "IPR", "BLACKGLASS", "COMMAND", "AAIK"]
        for module in modules:
            self.outputs.create(self.actor, {"module": module, "subject_type": "case", "subject_id": module, "summary": module, "payload": {}})
        rows = self.outputs.list(self.actor, limit=20)
        self.assertEqual({row["module"] for row in rows}, set(modules))

    def test_invalid_module_and_review_are_rejected(self):
        with self.assertRaises(ValueError):
            self.outputs.create(self.actor, {"module": "UNKNOWN", "subject_type": "case", "summary": "bad"})
        created = self.outputs.create(self.actor, {"module": "CRF", "subject_type": "case", "summary": "constraint assessment"})
        with self.assertRaises(ValueError):
            self.outputs.review(self.actor, created["id"], {"review_kind": "peer", "decision": "affirm"})


if __name__ == "__main__":
    unittest.main()
