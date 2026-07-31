from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from phase40_markets import MarketObservation
from phase40_verified import (
    ResilientAlphaVantageEquitiesAdapter,
    RevisionAwareMarketStore,
)


class Phase40ReviewRepairTests(unittest.TestCase):
    def test_duplicate_identity_refreshes_latest_revision(self):
        store = RevisionAwareMarketStore(":memory:")
        first = MarketObservation(
            domain="equities",
            provider="fixture",
            instrument="IBM",
            external_id="IBM:2026-07-31T00:00:00Z",
            observed_at="2026-07-31T12:00:00Z",
            event_time="2026-07-31T00:00:00Z",
            value=100.0,
            unit="USD",
            status="quote",
            payload={"price": "100.0"},
            provenance={"source": "fixture"},
        )
        revised = MarketObservation(
            domain="equities",
            provider="fixture",
            instrument="IBM",
            external_id=first.external_id,
            observed_at="2026-07-31T13:00:00Z",
            event_time=first.event_time,
            value=101.5,
            unit="USD",
            status="quote",
            payload={"price": "101.5"},
            provenance={"source": "fixture", "revision": True},
        )

        first_id = store.record(first)
        revised_id = store.record(revised)
        rows = store.observations(domain="equities")

        self.assertEqual(first_id, revised_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 101.5)
        self.assertEqual(rows[0]["observed_at"], revised.observed_at)
        self.assertEqual(rows[0]["payload"], revised.payload)
        self.assertTrue(rows[0]["provenance"]["revision"])

    def test_one_symbol_failure_preserves_other_valid_quotes(self):
        valid = {
            "Global Quote": {
                "01. symbol": "MSFT",
                "05. price": "420.25",
                "07. latest trading day": "2026-07-31",
            }
        }

        def response(url: str, timeout: int = 30):
            if "symbol=IBM" in url:
                raise OSError("fixture request failure")
            return valid

        with patch.dict(
            os.environ,
            {
                "AURORA_ALPHA_VANTAGE_API_KEY": "fixture-secret",
                "AURORA_EQUITY_SYMBOLS": "IBM,MSFT",
            },
            clear=True,
        ), patch("phase40_verified._json", side_effect=response):
            rows = ResilientAlphaVantageEquitiesAdapter().fetch()

        self.assertEqual([row.instrument for row in rows], ["MSFT"])
        self.assertEqual(rows[0].value, 420.25)
        self.assertTrue(rows[0].provenance["partial_success"])
        self.assertEqual(rows[0].provenance["failed_symbols"], ["IBM:request_failed"])
        self.assertNotIn("fixture-secret", str(rows[0].value_dict()))

    def test_all_symbol_failures_fail_provider(self):
        with patch.dict(
            os.environ,
            {
                "AURORA_ALPHA_VANTAGE_API_KEY": "fixture-secret",
                "AURORA_EQUITY_SYMBOLS": "IBM,MSFT",
            },
            clear=True,
        ), patch("phase40_verified._json", side_effect=OSError("fixture request failure")):
            with self.assertRaisesRegex(RuntimeError, "no valid quotes"):
                ResilientAlphaVantageEquitiesAdapter().fetch()


if __name__ == "__main__":
    unittest.main()
