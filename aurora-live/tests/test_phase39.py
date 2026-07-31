from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from phase38_complete import Phase38Application
from phase39_complete import Phase39Application
from phase39_infrastructure import (
    CISAKEVAdapter,
    ConfiguredJSONAdapter,
    EONETWildfireAdapter,
    InfrastructureCoordinator,
    InfrastructureObservation,
    InfrastructureStore,
    LAYERS,
    NWSAlertsAdapter,
    OFACSDNAdapter,
    ProviderRun,
    _now,
)


class Phase39Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase39Application, Phase38Application))

    def test_registration_never_qualifies_a_layer(self):
        store = InfrastructureStore(":memory:")
        coordinator = InfrastructureCoordinator(store)
        self.assertEqual(len(coordinator.adapters), 8)
        self.assertEqual({row["layer"] for row in store.providers()}, set(LAYERS))
        self.assertEqual(store.coverage()["qualified_layers"], 0)
        self.assertFalse(store.coverage()["fully_qualified"])

    def test_observation_identity_is_idempotent(self):
        store = InfrastructureStore(":memory:")
        observation = InfrastructureObservation(
            layer="cyber",
            provider="fixture",
            external_id="CVE-TEST-1",
            observed_at=_now(),
            event_time=_now(),
            severity="HIGH",
            title="Fixture vulnerability",
            summary="fixture",
            source_url="https://example.invalid",
            payload={"fixture": True},
            provenance={"source": "test"},
        )
        first = store.record(observation)
        second = store.record(observation)
        self.assertEqual(first, second)
        self.assertEqual(len(store.observations("cyber")), 1)

    def test_nws_adapter_parses_official_alert(self):
        payload = {
            "features": [
                {
                    "id": "urn:alert:1",
                    "geometry": {"coordinates": [-77.0, 38.9]},
                    "properties": {
                        "id": "urn:alert:1",
                        "headline": "Tornado Warning",
                        "severity": "Extreme",
                        "sent": _now(),
                        "description": "Take shelter",
                    },
                }
            ]
        }
        with patch("phase39_infrastructure._json", return_value=payload):
            rows = NWSAlertsAdapter().fetch()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].layer, "severe_weather")
        self.assertEqual(rows[0].severity, "EXTREME")
        self.assertEqual(rows[0].latitude, 38.9)
        self.assertEqual(rows[0].longitude, -77.0)

    def test_eonet_adapter_preserves_source_and_geometry(self):
        payload = {
            "events": [
                {
                    "id": "EONET_1",
                    "title": "Wildfire fixture",
                    "link": "https://eonet.invalid/1",
                    "sources": [{"url": "https://source.invalid/fire"}],
                    "geometry": [{"date": _now(), "coordinates": [20.0, 10.0]}],
                }
            ]
        }
        with patch("phase39_infrastructure._json", return_value=payload):
            rows = EONETWildfireAdapter().fetch()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].layer, "wildfire")
        self.assertEqual(rows[0].source_url, "https://source.invalid/fire")
        self.assertEqual(rows[0].latitude, 10.0)
        self.assertEqual(rows[0].longitude, 20.0)

    def test_cisa_kev_adapter_preserves_cve_identity(self):
        payload = {
            "vulnerabilities": [
                {
                    "cveID": "CVE-2026-0001",
                    "vulnerabilityName": "Fixture exploit",
                    "dateAdded": "2026-07-30",
                    "shortDescription": "Actively exploited fixture",
                }
            ]
        }
        with patch("phase39_infrastructure._json", return_value=payload):
            rows = CISAKEVAdapter().fetch()
        self.assertEqual(rows[0].external_id, "CVE-2026-0001")
        self.assertEqual(rows[0].layer, "cyber")
        self.assertNotIn("credential", json.dumps(rows[0].value()).lower())

    def test_ofac_adapter_is_namespace_tolerant(self):
        xml = b"""<?xml version='1.0'?>
        <sdnList xmlns='https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML'>
          <sdnEntry><uid>42</uid><firstName>Test</firstName><lastName>Entity</lastName><sdnType>Entity</sdnType><programList><program>TEST</program></programList></sdnEntry>
        </sdnList>"""
        with patch("phase39_infrastructure._request", return_value=xml):
            rows = OFACSDNAdapter().fetch()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, "42")
        self.assertEqual(rows[0].title, "Test Entity")
        self.assertEqual(rows[0].layer, "sanctions")

    def test_unconfigured_scoped_provider_stays_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            store = InfrastructureStore(":memory:")
            coordinator = InfrastructureCoordinator(store)
            result = coordinator.run("configured-official-outage-feed")
        self.assertFalse(result.configured)
        self.assertFalse(result.successful)
        provider = next(row for row in store.providers() if row["provider"] == result.provider)
        self.assertEqual(provider["state"], "NOT_CONFIGURED")
        self.assertEqual(provider["consecutive_failures"], 0)

    def test_configuration_never_returns_secret_value(self):
        adapter = ConfiguredJSONAdapter(
            name="power-fixture",
            layer="power",
            url_env="AURORA_POWER_FEED_URL",
            license_env="AURORA_POWER_FEED_LICENSE",
            api_key_env="AURORA_POWER_API_KEY",
        )
        with patch.dict(
            os.environ,
            {
                "AURORA_POWER_FEED_URL": "https://example.invalid/data?key={api_key}",
                "AURORA_POWER_API_KEY": "super-secret-fixture",
            },
            clear=True,
        ):
            value = adapter.configuration()
        self.assertTrue(value["configured"])
        self.assertNotIn("super-secret-fixture", json.dumps(value))

    def test_all_layers_require_fresh_durable_success(self):
        store = InfrastructureStore(":memory:")
        now = _now()
        for layer in LAYERS:
            provider = f"{layer}-fixture"
            store.upsert_provider(
                {
                    "provider": provider,
                    "layer": layer,
                    "state": "ONLINE",
                    "last_attempt_at": now,
                    "last_success_at": now,
                    "consecutive_failures": 0,
                    "freshness_seconds": 0,
                    "last_error": "",
                    "completeness_note": "test only",
                    "license_note": "test only",
                }
            )
            store.record(
                InfrastructureObservation(
                    layer=layer,
                    provider=provider,
                    external_id=f"{layer}-1",
                    observed_at=now,
                    event_time=now,
                    severity="INFO",
                    title=f"{layer} fixture",
                    summary="fixture",
                    source_url="https://example.invalid",
                    payload={},
                    provenance={"source": "test"},
                )
            )
            store.record_run(ProviderRun(provider, layer, True, True, 1, started_at=now, completed_at=now))
        coverage = store.coverage()
        self.assertEqual(coverage["qualified_layers"], 8)
        self.assertTrue(coverage["fully_qualified"])


if __name__ == "__main__":
    unittest.main()
