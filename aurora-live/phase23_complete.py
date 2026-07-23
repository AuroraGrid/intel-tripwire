from __future__ import annotations

import uuid

from phase22_complete import Phase22Application
from phase23_experience import UnifiedAnalystExperience
from platform_wsgi import HTTPError, RID_RE


class Phase23Application(Phase22Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.command_center = UnifiedAnalystExperience(
            self.store,
            self.mesh,
            self.integrity,
            self.detection,
            self.operating_picture,
            self.routes,
            self.autonomous_forecasts,
        )

    def _command_center_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/command-center/overview" and method == "GET":
            return 200, self.command_center.overview(actor)
        if path == "/api/platform/command-center/search" and method == "GET":
            return 200, {
                "results": self.command_center.search(
                    actor, value("query"), int(value("limit", "50"))
                )
            }
        if path == "/api/platform/command-center/activity" and method == "GET":
            return 200, {
                "activity": self.command_center.activity(
                    actor, int(value("limit", "100"))
                )
            }
        if path == "/api/platform/command-center/saved-views":
            if method == "GET":
                return 200, {
                    "saved_views": self.command_center.saved_views(actor)
                }
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.command_center.save_view(actor, body())
        if path == "/api/platform/command-center/assignments" and method == "POST":
            self.store.identity.require(actor, "write")
            return 200, self.command_center.assign(actor, body())
        if path == "/api/platform/command-center/comments" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.command_center.comment(actor, body())

        if (
            len(parts) == 5
            and parts[:4]
            == ["api", "platform", "command-center", "saved-views"]
            and method == "GET"
        ):
            return 200, self.command_center.saved_view(actor, parts[4])

        if (
            len(parts) == 6
            and parts[:4]
            == ["api", "platform", "command-center", "collaboration"]
            and method == "GET"
        ):
            return 200, self.command_center.collaboration(
                actor, parts[4], parts[5]
            )
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/command-center"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._command_center_response(
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


application = Phase23Application()
