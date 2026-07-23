from __future__ import annotations

import uuid

from phase16_complete import Phase16Application
from phase17_fabric import RealtimeFabric
from platform_wsgi import HTTPError, RID_RE


class Phase17Application(Phase16Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fabric = RealtimeFabric(self.store, self.detection)

    def _fabric_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/fabric/events" and method == "GET":
            return 200, self.fabric.stream(actor, int(value("after", "0")), int(value("limit", "100")), value("event_type"))
        if path == "/api/platform/fabric/process" and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 200, self.fabric.process_pending(actor, int(payload.get("limit", 100)))
        if path == "/api/platform/fabric/checkpoint" and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 200, self.fabric.checkpoint(actor, payload.get("consumer", ""), int(payload.get("sequence", 0)))
        if path == "/api/platform/fabric/replay" and method == "GET":
            return 200, self.fabric.replay(actor, value("consumer"), int(value("limit", "100")))
        if path == "/api/platform/fabric/status" and method == "GET":
            return 200, self.fabric.status(actor)
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/fabric"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._fabric_response(path, method, actor, environ)
            return self._response(environ, start_response, status, payload, rid)
        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except ValueError as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except Exception:
            return self._error(environ, start_response, rid, HTTPError(500, "internal_error", "internal server error"))


application = Phase17Application()
