from __future__ import annotations

import os
import uuid

from phase37_complete import Phase37Application
from phase38_providers import TransportProviderCoordinator
from phase38_transport import TransportObservation, TransportRegistry, TransportStore
from platform_wsgi import HTTPError, RID_RE


class Phase38Application(Phase37Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = (
            os.getenv("AURORA_TRANSPORT_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or ":memory:"
        )
        self.transport_store = TransportStore(target)
        self.transport = TransportRegistry(self.transport_store)
        self.transport_providers = TransportProviderCoordinator(self.transport_store)

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase38.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 38,
                        "capability": "durable aviation and maritime intelligence infrastructure",
                        "domains": ["aviation", "maritime"],
                        "aviation_provider": "AviationWeather.gov keyless official API",
                        "maritime_provider": "AISStream environment-secret WebSocket adapter",
                        "provider_registration_is_not_live_evidence": True,
                        "transport_live_requires_successful_fresh_observations": True,
                        "webcam_qualification_remains_independent": True,
                    },
                    rid,
                )

            if path == "/api/public/transport/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.transport_store.coverage(), rid)

            if path == "/api/public/transport/providers" and method == "GET":
                query = self._query(environ)
                domain = self._value(query, "domain", "")
                providers = self.transport_store.providers(domain)
                return self._response(environ, start_response, 200, {"providers": providers, "total": len(providers)}, rid)

            if path == "/api/public/transport/configuration" and method == "GET":
                return self._response(environ, start_response, 200, self.transport_providers.configuration(), rid)

            if path == "/api/public/transport/observations" and method == "GET":
                query = self._query(environ)
                rows = self.transport_store.observations(
                    domain=self._value(query, "domain", ""),
                    provider=self._value(query, "provider", ""),
                    limit=int(self._value(query, "limit", "250")),
                )
                return self._response(environ, start_response, 200, {"observations": rows, "total": len(rows)}, rid)

            if path == "/api/platform/transport/providers" and method == "POST":
                self._user(environ)
                return self._response(environ, start_response, 201, self.transport.register_provider(self.platform.body(environ)), rid)

            if path.startswith("/api/platform/transport/providers/") and path.endswith("/health") and method == "POST":
                self._user(environ)
                provider = path[len("/api/platform/transport/providers/") : -len("/health")].strip("/")
                if not provider:
                    raise HTTPError(404, "not_found", "route not found")
                return self._response(environ, start_response, 200, self.transport.observe_provider(provider, self.platform.body(environ)), rid)

            if path == "/api/platform/transport/run/aviation" and method == "POST":
                self._user(environ)
                body = self.platform.body(environ)
                result = self.transport_providers.run_aviation(
                    bbox=str(body.get("bbox") or "-90,-180,90,180"),
                    hours=int(body.get("hours") or 1),
                    timeout=int(body.get("timeout") or 20),
                )
                status = 200 if result.successful else 502
                return self._response(environ, start_response, status, result.value(), rid)

            if path == "/api/platform/transport/run/maritime" and method == "POST":
                self._user(environ)
                body = self.platform.body(environ)
                result = self.transport_providers.run_maritime(
                    max_messages=int(body.get("max_messages") or 25),
                    timeout=int(body.get("timeout") or 20),
                )
                status = 200 if result.successful else 503
                return self._response(environ, start_response, status, result.value(), rid)

            if path == "/api/platform/transport/observations" and method == "POST":
                self._user(environ)
                body = self.platform.body(environ)
                observation = TransportObservation(
                    domain=str(body.get("domain") or "").strip().lower(),
                    provider=str(body.get("provider") or "").strip(),
                    external_id=str(body.get("external_id") or "").strip(),
                    observed_at=str(body.get("observed_at") or "").strip(),
                    event_time=str(body.get("event_time") or "").strip(),
                    latitude=float(body.get("latitude")),
                    longitude=float(body.get("longitude")),
                    state=str(body.get("state") or "UNKNOWN").strip().upper(),
                    payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                    provenance=body.get("provenance") if isinstance(body.get("provenance"), dict) else {},
                )
                identifier = self.transport_store.record(observation)
                return self._response(environ, start_response, 201, {"observation_id": identifier, **observation.value()}, rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                payload = {
                    "phase": 38,
                    "transport_coverage": self.transport_store.coverage(),
                    "transport_configuration": self.transport_providers.configuration(),
                    "transport_providers": self.transport_store.providers(),
                    "recent_transport_observations": self.transport_store.observations(limit=100),
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


application = Phase38Application()
