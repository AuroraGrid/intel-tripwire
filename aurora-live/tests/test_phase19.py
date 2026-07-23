import base64
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase19_ai import GroqPublicEvidenceAssistant
from phase19_verification import MultimodalVerification
from storage import Store


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nXQAAAAASUVORK5CYII="
)


class Phase19Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase19.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.media = MultimodalVerification(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def register(self, **overrides):
        payload = {
            "media_type": "IMAGE",
            "mime_type": "image/png",
            "classification": "PUBLIC",
            "original_filename": "evidence.png",
            "source_url": "https://example.com/evidence.png",
            "content_base64": base64.b64encode(PNG_1X1).decode("ascii"),
            "metadata": {"acquisition": "test fixture"},
        }
        payload.update(overrides)
        return self.media.register_asset(self.actor, payload)

    def test_registers_hash_signature_metadata_and_revision(self):
        asset = self.register()
        self.assertEqual(asset["media_type"], "IMAGE")
        self.assertEqual(asset["mime_type"], "image/png")
        self.assertEqual(asset["status"], "READY_FOR_REVIEW")
        check_types = {item["check_type"] for item in asset["checks"]}
        self.assertTrue({"CRYPTOGRAPHIC_HASH", "FILE_SIGNATURE"}.issubset(check_types))
        self.assertEqual(self.media.revisions(self.actor, asset["id"])[0]["action"], "CREATED")

    def test_exact_duplicate_is_idempotent(self):
        first = self.register()
        second = self.register()
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(self.media.revisions(self.actor, first["id"])), 1)
        self.assertEqual(len(self.media.list_assets(self.actor)), 1)

    def test_declared_mime_mismatch_is_visible(self):
        asset = self.register(mime_type="image/jpeg")
        signature = next(
            item for item in asset["checks"] if item["check_type"] == "FILE_SIGNATURE"
        )
        self.assertEqual(signature["state"], "WARNING")
        self.assertIn("differs", signature["uncertainty"])

    def test_model_output_remains_inference(self):
        asset = self.register()
        check = self.media.record_check(self.actor, asset["id"], {
            "check_type": "GEOLOCATION",
            "result_kind": "INFERENCE",
            "producer": "LOCAL_MODEL",
            "model": "local-vision",
            "model_version": "1",
            "state": "UNRESOLVED",
            "inference": "Architecture may be consistent with a coastal region.",
            "uncertainty": "No unique landmark is visible.",
            "falsifiers": ["A verified location outside the candidate region"],
            "output": {"candidate_regions": ["coastal-region"]},
        })
        self.assertEqual(check["result_kind"], "INFERENCE")
        self.assertEqual(check["producer"], "LOCAL_MODEL")
        self.assertEqual(check["observation"], "")
        self.assertEqual(self.media.asset(self.actor, asset["id"])["review_state"], "UNREVIEWED")

    def test_unapproved_cloud_providers_are_rejected(self):
        asset = self.register()
        for provider in ("OPENAI", "XAI", "GEMINI"):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError):
                    self.media.record_check(self.actor, asset["id"], {
                        "check_type": "CONTEXT",
                        "result_kind": "INFERENCE",
                        "producer": provider,
                        "model": "model",
                        "inference": "result",
                    })

    def test_analyst_review_is_append_only_and_not_authenticity_label(self):
        asset = self.register()
        reviewed = self.media.review(self.actor, asset["id"], {
            "review_state": "CONSISTENT_WITH_EVIDENCE",
            "rationale": "Hash, source archive, and visible details are mutually consistent.",
        })
        self.assertEqual(reviewed["review_state"], "CONSISTENT_WITH_EVIDENCE")
        self.assertNotIn("AUTHENTIC", reviewed["review_state"])
        revisions = self.media.revisions(self.actor, asset["id"])
        self.assertEqual([item["revision_number"] for item in revisions], [1, 2])
        self.assertEqual(revisions[-1]["action"], "ANALYST_REVIEW")

    def test_derivatives_and_links_preserve_provenance(self):
        asset = self.register()
        derivative = self.media.add_derivative(self.actor, asset["id"], {
            "derivative_kind": "KEYFRAME",
            "sequence": 4,
            "content_base64": base64.b64encode(b"frame-four").decode("ascii"),
            "mime_type": "image/jpeg",
            "metadata": {"timestamp_seconds": 12.5},
        })
        link = self.media.link(self.actor, asset["id"], {
            "resource_type": "claim",
            "resource_id": "claim-123",
            "relation_type": "SUPPORTS",
        })
        self.assertEqual(derivative["sequence"], 4)
        self.assertEqual(link["resource_id"], "claim-123")
        loaded = self.media.asset(self.actor, asset["id"])
        self.assertEqual(len(loaded["derivatives"]), 1)
        self.assertEqual(len(loaded["links"]), 1)

    def test_workspace_isolation(self):
        asset = self.register()
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        with self.assertRaises(KeyError):
            self.media.asset(other, asset["id"])
        self.assertEqual(self.media.list_assets(other), [])

    def test_free_ai_quota_is_workspace_scoped_and_bounded(self):
        first = self.media.reserve_ai(self.actor, "GROQ", 1)
        self.assertEqual(first["requests"], 1)
        with self.assertRaises(ValueError):
            self.media.reserve_ai(self.actor, "GROQ", 1)
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        self.assertEqual(self.media.reserve_ai(other, "GROQ", 1)["requests"], 1)

    def test_groq_is_disabled_without_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            assistant = GroqPublicEvidenceAssistant(self.media)
        self.assertFalse(assistant.status()["enabled"])
        self.assertFalse(assistant.status()["configured"])

    def test_scorecard_states_no_automatic_authenticity(self):
        self.register()
        scorecard = self.media.scorecard(self.actor)
        self.assertEqual(scorecard["phase"], 19)
        self.assertIn("No automated result", scorecard["authenticity_rule"])


if __name__ == "__main__":
    unittest.main()
