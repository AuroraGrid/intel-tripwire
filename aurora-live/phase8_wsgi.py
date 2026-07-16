from __future__ import annotations

import os
import uuid
from pathlib import Path

from phase8_runtime import enrich_incident, operational_status, read_runtime
from platform_wsgi import HTTPError, RID_RE, STATUS
from production_wsgi import ProductionApplication


ROOT = Path(__file__).resolve().parent


class Phase8Application:
    def __init__(self, base=None, runtime_path=None, dashboard_path=None):
        self.base = base or ProductionApplication()
        self.platform = self.base.base
        self.store = self.platform.store
        self.runtime_path = Path(runtime_path or os.getenv("AURORA_RUNTIME_PATH", "/data/aurora-runtime.json"))
        self.dashboard_path = Path(dashboard_path or ROOT / "static" / "platform.html")
        self.stale_after = max(30, int(os.getenv("AURORA_STALE_AFTER_SECONDS", "900")))

    def _html(self, environ, start_response, rid):
        self.platform.origin(environ)
        try:
            body = self.dashboard_path.read_bytes()
        except FileNotFoundError as exc:
            raise HTTPError(404, "not_found", "dashboard not found") from exc
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            *self.platform.security_headers(environ, rid, "public, max-age=60"),
            ("Content-Length", str(len(body))),
        ]
        start_response("200 OK", headers)
        return [body]

    def _user(self, environ):
        self.platform.origin(environ)
        return self.platform.user(environ)

    def _status(self, user):
        snapshot = read_runtime(self.runtime_path)
        status = operational_status(snapshot, self.stale_after)
        workers = self.base.worker_state.status(int(os.getenv("AURORA_WORKER_STALE_SECONDS", "120")))
        workers["total_workers"] = len(workers.get("workers") or [])
        status["workers"] = workers
        status["database"] = self.store.backend
        status["workspace_id"] = user.get("workspace_id")
        return status

    def _bundle(self, user, incident_id):
        workspace_id = user.get("workspace_id")
        incident = enrich_incident(self.store.incident(incident_id, True, workspace_id))
        cases = self.platform.ops.cases(user["id"], workspace_id)
        return {
            "incident": incident,
            "timeline": self.store.timeline(incident_id, workspace_id),
            "graph": self.store.graph(incident_id, workspace_id),
            "cases": [{key: case.get(key) for key in ("id", "title", "status", "priority")} for case in cases],
        }

    def _response(self, environ, start_response, status, value, rid):
        return self.base.response(environ, start_response, status, value, rid, rid)

    def _error(self, environ, start_response, rid, error):
        body = self.platform.json({"error": {"code": error.code, "message": error.message}, "request_id": rid})
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("X-Request-ID", rid),
            *error.headers,
            ("Content-Length", str(len(body))),
        ]
        start_response(f"{error.status} {STATUS.get(error.status, 'Unknown')}", headers)
        return [body]

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        managed = path in {"/platform", "/platform/", "/api/platform/operations-status"} or path.endswith("/bundle")
        if not managed:
            return self.base(environ, start_response)
        try:
            if method == "GET" and path in {"/platform", "/platform/"}:
                return self._html(environ, start_response, rid)
            user = self._user(environ)
            if method == "GET" and path == "/api/platform/operations-status":
                return self._response(environ, start_response, 200, self._status(user), rid)
            parts = [part for part in path.split("/") if part]
            if method == "GET" and len(parts) == 5 and parts[:3] == ["api", "platform", "incidents"] and parts[4] == "bundle":
                return self._response(environ, start_response, 200, self._bundle(user, parts[3]), rid)
            raise HTTPError(404, "not_found", "route not found")
        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except ValueError as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except Exception:
            return self._error(environ, start_response, rid, HTTPError(500, "internal_error", "internal server error"))


application = Phase8Application()
