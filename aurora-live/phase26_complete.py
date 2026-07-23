from __future__ import annotations

import uuid

from phase25_complete import Phase25Application
from phase26_operations import ProductionOperations
from platform_wsgi import HTTPError, RID_RE


class Phase26Application(Phase25Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.production = ProductionOperations(
            self.store, self.qualification
        )

    def _operations_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/operations/profiles" and method == "GET":
            return 200, self.production.profiles()
        if path == "/api/platform/operations/profile" and method == "GET":
            return 200, self.production.profile(value("name", "public"))
        if path == "/api/platform/operations/slo" and method == "GET":
            return 200, self.production.slo(
                actor, int(value("window_hours", "24"))
            )
        if path == "/api/platform/operations/readiness" and method == "GET":
            return 200, self.production.readiness(
                actor, value("profile", "public")
            )
        if path == "/api/platform/operations/events" and method == "GET":
            return 200, {
                "events": self.production.events(
                    actor, int(value("limit", "100"))
                )
            }
        if path == "/api/platform/operations/samples" and method == "POST":
            return 201, self.production.record_sample(actor, body())
        if path == "/api/platform/operations/drills" and method == "POST":
            return 201, self.production.record_drill(actor, body())
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if (
            path == "/.well-known/aurora-deployment.json"
            and method == "GET"
        ):
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 26,
                    **self.production.profiles(),
                    "claims": {
                        "production_ready": "qualification required",
                        "public_uptime": "not inferred from configuration",
                        "external_ai_required": False,
                    },
                },
                rid,
            )

        if not path.startswith("/api/platform/operations"):
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._operations_response(
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


application = Phase26Application()

