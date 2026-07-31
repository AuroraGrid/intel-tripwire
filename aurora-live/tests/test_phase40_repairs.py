from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from phase40_repairs import (
    ProductionMarketCoordinator,
    ResilientWorldBankCommoditiesAdapter,
    ResilientWorldBankIndicatorsAdapter,
)
from phase40_markets import MarketStore


class Phase40RepairTests(unittest.TestCase):
    def test_pink_sheet_offset_multiline_header(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Monthly Prices"
        sheet.append(["World Bank Commodity Price Data"])
        sheet.append([None, "Date", "Crude oil, Brent", "Gold", "Copper"])
        sheet.append([None, None, "$/bbl", "$/toz", "$/mt"])
        sheet.append([None, None, "OIL_BRENT", "GOLD", "COPPER"])
        sheet.append([None, "2026M05", 70.0, 2300.0, 9500.0])
        sheet.append([None, "2026M06", 72.5, 2400.0, 9700.0])
        buffer = io.BytesIO()
        workbook.save(buffer)

        with patch.dict(
            os.environ,
            {"AURORA_COMMODITY_SERIES": "CRUDE_BRENT,GOLD,COPPER"},
            clear=True,
        ), patch("phase40_repairs._request", return_value=buffer.getvalue()):
            rows = ResilientWorldBankCommoditiesAdapter().fetch()

        by_instrument = {row.instrument: row for row in rows}
        self.assertEqual(by_instrument["OIL_BRENT"].value, 72.5)
        self.assertEqual(by_instrument["GOLD"].unit, "$/toz")
        self.assertEqual(by_instrument["COPPER"].event_time, "2026-06-01T00:00:00Z")
        self.assertEqual(by_instrument["OIL_BRENT"].provenance["date_column_index"], 1)

    def test_pink_sheet_can_infer_date_column_without_date_label(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Monthly Prices"
        sheet.append(["Title"])
        sheet.append([None, None, "OIL_BRENT", "GOLD"])
        sheet.append([None, None, "$/bbl", "$/toz"])
        sheet.append([None, "2026M06", 72.5, 2400.0])
        buffer = io.BytesIO()
        workbook.save(buffer)

        with patch.dict(os.environ, {"AURORA_COMMODITY_SERIES": "CRUDE_BRENT,GOLD"}, clear=True), patch(
            "phase40_repairs._request", return_value=buffer.getvalue()
        ):
            rows = ResilientWorldBankCommoditiesAdapter().fetch()
        self.assertEqual({row.instrument for row in rows}, {"OIL_BRENT", "GOLD"})

    def test_world_bank_indicators_continue_after_scoped_failure(self):
        payload = [
            {"page": 1},
            [
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP"},
                    "country": {"id": "CN", "value": "China"},
                    "countryiso3code": "CHN",
                    "date": "2025",
                    "value": 100.0,
                }
            ],
        ]
        with patch.dict(
            os.environ,
            {
                "AURORA_WORLD_BANK_COUNTRIES": "USA,CHN",
                "AURORA_WORLD_BANK_INDICATORS": "NY.GDP.MKTP.CD",
            },
            clear=True,
        ), patch("phase40_repairs._json", side_effect=[OSError("temporary failure"), payload]) as mocked:
            rows = ResilientWorldBankIndicatorsAdapter().fetch()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].instrument, "CHN:NY.GDP.MKTP.CD")
        self.assertEqual(mocked.call_count, 2)
        self.assertFalse(rows[0].provenance["request_url_persisted"])

    def test_world_bank_indicators_fail_only_when_no_numeric_rows_exist(self):
        with patch.dict(
            os.environ,
            {
                "AURORA_WORLD_BANK_COUNTRIES": "USA",
                "AURORA_WORLD_BANK_INDICATORS": "NY.GDP.MKTP.CD",
            },
            clear=True,
        ), patch("phase40_repairs._json", return_value=[{"page": 1}, []]):
            with self.assertRaisesRegex(RuntimeError, "no numeric observations"):
                ResilientWorldBankIndicatorsAdapter().fetch()

    def test_production_coordinator_installs_repaired_adapters(self):
        coordinator = ProductionMarketCoordinator(MarketStore(":memory:"))
        by_name = {adapter.name: adapter for adapter in coordinator.adapters}
        self.assertIsInstance(by_name["world-bank-pink-sheet"], ResilientWorldBankCommoditiesAdapter)
        self.assertIsInstance(by_name["world-bank-indicators-v2"], ResilientWorldBankIndicatorsAdapter)


if __name__ == "__main__":
    unittest.main()
