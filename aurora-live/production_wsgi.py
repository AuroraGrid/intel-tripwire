from __future__ import annotations

import os
import uuid

from observability import METRICS, log_event, metrics_enabled, timed_request
from platform_wsgi import HTTPError, RID_RE, STATUS, create_application
from worker_state import WorkerState


class ProductionApplication:
    def __init__(self, base=None):
        self.base = base or create_application()
        self.identity = self.base.store.identity
        self.worker_state = WorkerState(self.base.store)

    def response(self, environ, start_response, status, value, rid, trace_id=None):
        body = self.base.json(value)
        headers = [("Content-Type", "application/json; charset=utf-8"), *self.base.security_headers(environ, rid), ("Content-Length", str(len(body)))]
        if trace_id: headers.append(("X-Trace-ID", trace_id))
        start_response(f"{status} {STATUS.get(status, 'Service Unavailable' if status == 503 else 'Unknown')}", headers)
        return [body]

    def readiness(self):
        checks = {"database": {"ok": False}, "workers": {"ok": True, "required": os.getenv("AURORA_REQUIRE_WORKER", "0") == "1"}}
        try:
            with self.base.store.db() as connection: connection.execute("SELECT 1").fetchone()
            checks["database"] = {"ok": True, "backend": self.base.store.backend}
        except Exception as exc:
            checks["database"] = {"ok": False, "error": type(exc).__name__}
        workers = self.worker_state.status(int(os.getenv("AURORA_WORKER_STALE_SECONDS", "120")))
        checks["workers"].update({"healthy": workers["healthy_workers"]})
        if checks["workers"]["required"]: checks["workers"]["ok"] = workers["healthy_workers"] > 0
        ready = all(item["ok"] for item in checks.values())
        METRICS.set("aurora_readiness", 1 if ready else 0)
        METRICS.set("aurora_healthy_workers", workers["healthy_workers"])
        return ready, {"status": "ready" if ready else "not_ready", "checks": checks}

    def dispatch_admin(self, environ, user, path, method):
        parts = [part for part in path.split("/") if part]
        if path == "/api/platform/workers" and method == "GET":
            self.identity.require(user, "workers"); return self.worker_state.status(int(os.getenv("AURORA_WORKER_STALE_SECONDS", "120")))
        if path == "/api/platform/workspaces" and method == "GET": return {"workspaces": self.identity.workspaces(user["id"])}
        if path == "/api/platform/workspaces" and method == "POST":
            payload = self.base.body(environ); return self.identity.create_workspace(user, payload.get("name", ""), payload.get("slug", ""))
        if path == "/api/platform/memberships" and method == "GET": return {"memberships": self.identity.memberships(user)}
        if path == "/api/platform/memberships" and method == "POST":
            payload = self.base.body(environ); return self.identity.add_membership(user, payload.get("user_id", ""), payload.get("role", ""), payload.get("workspace_id"))
        if path == "/api/platform/tokens" and method == "GET": return {"tokens": self.identity.tokens(user)}
        if path == "/api/platform/tokens" and method == "POST":
            payload = self.base.body(environ); token, secret = self.identity.issue_token(user, payload.get("user_id"), payload.get("name", "api"), payload.get("expires_at"), payload.get("workspace_id")); return {"token": token, "secret": secret, "warning": "store this secret now"}
        if len(parts) == 5 and parts[:3] == ["api", "platform", "tokens"] and parts[4] == "revoke" and method == "POST": return self.identity.revoke(user, parts[3])
        if len(parts) == 5 and parts[:3] == ["api", "platform", "tokens"] and parts[4] == "rotate" and method == "POST":
            token, secret = self.identity.rotate(user, parts[3]); return {"token": token, "secret": secret, "warning": "store this secret now"}
        if path == "/api/platform/audit" and method == "GET":
            query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(str(environ.get("QUERY_STRING") or "")); return {"events": self.identity.audits(user, int((query.get("limit") or ["100"])[0]))}
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        trace_id = str(environ.get("HTTP_X_TRACE_ID") or "")
        trace_id = trace_id if RID_RE.fullmatch(trace_id) else rid
        finish = timed_request(method, path)
        completed = False

        def observed_start(status, headers, exc_info=None):
            nonlocal completed
            code = int(str(status).split()[0])
            headers = list(headers)
            if not any(key.lower() == "x-trace-id" for key, _ in headers): headers.append(("X-Trace-ID", trace_id))
            if not completed:
                duration = finish(code)
                log_event("http_request", level="error" if code >= 500 else "info", request_id=rid, trace_id=trace_id, method=method, path=path, status=code, duration_seconds=round(duration, 6))
                completed = True
            if exc_info is None:
                return start_response(status, headers)
            return start_response(status, headers, exc_info)

        try:
            if path == "/api/platform/metrics" and method == "GET":
                if not metrics_enabled(): raise HTTPError(404, "not_found", "route not found")
                body = METRICS.render(); observed_start("200 OK", [("Content-Type", "text/plain; version=0.0.4"), ("Content-Length", str(len(body))), ("X-Request-ID", rid)]); return [body]
            if path == "/api/platform/ready" and method == "GET":
                ready, payload = self.readiness(); return self.response(environ, observed_start, 200 if ready else 503, payload, rid, trace_id)
            managed = path == "/api/platform/workers" or path.startswith("/api/platform/workspaces") or path.startswith("/api/platform/memberships") or path.startswith("/api/platform/tokens") or path.startswith("/api/platform/audit")
            if not managed: return self.base(environ, observed_start)
            self.base.origin(environ); user = self.base.user(environ); value = self.dispatch_admin(environ, user, path, method)
            status_code = 201 if method == "POST" and path in {"/api/platform/workspaces", "/api/platform/memberships", "/api/platform/tokens"} else 200
            return self.response(environ, observed_start, status_code, value, rid, trace_id)
        except PermissionError as exc: error = HTTPError(403, "forbidden", str(exc))
        except HTTPError as exc: error = exc
        except KeyError as exc: error = HTTPError(404, "not_found", str(exc).strip("'") or "resource not found")
        except ValueError as exc: error = HTTPError(400, "bad_request", str(exc))
        except Exception as exc:
            log_event("request_exception", "error", request_id=rid, trace_id=trace_id, method=method, path=path, error=type(exc).__name__, message=str(exc)); error = HTTPError(500, "internal_error", "internal server error")
        body = self.base.json({"error": {"code": error.code, "message": error.message}, "request_id": rid})
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"), ("X-Request-ID", rid), ("X-Trace-ID", trace_id), *error.headers, ("Content-Length", str(len(body)))]
        observed_start(f"{error.status} {STATUS.get(error.status, 'Service Unavailable' if error.status == 503 else 'Unknown')}", headers)
        return [body]


application = ProductionApplication()
