from __future__ import annotations

import uuid

from phase32_complete import Phase32Application
from phase33_webcams import WebcamRegistry
from platform_wsgi import HTTPError, RID_RE


class Phase33Application(Phase32Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.webcams = WebcamRegistry()

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-webcams.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 33,
                        "capability": "regional webcam registry and health qualification",
                        "regions": [row["region"] for row in self.webcams.coverage()["regions"]],
                        "required_online_per_region": 10,
                        "registration_is_not_live_evidence": True,
                    },
                    rid,
                )

            if path == "/api/public/webcams" and method == "GET":
                query = self._query(environ)
                items = self.webcams.list(
                    self._value(query, "region", ""),
                    self._value(query, "health", ""),
                    int(self._value(query, "limit", "250")),
                )
                return self._response(environ, start_response, 200, {"webcams": items, "total": len(items)}, rid)

            if path == "/api/public/webcams/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.webcams.coverage(), rid)

            if path == "/api/public/source-health/webcams" and method == "GET":
                return self._response(environ, start_response, 200, self.webcams.source_health(), rid)

            if path == "/api/platform/webcams" and method == "POST":
                self._user(environ)
                return self._response(environ, start_response, 201, self.webcams.register(self.platform.body(environ)), rid)

            if path.startswith("/api/platform/webcams/") and path.endswith("/health") and method == "POST":
                self._user(environ)
                webcam_id = path[len("/api/platform/webcams/") : -len("/health")].strip("/")
                if not webcam_id:
                    raise HTTPError(404, "not_found", "route not found")
                return self._response(environ, start_response, 200, self.webcams.observe(webcam_id, self.platform.body(environ)), rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase33Application()
