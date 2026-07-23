from __future__ import annotations

import uuid

from phase24_complete import Phase24Application
from phase25_qualification import IntegrationQualifier
from platform_wsgi import HTTPError, RID_RE


class Phase25Application(Phase24Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qualification = IntegrationQualifier(
            self.store,
            {
                "mesh": self.mesh,
                "integrity": self.integrity,
                "detection": self.detection,
                "fabric": self.fabric,
                "graph": self.graph_engine,
                "media": self.media,
                "picture": self.operating_picture,
                "routes": self.routes,
                "forecasts": self.forecasts,
                "autonomous": self.autonomous_forecasts,
                "command": self.command_center,
                "developer": self.developer,
                "mcp": self.mcp,
                "delivery": self.delivery,
            },
        )

    def _qualification_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)

        if path == "/api/platform/qualification/domain-model" and method == "GET":
            return 200, self.qualification.domain_model()
        if path == "/api/platform/qualification/world-monitor" and method == "GET":
            return 200, self.qualification.baseline
        if path == "/api/platform/qualification/benchmark" and method == "GET":
            return 200, self.qualification.benchmark(actor)
        if path == "/api/platform/qualification/latest" and method == "GET":
            return 200, self.qualification.latest(actor)
        if path == "/api/platform/qualification/runs" and method == "GET":
            return 200, {
                "runs": self.qualification.runs(
                    actor, int(value("limit", "50"))
                )
            }
        if path == "/api/platform/qualification/run" and method == "POST":
            self.store.identity.require(actor, "admin")
            return 201, self.qualification.run(actor)
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if (
            path == "/.well-known/aurora-qualification-methodology.json"
            and method == "GET"
        ):
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 25,
                    "domain_model": self.qualification.domain_model(),
                    "world_monitor_baseline": self.qualification.baseline,
                    "rules": {
                        "superiority_requires_measured_evidence": True,
                        "unknowns_are_not_scored_as_wins": True,
                        "external_review_cannot_be_self_certified": True,
                    },
                },
                rid,
            )

        if not path.startswith("/api/platform/qualification"):
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._qualification_response(
                path, method, actor, environ
            )
            return self._response(
                environ, start_response, status, payload, rid
            )
        except PermissionError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(403, "forbidden", str(exc)),
            )
        except KeyError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(
                    404,
                    "not_found",
                    str(exc).strip("'") or "resource not found",
                ),
            )
        except ValueError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(400, "bad_request", str(exc)),
            )
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except Exception:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(500, "internal_error", "internal server error"),
            )


application = Phase25Application()
