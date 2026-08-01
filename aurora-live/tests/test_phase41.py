from __future__ import annotations

import unittest

from phase40_complete import Phase40Application
from phase41_complete import Phase41Application
from phase41_media import MediaVerifier, MediaStore, average_hash
from phase41_replay import ReplayRecord, ReplayStore, _now


class Phase41Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase41Application, Phase40Application))

    def test_replay_is_idempotent_and_ordered(self):
        store = ReplayStore(":memory:")
        store.ingest(
            ReplayRecord("markets", "markets:a", "2026-07-31T10:00:00Z", "A", "", "", {}, {})
        )
        store.ingest(
            ReplayRecord("transport", "transport:b", "2026-07-31T11:00:00Z", "B", "", "", {}, {})
        )
        store.ingest(
            ReplayRecord("markets", "markets:a", "2026-07-31T10:00:00Z", "A", "", "", {}, {})
        )
        rows = store.query(limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["record_id"], "transport:b")
        coverage = store.coverage()
        self.assertEqual(coverage["total"], 2)

    def test_media_hash_and_duplicate(self):
        store = MediaStore(":memory:")
        verifier = MediaVerifier(store)
        data = b"\xff\xd8\xff" + b"ABCDEFGH" * 32
        first = verifier.verify_bytes(data, source_url="https://example.invalid/a.jpg", content_type="image/jpeg")
        second = verifier.verify_bytes(data, source_url="https://example.invalid/b.jpg", content_type="image/jpeg")
        self.assertEqual(first["verification_state"], "HASHED")
        self.assertEqual(second["verification_state"], "DUPLICATE_OF")
        self.assertFalse(first["detail"]["authenticity_claim"])
        self.assertEqual(average_hash(data), first["perceptual_hash"])

    def test_media_rejects_oversized(self):
        store = MediaStore(":memory:")
        verifier = MediaVerifier(store)
        data = b"x" * (MediaVerifier.MAX_BYTES + 1)
        result = verifier.verify_bytes(data, source_url="https://example.invalid/big.bin")
        self.assertEqual(result["verification_state"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
