from __future__ import annotations

import uuid

from phase11c_complete import Phase11CApplication
from phase12_fusion import SignalFusion
from platform_wsgi import HTTPError, RID_RE


class Phase12Application(Phase11CApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fusion = SignalFusion(self.store)

    def _fusion_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        parts = [part for part in path.split("/") if part]
        if path == "/api/platform/live-signals":
            if method == "GET":
                return 200, {"signals": self.fusion.list(actor, value("signal_type"), value("provider"), int(value("limit", "200")))}
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.fusion.ingest(actor, self.platform.body(environ))
        if path == "/api/platform/provider-health":
            if method == "GET":
                return 200, self.fusion.provider_scorecard(actor)
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.fusion.record_health(actor, self.platform.body(environ))
        if path == "/api/platform/fused-events":
            if method == "GET":
                return 200, {"events": self.fusion.fused_list(actor, int(value("limit", "100")))}
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.fusion.fuse(actor, self.platform.body(environ))
        if len(parts) == 4 and parts[:3] == ["api", "platform", "fused-events"] and method == "GET":
            return 200, self.fusion.fused(actor, parts[3])
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        managed = path.startswith("/api/platform/live-signals") or path.startswith("/api/platform/provider-health") or path.startswith("/api/platform/fused-events")
        if not managed:
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._fusion_response(path, method, actor, environ)
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


application = Phase12Application()
