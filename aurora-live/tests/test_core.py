import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AURORA_OFFLINE"] = "1"

from app import assess_cluster, cluster_evidence, host_family, load_fixtures  # noqa: E402


class CoreTests(unittest.TestCase):
    def test_source_family(self):
        self.assertEqual(host_family("https://www.bbc.co.uk/news/world"), "bbc.co.uk")
        self.assertEqual(host_family("https://m.reuters.com/a"), "reuters.com")

    def test_official_record_is_g3_supported(self):
        official = next(item for item in load_fixtures() if item.official)
        claim = assess_cluster([official])
        self.assertEqual(claim.k_align_status, "SUPPORTED")
        self.assertEqual(claim.confidence_grade, "G3")

    def test_two_independent_news_sources_are_plausible(self):
        items = [item for item in load_fixtures() if "port" in item.title.lower()]
        claim = assess_cluster(max(cluster_evidence(items), key=len))
        self.assertEqual(claim.independent_origins, 2)
        self.assertEqual(claim.k_align_status, "PLAUSIBLE")
        self.assertEqual(claim.confidence_grade, "G2")

    def test_one_social_claim_is_not_proven(self):
        item = next(item for item in load_fixtures() if item.evidence_type == "social_claim")
        claim = assess_cluster([item])
        self.assertEqual(claim.k_align_status, "NOT_PROVEN")
        self.assertEqual(claim.confidence_grade, "G1")
        self.assertEqual(claim.action_state, "INVESTIGATE")


if __name__ == "__main__":
    unittest.main()
