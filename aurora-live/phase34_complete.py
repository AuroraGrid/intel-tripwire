from __future__ import annotations

import uuid

from phase33_complete import Phase33Application
from phase34_imagery import IMAGE_CATEGORIES, ImageRegistry
from platform_wsgi import HTTPError, RID_RE


class Phase34Application(Phase33Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imagery = ImageRegistry()

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-imagery.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 34,
                        "capability": "live still imagery with freshness, replay, duplicate, and provenance controls",
                        "categories": list(IMAGE_CATEGORIES),
                        "registration_is_not_current_evidence": True,
                        "freshness_observation_required": True,
                    },
                    rid,
                )

            if path == "/api/public/imagery" and method == "GET":
                query = self._query(environ)
                items = self.imagery.list(
                    self._value(query, "region", ""),
                    self._value(query, "category", ""),
                    self._value(query, "state", ""),
                    int(self._value(query, "limit", "250")),
                )
                return self._response(environ, start_response, 200, {"images": items, "total": len(items)}, rid)

            if path == "/api/public/imagery/latest" and method == "GET":
                query = self._query(environ)
                items = self.imagery.latest(
                    self._value(query, "region", ""),
                    self._value(query, "category", ""),
                    int(self._value(query, "limit", "100")),
                )
                return self._response(environ, start_response, 200, {"images": items, "total": len(items)}, rid)

            if path == "/api/public/imagery/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.imagery.coverage(), rid)

            if path == "/api/public/source-health/imagery" and method == "GET":
                return self._response(environ, start_response, 200, self.imagery.source_health(), rid)

            if path.startswith("/api/public/imagery/") and method == "GET":
                source_id = path[len("/api/public/imagery/") :].strip("/")
                if not source_id:
                    raise HTTPError(404, "not_found", "route not found")
                return self._response(environ, start_response, 200, self.imagery.get(source_id), rid)

            if path == "/api/platform/imagery" and method == "POST":
                self._user(environ)
                return self._response(environ, start_response, 201, self.imagery.register(self.platform.body(environ)), rid)

            if path.startswith("/api/platform/imagery/") and path.endswith("/observations") and method == "POST":
                self._user(environ)
                source_id = path[len("/api/platform/imagery/") : -len("/observations")].strip("/")
                if not source_id:
                    raise HTTPError(404, "not_found", "route not found")
                return self._response(environ, start_response, 200, self.imagery.observe(source_id, self.platform.body(environ)), rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase34Application()
