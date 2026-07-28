from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from phase35_complete import Phase35Application
from phase36_operations import OperationalCoordinator, TelemetryHttpTransport, UnifiedSourceHealth, regional_baseline
from phase36_sources import BASELINE_REGION_ADAPTERS, operational_adapter_names
from phase36_store import OperationalStore
from platform_wsgi import HTTPError, RID_RE


class Phase36Application(Phase35Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = os.getenv("AURORA_OPERATIONAL_DB") or os.getenv("AURORA_DATABASE_URL") or os.getenv("AURORA_INGESTION_DB", ":memory:")
        self.operational_store = OperationalStore(target)
        self.operational_transport = TelemetryHttpTransport()
        self.operations = OperationalCoordinator(self.imagery, self.operational_store, transport=self.operational_transport)
        self.unified_health = UnifiedSourceHealth(self.webcams, self.imagery, self.operational_store)
        self.ingestion_store = self.operational_store
        self.ingestion = self.operations.engine

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-operations.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 36,
                        "capability": "recurring official imagery operations with durable history, circuit breakers, telemetry, unified health, and regional qualification",
                        "adapters": list(operational_adapter_names()),
                        "regional_baseline": BASELINE_REGION_ADAPTERS,
                        "registration_is_not_evidence": True,
                        "only_successful_validated_observations_qualify_regions": True,
                    },
                    rid,
                )

            if path == "/api/public/operations/providers" and method == "GET":
                rows = self.operational_store.provider_states()
                return self._response(environ, start_response, 200, {"providers": rows, "total": len(rows)}, rid)

            if path == "/api/public/operations/ticks" and method == "GET":
                query = self._query(environ)
                rows = self.operational_store.ticks(int(self._value(query, "limit", "50")))
                return self._response(environ, start_response, 200, {"ticks": rows, "total": len(rows)}, rid)

            if path == "/api/public/source-health/unified" and method == "GET":
                return self._response(environ, start_response, 200, self.unified_health.snapshot(), rid)

            if path == "/api/public/imagery/regional-baseline" and method == "GET":
                return self._response(environ, start_response, 200, regional_baseline(self.imagery, self.operational_store), rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                payload = {
                    "phase": 36,
                    "source_health": self.unified_health.snapshot(),
                    "imagery_baseline": regional_baseline(self.imagery, self.operational_store),
                    "webcam_coverage": self.webcams.coverage(),
                    "latest_runtime_imagery": self.imagery.latest(limit=21),
                    "recent_durable_observations": self.operational_store.observations(limit=21),
                    "recent_runs": self.operational_store.runs(25),
                    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
                return self._response(environ, start_response, 200, payload, rid)

            if path == "/api/platform/operations/run" and method == "POST":
                self._user(environ)
                query = self._query(environ)
                requested = self._value(query, "adapter", "")
                force = self._value(query, "force", "").lower() in {"1", "true", "yes"}
                names = [requested] if requested else list(operational_adapter_names())
                result = self.operations.run_due(names, force=force)
                status = 200 if result["failed"] == 0 else 207
                return self._response(environ, start_response, status, result, rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase36Application()
