from __future__ import annotations

import os
import uuid

from phase36_complete import Phase36Application
from phase36_operations import UnifiedSourceHealth, regional_baseline
from phase37_capabilities import reconciled_gaps, reconciled_manifest
from phase37_webcams import DurableWebcamRegistry, WebcamHealthCoordinator, WebcamStore
from platform_wsgi import HTTPError, RID_RE


class Phase37Application(Phase36Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = (
            os.getenv("AURORA_WEBCAM_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("AURORA_INGESTION_DB")
            or ":memory:"
        )
        self.webcam_store = WebcamStore(target)
        self.webcams = DurableWebcamRegistry(self.webcam_store)
        self.webcam_health = WebcamHealthCoordinator(self.webcams)
        self.unified_health = UnifiedSourceHealth(self.webcams, self.imagery, self.operational_store)

    def _product_manifest(self):
        return reconciled_manifest(
            webcam_coverage=self.webcams.coverage(),
            imagery_baseline=regional_baseline(self.imagery, self.operational_store),
            unified_health=self.unified_health.snapshot(),
        )

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase37.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 37,
                        "capability": "evidence-aware product status and durable 70-camera qualification operations",
                        "webcam_target": 70,
                        "regions": 7,
                        "required_online_per_region": 10,
                        "runtime_status_overrides_static_declarations": True,
                        "registration_is_not_live_evidence": True,
                    },
                    rid,
                )

            if path in {"/.well-known/aurora-product.json", "/api/public/product/capabilities"} and method == "GET":
                payload = self._product_manifest()
                if path.startswith("/.well-known/"):
                    return self._json_document(environ, start_response, payload, rid)
                return self._response(environ, start_response, 200, payload, rid)

            if path == "/api/public/product/gaps" and method == "GET":
                query = self._query(environ)
                return self._response(
                    environ,
                    start_response,
                    200,
                    reconciled_gaps(
                        webcam_coverage=self.webcams.coverage(),
                        imagery_baseline=regional_baseline(self.imagery, self.operational_store),
                        unified_health=self.unified_health.snapshot(),
                        priority=self._value(query, "priority", ""),
                    ),
                    rid,
                )

            if path == "/api/public/webcams/matrix" and method == "GET":
                return self._response(environ, start_response, 200, self.webcams.matrix(), rid)

            if path.startswith("/api/public/webcams/") and path.endswith("/history") and method == "GET":
                webcam_id = path[len("/api/public/webcams/") : -len("/history")].strip("/")
                if not webcam_id:
                    raise HTTPError(404, "not_found", "route not found")
                self.webcams.get(webcam_id)
                query = self._query(environ)
                rows = self.webcam_store.health_history(webcam_id, int(self._value(query, "limit", "100")))
                return self._response(
                    environ,
                    start_response,
                    200,
                    {"webcam_id": webcam_id, "history": rows, "total": len(rows)},
                    rid,
                )

            if path == "/api/platform/webcams/bulk" and method == "POST":
                self._user(environ)
                body = self.platform.body(environ)
                sources = body.get("sources") if isinstance(body, dict) else None
                if not isinstance(sources, list) or not sources:
                    raise ValueError("sources must be a non-empty list")
                if len(sources) > 500:
                    raise ValueError("bulk registration is limited to 500 sources")
                registered = []
                errors = []
                for index, source in enumerate(sources):
                    try:
                        if not isinstance(source, dict):
                            raise ValueError("source must be an object")
                        registered.append(self.webcams.register(source))
                    except (TypeError, ValueError) as exc:
                        errors.append({"index": index, "error": str(exc)})
                status = 201 if not errors else 207
                return self._response(
                    environ,
                    start_response,
                    status,
                    {
                        "requested": len(sources),
                        "registered": registered,
                        "registered_count": len(registered),
                        "errors": errors,
                        "error_count": len(errors),
                        "coverage": self.webcams.coverage(),
                    },
                    rid,
                )

            if path == "/api/platform/webcams/health/run" and method == "POST":
                self._user(environ)
                query = self._query(environ)
                result = self.webcam_health.run(
                    region=self._value(query, "region", ""),
                    webcam_id=self._value(query, "webcam_id", ""),
                    limit=int(self._value(query, "limit", "250")),
                )
                return self._response(environ, start_response, 200 if result["failed"] == 0 else 207, result, rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                payload = {
                    "phase": 37,
                    "product": self._product_manifest(),
                    "source_health": self.unified_health.snapshot(),
                    "imagery_baseline": regional_baseline(self.imagery, self.operational_store),
                    "webcam_coverage": self.webcams.coverage(),
                    "webcam_matrix": self.webcams.matrix(),
                    "latest_runtime_imagery": self.imagery.latest(limit=21),
                    "recent_durable_observations": self.operational_store.observations(limit=21),
                    "recent_runs": self.operational_store.runs(25),
                }
                return self._response(environ, start_response, 200, payload, rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase37Application()
