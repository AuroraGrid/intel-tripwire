from __future__ import annotations

import unittest

from phase40_markets import MarketStore
from phase40_verified import NativeCodeWorldBankCommoditiesAdapter, VerifiedMarketCoordinator


class Phase40VerifiedTests(unittest.TestCase):
    def test_native_code_wins_over_shared_unit_row(self):
        labels = ["Crude oil, Brent", "OIL_BRENT", "$/bbl"]
        self.assertEqual(NativeCodeWorldBankCommoditiesAdapter._instrument(labels, 2), "OIL_BRENT")

    def test_native_non_default_code_wins_over_unit(self):
        labels = ["Wheat, U.S., HRW", "iWHEAT_US_HRW", "$/mt"]
        self.assertEqual(NativeCodeWorldBankCommoditiesAdapter._instrument(labels, 3), "IWHEAT_US_HRW")

    def test_unit_only_label_falls_back_without_collision_prone_symbol(self):
        self.assertEqual(NativeCodeWorldBankCommoditiesAdapter._instrument(["$/mt"], 7), "SERIES_7")

    def test_verified_coordinator_installs_native_code_adapter(self):
        coordinator = VerifiedMarketCoordinator(MarketStore(":memory:"))
        adapter = next(row for row in coordinator.adapters if row.name == "world-bank-pink-sheet")
        self.assertIsInstance(adapter, NativeCodeWorldBankCommoditiesAdapter)


if __name__ == "__main__":
    unittest.main()
