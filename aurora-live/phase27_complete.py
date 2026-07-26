from __future__ import annotations

import uuid

from phase26_complete import Phase26Application
from phase27_competitive import CompetitiveGapClosure
from platform_wsgi import HTTPError, RID_RE


class Phase27Application(Phase26Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.competitive = CompetitiveGapClosure(
            self.store, self.qualification
        )

    def _competitive_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/competitive/gaps" and method == "GET":
            return 200, self.competitive.summary(
                actor, value("as_of") or None
            )
        if path == "/api/platform/competitive/evidence" and method == "GET":
            return 200, {
                "evidence": self.competitive.evidence(
                    actor,
                    value("capability"),
                    int(value("limit", "100")),
                )
            }
        if path == "/api/platform/competitive/sync" and method == "POST":
            return 200, self.competitive.sync(actor)
        if path == "/api/platform/competitive/evidence" and method == "POST":
            return 201, self.competitive.record_evidence(actor, body())
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if (
            path == "/.well-known/aurora-competitive-gaps.json"
            and method == "GET"
        ):
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 27,
                    "purpose": "evidence-backed competitive gap closure",
                    "claims": {
                        "superiority_is_self_certified": False,
                        "latest_evidence_wins": True,
                        "evidence_expires": True,
                        "external_evidence_required_where_declared": True,
                    },
                },
                rid,
            )

        namespace = (
            path == "/api/platform/competitive"
            or path.startswith("/api/platform/competitive/")
        )
        if not namespace:
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._competitive_response(
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


application = Phase27Application()
