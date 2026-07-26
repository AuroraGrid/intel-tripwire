import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from phase11_store import ForecastLedger
from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase28_accuracy import AccuracyHistory
from phase28_complete import Phase28Application
from platform_wsgi import create_application
from production_wsgi import ProductionApplication
from storage import Store


def request(app, path, method="GET", body=None, token=""):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path.split("?", 1)[0],
        "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": "localhost",
    }
    if token:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    captured = {}

    def start(status, headers):
        captured.update(status=status, headers=dict(headers))

    result = b"".join(app(environ, start))
    return {
        "code": int(captured["status"].split()[0]),
        "json": json.loads(result) if result else None,
    }


class Phase28Tests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase28.db")
        _, self.token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(self.token)
        _, viewer_token = self.store.create_user(
            f"viewer-{uuid.uuid4().hex}@example.com", "viewer"
        )
        self.viewer = self.store.auth(viewer_token)
        self.viewer["workspace_id"] = self.actor["workspace_id"]
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.detection = DetectionEngine(
            self.store, self.mesh, self.integrity
        )
        self.forecasts = ForecastLedger(self.store)
        self.history = AccuracyHistory(
            self.store,
            self.integrity,
            self.detection,
            self.forecasts,
        )
        self.source = self.integrity.register_source(
            self.actor,
            {
                "name": "Primary Test Source",
                "canonical_url": "https://primary.example/",
                "lineage_key": "primary-test",
                "source_tier": 1,
                "reliability": 0.9,
            },
        )

    def tearDown(self):
        CURRENT_WORKSPACE.set(None)
        self.temp.cleanup()

    def outcome_payload(self, **updates):
        payload = {
            "subject_type": "SOURCE",
            "subject_id": self.source["id"],
            "outcome": "TRUE_POSITIVE",
            "domain": "security",
            "evidence": {"artifact": "resolution-1.json"},
            "observed_at": "2026-07-25T12:00:00Z",
        }
        payload.update(updates)
        return payload

    def test_outcomes_are_idempotent_and_append_only(self):
        first = self.history.record_outcome(
            self.actor, self.outcome_payload()
        )
        second = self.history.record_outcome(
            self.actor, self.outcome_payload()
        )
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["id"], second["id"])
        self.history.record_outcome(
            self.actor,
            self.outcome_payload(
                outcome="FALSE_POSITIVE",
                evidence={"artifact": "resolution-2.json"},
            ),
        )
        rows = self.history.outcomes(self.actor)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["outcome"], "TRUE_POSITIVE")

    def test_scorecard_is_deterministic_and_reuses_forecast_ledger(self):
        self.history.record_outcome(
            self.actor, self.outcome_payload(weight=2)
        )
        self.history.record_outcome(
            self.actor,
            self.outcome_payload(
                outcome="FALSE_POSITIVE",
                weight=1,
                evidence={"artifact": "resolution-2.json"},
            ),
        )
        forecast = self.forecasts.create(
            self.actor,
            {
                "question": "Will the corridor reopen?",
                "probability": 0.8,
                "source": "analyst",
            },
        )
        self.forecasts.resolve(
            self.actor, forecast["id"], True, "Reopened"
        )
        first = self.history.scorecard(self.actor)
        second = self.history.scorecard(self.actor)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first["overall"]["precision"], 0.5)
        self.assertAlmostEqual(
            first["overall"]["weighted_accuracy"], 2 / 3
        )
        self.assertEqual(first["forecast_calibration"]["count"], 1)

    def test_detection_and_analyst_subjects_must_exist_in_workspace(self):
        self.mesh.ingest_observations(
            self.actor,
            "seismic.usgs",
            [
                {
                    "external_id": "phase28-quake",
                    "title": "Magnitude 6.0 earthquake near test city",
                    "observed_at": "2026-07-25T12:00:00Z",
                }
            ],
        )
        detection_id = self.detection.process_pending(
            self.actor
        )["detection_ids"][0]
        result = self.history.record_outcome(
            self.actor,
            self.outcome_payload(
                subject_type="DETECTION",
                subject_id=detection_id,
                evidence={"artifact": "detection-resolution.json"},
            ),
        )
        self.assertEqual(result["subject_type"], "DETECTION")
        with self.assertRaises(KeyError):
            self.history.record_outcome(
                self.actor,
                self.outcome_payload(
                    subject_type="ANALYST",
                    subject_id="missing-user",
                ),
            )

    def test_historical_analogs_are_offline_and_deterministic(self):
        payload = {
            "canonical_key": "strait-closure-2025",
            "title": "Temporary closure of a strategic shipping strait",
            "domain": "maritime",
            "outcome": "REOPENED_WITHIN_48_HOURS",
            "summary": "Navigation resumed after de-escalation.",
            "features": {
                "chokepoint": True,
                "military_alert": True,
            },
            "evidence": {"report": "case-2025.pdf"},
            "observed_at": "2025-05-01T00:00:00Z",
        }
        first = self.history.record_case(self.actor, payload)
        duplicate = self.history.record_case(self.actor, payload)
        self.assertEqual(first["id"], duplicate["id"])
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(ValueError):
            self.history.record_case(
                self.actor, {**payload, "outcome": "REMAINED_CLOSED"}
            )
        analogs = self.history.analogs(
            self.actor, "shipping strait closure", "maritime"
        )
        self.assertEqual(analogs[0]["id"], first["id"])
        self.assertGreater(analogs[0]["similarity"], 0)

    def test_syndication_tracks_occurrences_and_independent_lineages(self):
        second_source = self.integrity.register_source(
            self.actor,
            {
                "name": "Independent Test Source",
                "canonical_url": "https://independent.example/",
                "lineage_key": "independent-test",
                "source_tier": 2,
                "reliability": 0.8,
            },
        )
        payload = {
            "source_origin_id": self.source["id"],
            "url": "https://primary.example/report",
            "content": "A port closure was announced by authorities.",
            "evidence": {"artifact": "capture-primary.json"},
            "published_at": "2026-07-25T12:00:00Z",
        }
        first = self.history.record_fingerprint(self.actor, payload)
        repeated = self.history.record_fingerprint(self.actor, payload)
        second = self.history.record_fingerprint(
            self.actor,
            {
                **payload,
                "source_origin_id": second_source["id"],
                "url": "https://independent.example/report",
                "evidence": {"artifact": "capture-independent.json"},
            },
        )
        self.assertFalse(first["duplicate_content"])
        self.assertTrue(repeated["duplicate_occurrence"])
        self.assertTrue(second["duplicate_content"])
        self.assertEqual(second["occurrence_count"], 2)
        self.assertEqual(second["independent_lineage_count"], 2)

    def test_workspace_isolation_and_viewer_write_protection(self):
        recorded = self.history.record_outcome(
            self.actor, self.outcome_payload()
        )
        other = dict(self.actor)
        other["workspace_id"] = "another-workspace"
        with self.assertRaises(KeyError):
            self.history.outcome(other, recorded["id"])
        with self.assertRaises(PermissionError):
            self.history.record_outcome(
                self.viewer, self.outcome_payload()
            )

    def test_validation_rejects_missing_evidence_and_nonfinite_scores(self):
        with self.assertRaises(ValueError):
            self.history.record_outcome(
                self.actor, self.outcome_payload(evidence={})
            )
        with self.assertRaises(ValueError):
            self.history.record_outcome(
                self.actor, self.outcome_payload(score=float("nan"))
            )
        with self.assertRaises(ValueError):
            self.history.record_fingerprint(
                self.actor,
                {
                    "source_origin_id": self.source["id"],
                    "url": "https://primary.example/report",
                    "content_hash": "not-a-hash",
                    "evidence": {"artifact": "bad.json"},
                },
            )

    def test_phase28_api_is_authenticated_and_release_shaped(self):
        CURRENT_WORKSPACE.set(None)
        app = Phase28Application(
            base=ProductionApplication(create_application(store=self.store))
        )
        unauthorized = request(app, "/api/platform/accuracy/scorecard")
        self.assertEqual(unauthorized["code"], 401)
        created = request(
            app,
            "/api/platform/accuracy/outcomes",
            "POST",
            self.outcome_payload(evidence={"artifact": "api.json"}),
            self.token,
        )
        self.assertEqual(created["code"], 201)
        CURRENT_WORKSPACE.set(None)
        card = request(
            app,
            "/api/platform/accuracy/scorecard",
            token=self.token,
        )
        self.assertEqual(card["code"], 200)
        self.assertEqual(card["json"]["phase"], 28)


if __name__ == "__main__":
    unittest.main()

