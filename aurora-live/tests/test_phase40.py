from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from phase39_complete import Phase39Application
from phase40_complete import Phase40Application
from phase40_markets import (
    AlphaVantageEquitiesAdapter,
    CoinbaseCryptoAdapter,
    DOMAINS,
    ECBFXAdapter,
    EIAEnergyAdapter,
    KalshiPredictionMarketsAdapter,
    MarketCoordinator,
    MarketObservation,
    MarketStore,
    ProviderRun,
    WorldBankCommoditiesAdapter,
    WorldBankIndicatorsAdapter,
    _now,
)


class Phase40Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase40Application, Phase39Application))

    def test_registration_never_qualifies_market_domains(self):
        with patch.dict(os.environ, {}, clear=True):
            store = MarketStore(":memory:")
            coordinator = MarketCoordinator(store)
        self.assertEqual(len(coordinator.adapters), 7)
        self.assertEqual({row["domain"] for row in store.providers()}, set(DOMAINS))
        self.assertEqual(store.coverage()["qualified_domains"], 0)
        self.assertFalse(store.coverage()["fully_qualified"])

    def test_observation_identity_is_idempotent(self):
        store = MarketStore(":memory:")
        observation = MarketObservation(
            domain="crypto",
            provider="fixture",
            instrument="BTC-USD",
            external_id="BTC-USD:1",
            observed_at=_now(),
            event_time=_now(),
            value=100.0,
            unit="USD",
            status="quote",
            payload={"fixture": True},
            provenance={"source": "test"},
        )
        first = store.record(observation)
        second = store.record(observation)
        self.assertEqual(first, second)
        self.assertEqual(len(store.observations("crypto")), 1)

    def test_alpha_vantage_is_scoped_and_secret_safe(self):
        secret = "alpha-secret-fixture"
        payload = {"Global Quote": {"01. symbol": "IBM", "05. price": "123.45", "07. latest trading day": "2026-07-30"}}
        with patch.dict(
            os.environ,
            {"AURORA_ALPHA_VANTAGE_API_KEY": secret, "AURORA_EQUITY_SYMBOLS": "IBM"},
            clear=True,
        ), patch("phase40_markets._json", return_value=payload) as mocked:
            adapter = AlphaVantageEquitiesAdapter()
            rows = adapter.fetch()
            configuration = adapter.configuration()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].instrument, "IBM")
        self.assertEqual(rows[0].value, 123.45)
        self.assertIn(secret, mocked.call_args.args[0])
        self.assertNotIn(secret, json.dumps(rows[0].value_dict()))
        self.assertNotIn(secret, json.dumps(configuration))

    def test_eia_is_scoped_and_secret_safe(self):
        secret = "eia-secret-fixture"
        payload = {
            "response": {
                "data": [
                    {
                        "period": "2026-05",
                        "stateid": "CO",
                        "sectorid": "RES",
                        "price": "14.20",
                        "price-units": "cents per kilowatthour",
                    }
                ]
            }
        }
        with patch.dict(os.environ, {"AURORA_EIA_API_KEY": secret}, clear=True), patch(
            "phase40_markets._json", return_value=payload
        ) as mocked:
            rows = EIAEnergyAdapter().fetch()
        self.assertEqual(rows[0].domain, "energy")
        self.assertEqual(rows[0].value, 14.2)
        self.assertIn(secret, mocked.call_args.args[0])
        self.assertNotIn(secret, json.dumps(rows[0].value_dict()))

    def test_world_bank_commodity_workbook_parser(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Monthly Prices"
        sheet.append(["World Bank Commodity Price Data"])
        sheet.append(["Date", "CRUDE_BRENT", "GOLD"])
        sheet.append(["", "$/bbl", "$/toz"])
        sheet.append(["2026M06", 72.5, 2400.0])
        buffer = io.BytesIO()
        workbook.save(buffer)
        with patch("phase40_markets._request", return_value=buffer.getvalue()):
            rows = WorldBankCommoditiesAdapter().fetch()
        by_instrument = {row.instrument: row for row in rows}
        self.assertEqual(by_instrument["CRUDE_BRENT"].value, 72.5)
        self.assertEqual(by_instrument["GOLD"].unit, "$/toz")
        self.assertEqual(by_instrument["GOLD"].event_time, "2026-06-01T00:00:00Z")

    def test_ecb_csv_parser(self):
        content = (
            "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\n"
            "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-07-30,1.17\n"
        ).encode()
        with patch("phase40_markets._request", return_value=content):
            rows = ECBFXAdapter().fetch()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].instrument, "EUR/USD")
        self.assertEqual(rows[0].value, 1.17)

    def test_coinbase_ticker_parser(self):
        payload = {"price": "65000.25", "time": "2026-07-31T10:00:00Z", "bid": "65000", "ask": "65001"}
        with patch.dict(os.environ, {"AURORA_COINBASE_PRODUCTS": "BTC-USD"}, clear=True), patch(
            "phase40_markets._json", return_value=payload
        ):
            rows = CoinbaseCryptoAdapter().fetch()
        self.assertEqual(rows[0].instrument, "BTC-USD")
        self.assertEqual(rows[0].unit, "USD")
        self.assertEqual(rows[0].value, 65000.25)

    def test_world_bank_indicator_parser(self):
        payload = [
            {"page": 1},
            [
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP"},
                    "country": {"id": "US", "value": "United States"},
                    "countryiso3code": "USA",
                    "date": "2025",
                    "value": 100.0,
                }
            ],
        ]
        with patch.dict(
            os.environ,
            {"AURORA_WORLD_BANK_COUNTRIES": "USA", "AURORA_WORLD_BANK_INDICATORS": "NY.GDP.MKTP.CD"},
            clear=True,
        ), patch("phase40_markets._json", return_value=payload):
            rows = WorldBankIndicatorsAdapter().fetch()
        self.assertEqual(rows[0].instrument, "US:NY.GDP.MKTP.CD")
        self.assertEqual(rows[0].value, 100.0)

    def test_kalshi_market_parser(self):
        payload = {
            "markets": [
                {
                    "ticker": "TEST-YES",
                    "title": "Fixture market",
                    "status": "open",
                    "last_price_dollars": "0.61",
                    "updated_time": "2026-07-31T10:00:00Z",
                }
            ]
        }
        with patch("phase40_markets._json", return_value=payload):
            rows = KalshiPredictionMarketsAdapter().fetch()
        self.assertEqual(rows[0].instrument, "TEST-YES")
        self.assertEqual(rows[0].value, 0.61)
        self.assertEqual(rows[0].domain, "prediction_markets")

    def test_unconfigured_credentialed_provider_stays_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            store = MarketStore(":memory:")
            coordinator = MarketCoordinator(store)
            result = coordinator.run("alpha-vantage-global-quote")
        self.assertFalse(result.configured)
        self.assertFalse(result.successful)
        provider = next(row for row in store.providers() if row["provider"] == result.provider)
        self.assertEqual(provider["state"], "NOT_CONFIGURED")
        self.assertEqual(provider["consecutive_failures"], 0)

    def test_all_domains_require_fresh_durable_success(self):
        store = MarketStore(":memory:")
        now = _now()
        for domain in DOMAINS:
            provider = f"{domain}-fixture"
            store.upsert_provider(
                {
                    "provider": provider,
                    "domain": domain,
                    "state": "ONLINE",
                    "last_attempt_at": now,
                    "last_success_at": now,
                    "consecutive_failures": 0,
                    "event_age_seconds": 0,
                    "last_error": "",
                    "completeness_note": "test only",
                    "license_note": "test only",
                }
            )
            store.record(
                MarketObservation(
                    domain=domain,
                    provider=provider,
                    instrument=f"{domain}-instrument",
                    external_id=f"{domain}-1",
                    observed_at=now,
                    event_time=now,
                    value=1.0,
                    unit="test",
                    status="test",
                    payload={},
                    provenance={"source": "test"},
                )
            )
            store.record_run(ProviderRun(provider, domain, True, True, 1, started_at=now, completed_at=now))
        coverage = store.coverage()
        self.assertEqual(coverage["qualified_domains"], 7)
        self.assertTrue(coverage["fully_qualified"])


if __name__ == "__main__":
    unittest.main()
