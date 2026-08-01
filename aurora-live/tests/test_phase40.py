from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from phase39_complete import Phase39Application
from phase40_complete import Phase40Application
from phase40_markets import (
    CoinGeckoCryptoAdapter,
    FrankfurterFXAdapter,
    MarketCoordinator,
    MarketObservation,
    MarketStore,
    ProviderRun,
    LAYERS,
    _now,
)


class Phase40Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase40Application, Phase39Application))

    def test_registration_never_qualifies_a_layer(self):
        store = MarketStore(":memory:")
        coordinator = MarketCoordinator(store)
        self.assertEqual(len(coordinator.adapters), 7)
        self.assertEqual({row["layer"] for row in store.providers()}, set(LAYERS))
        self.assertEqual(store.coverage()["qualified_layers"], 0)
        self.assertFalse(store.coverage()["fully_qualified"])

    def test_observation_updates_append_time_series_snapshots(self):
        store = MarketStore(":memory:")
        observation = MarketObservation(
            layer="crypto",
            provider="fixture",
            external_id="btc",
            observed_at=_now(),
            event_time=_now(),
            symbol="BTC",
            title="Bitcoin",
            value=1.0,
            currency="USD",
            unit="price",
            source_url="https://example.invalid",
            payload={"fixture": True},
            provenance={"source": "test"},
        )
        first = store.record(observation)
        second = store.record(observation)
        self.assertNotEqual(first, second)
        rows = store.observations("crypto")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["external_id"] for row in rows}, {"btc"})

    def test_coingecko_adapter_parses_markets(self):
        payload = [
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 100.0,
            }
        ]
        with patch("phase40_markets._json", return_value=payload):
            rows = CoinGeckoCryptoAdapter().fetch()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].layer, "crypto")
        self.assertEqual(rows[0].value, 100.0)

    def test_frankfurter_adapter_parses_rates(self):
        payload = {"base": "EUR", "date": "2026-07-31", "rates": {"USD": 1.1, "GBP": 0.85}}
        with patch("phase40_markets._json", return_value=payload):
            rows = FrankfurterFXAdapter().fetch()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].layer, "currencies")

    def test_unconfigured_energy_stays_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            store = MarketStore(":memory:")
            coordinator = MarketCoordinator(store)
            result = coordinator.run("configured-energy-feed")
        self.assertFalse(result.configured)
        self.assertFalse(result.successful)
        provider = next(row for row in store.providers() if row["provider"] == result.provider)
        self.assertEqual(provider["state"], "NOT_CONFIGURED")

    def test_configuration_never_returns_secret_value(self):
        store = MarketStore(":memory:")
        coordinator = MarketCoordinator(store)
        with patch.dict(
            os.environ,
            {
                "AURORA_ENERGY_FEED_URL": "https://example.invalid/data?key={api_key}",
                "AURORA_ENERGY_API_KEY": "super-secret-fixture",
                "AURORA_FRED_API_KEY": "fred-secret",
            },
            clear=True,
        ):
            value = coordinator.configuration()
        encoded = json.dumps(value)
        self.assertNotIn("super-secret-fixture", encoded)
        self.assertNotIn("fred-secret", encoded)

    def test_layer_requires_fresh_durable_success(self):
        store = MarketStore(":memory:")
        now = _now()
        store.upsert_provider(
            {
                "provider": "crypto-fixture",
                "layer": "crypto",
                "state": "ONLINE",
                "last_attempt_at": now,
                "last_success_at": now,
                "consecutive_failures": 0,
                "freshness_seconds": 0,
                "last_error": "",
                "completeness_note": "test",
                "license_note": "test",
            }
        )
        store.record(
            MarketObservation(
                layer="crypto",
                provider="crypto-fixture",
                external_id="eth",
                observed_at=now,
                event_time=now,
                symbol="ETH",
                title="Ether",
                value=2.0,
                currency="USD",
                unit="price",
                source_url="https://example.invalid",
                payload={},
                provenance={"source": "test"},
            )
        )
        store.record_run(ProviderRun("crypto-fixture", "crypto", True, True, 1, started_at=now, completed_at=now))
        health = store.health()
        crypto = next(row for row in health["layers"] if row["layer"] == "crypto")
        self.assertTrue(crypto["operational"])


if __name__ == "__main__":
    unittest.main()
