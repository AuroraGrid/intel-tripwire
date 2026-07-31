from __future__ import annotations

import os
import uuid

from phase38_complete import Phase38Application
from phase39_infrastructure import InfrastructureCoordinator
from phase39_operational import OperationalInfrastructureStore
from platform_wsgi import HTTPError, RID_RE


class Phase39Application(Phase38Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = (
            os.getenv("AURORA_INFRASTRUCTURE_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ":memory:"
        )
        self.infrastructure_store = OperationalInfrastructureStore(target)
        self.infrastructure = InfrastructureCoordinator(self.infrastructure_store)

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase39.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 39,
                        "capability": "evidence-gated infrastructure and systemic-risk layers",
                        "layers": [
                            "severe_weather",
                            "wildfire",
                            "outage",
                            "bgp",
                            "power",
                            "cyber",
                            "sanctions",
                            "government_alerts",
                        ],
                        "registration_is_not_live_evidence": True,
                        "configured_is_not_operational": True,
                        "qualification_requires_fresh_durable_observations": True,
                        "freshness_basis": "recent retrieval evidence; event age is reported separately",
                        "missing_scoped_or_credentialed_feeds_remain_not_configured": True,
                    },
                    rid,
                )

            if path == "/api/public/infrastructure/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.infrastructure_store.coverage(), rid)

            if path == "/api/public/infrastructure/health" and method == "GET":
                query = self._query(environ)
                maximum_age = int(self._value(query, "max_age_seconds", "0") or 0)
                return self._response(environ, start_response, 200, self.infrastructure_store.health(maximum_age or None), rid)

            if path == "/api/public/infrastructure/providers" and method == "GET":
                query = self._query(environ)
                layer = self._value(query, "layer", "")
                rows = self.infrastructure_store.providers(layer)
                return self._response(environ, start_response, 200, {"providers": rows, "total": len(rows)}, rid)

            if path == "/api/public/infrastructure/runs" and method == "GET":
                query = self._query(environ)
                rows = self.infrastructure_store.runs(
                    layer=self._value(query, "layer", ""),
                    provider=self._value(query, "provider", ""),
                    limit=int(self._value(query, "limit", "100")),
                )
                return self._response(environ, start_response, 200, {"runs": rows, "total": len(rows)}, rid)

            if path == "/api/public/infrastructure/observations" and method == "GET":
                query = self._query(environ)
                rows = self.infrastructure_store.observations(
                    layer=self._value(query, "layer", ""),
                    provider=self._value(query, "provider", ""),
                    limit=int(self._value(query, "limit", "250")),
                )
                return self._response(environ, start_response, 200, {"observations": rows, "total": len(rows)}, rid)

            if path == "/api/public/infrastructure/configuration" and method == "GET":
                return self._response(environ, start_response, 200, self.infrastructure.configuration(), rid)

            if path.startswith("/api/platform/infrastructure/run/") and method == "POST":
                self._user(environ)
                provider = path[len("/api/platform/infrastructure/run/") :].strip("/")
                if not provider:
                    raise HTTPError(404, "not_found", "route not found")
                body = self.platform.body(environ)
                result = self.infrastructure.run(provider, timeout=int(body.get("timeout") or 30))
                status = 200 if result.successful else 409 if not result.configured else 502
                return self._response(environ, start_response, status, result.value(), rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                transport_health = self.transport_store.health()
                infrastructure_health = self.infrastructure_store.health()
                payload = {
                    "phase": 39,
                    "transport_coverage": self.transport_store.coverage(),
                    "transport_health": transport_health,
                    "transport_configuration": self.transport_providers.configuration(),
                    "transport_providers": self.transport_store.providers(),
                    "recent_transport_runs": self.transport_store.provider_runs(limit=20),
                    "recent_transport_observations": self.transport_store.observations(limit=100),
                    "transport_workers": transport_health["workers"],
                    "infrastructure_coverage": self.infrastructure_store.coverage(),
                    "infrastructure_health": infrastructure_health,
                    "infrastructure_configuration": self.infrastructure.configuration(),
                    "infrastructure_providers": self.infrastructure_store.providers(),
                    "recent_infrastructure_runs": self.infrastructure_store.runs(limit=40),
                    "recent_infrastructure_observations": self.infrastructure_store.observations(limit=200),
                    "webcam_coverage": self.webcams.coverage(),
                    "webcam_matrix": self.webcams.matrix(),
                    "webcams_fully_qualified": self.webcams.coverage()["fully_qualified"],
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


application = Phase39Application()
