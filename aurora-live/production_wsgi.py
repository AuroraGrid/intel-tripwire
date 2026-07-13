from __future__ import annotations

import json
import uuid

from platform_wsgi import HTTPError, RID_RE, STATUS, create_application
from worker_state import WorkerState


class ProductionApplication:
    def __init__(self, base=None):
        self.base = base or create_application()
        self.identity = self.base.store.identity
        self.worker_state = WorkerState(self.base.store)

    def response(self, environ, start_response, status, value, rid):
        body = self.base.json(value)
        headers = [("Content-Type", "application/json; charset=utf-8"), *self.base.security_headers(environ, rid), ("Content-Length", str(len(body)))]
        start_response(f"{status} {STATUS.get(status, 'Unknown')}", headers)
        return [body]

    def dispatch_admin(self, environ, user, path, method):
        parts = [part for part in path.split("/") if part]
        if path == "/api/platform/workers" and method == "GET":
            self.identity.require(user, "workers")
            return self.worker_state.status(int(__import__("os").getenv("AURORA_WORKER_STALE_SECONDS", "120")))
        if path == "/api/platform/workspaces" and method == "GET":
            return {"workspaces": self.identity.workspaces(user["id"])}
        if path == "/api/platform/workspaces" and method == "POST":
            payload = self.base.body(environ)
            return self.identity.create_workspace(user, payload.get("name", ""), payload.get("slug", ""))
        if path == "/api/platform/memberships" and method == "GET":
            return {"memberships": self.identity.memberships(user)}
        if path == "/api/platform/memberships" and method == "POST":
            payload = self.base.body(environ)
            return self.identity.add_membership(user, payload.get("user_id", ""), payload.get("role", ""), payload.get("workspace_id"))
        if path == "/api/platform/tokens" and method == "GET":
            return {"tokens": self.identity.tokens(user)}
        if path == "/api/platform/tokens" and method == "POST":
            payload = self.base.body(environ)
            token, secret = self.identity.issue_token(user, payload.get("user_id"), payload.get("name", "api"), payload.get("expires_at"), payload.get("workspace_id"))
            return {"token": token, "secret": secret, "warning": "store this secret now"}
        if len(parts) == 5 and parts[:3] == ["api", "platform", "tokens"] and parts[4] == "revoke" and method == "POST":
            return self.identity.revoke(user, parts[3])
        if len(parts) == 5 and parts[:3] == ["api", "platform", "tokens"] and parts[4] == "rotate" and method == "POST":
            token, secret = self.identity.rotate(user, parts[3])
            return {"token": token, "secret": secret, "warning": "store this secret now"}
        if path == "/api/platform/audit" and method == "GET":
            query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(str(environ.get("QUERY_STRING") or ""))
            return {"events": self.identity.audits(user, int((query.get("limit") or ["100"])[0]))}
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        managed = path == "/api/platform/workers" or path.startswith("/api/platform/workspaces") or path.startswith("/api/platform/memberships") or path.startswith("/api/platform/tokens") or path.startswith("/api/platform/audit")
        if not managed:
            return self.base(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            self.base.origin(environ)
            user = self.base.user(environ)
            method = str(environ.get("REQUEST_METHOD") or "GET").upper()
            value = self.dispatch_admin(environ, user, path, method)
            status = 201 if method == "POST" and path in {"/api/platform/workspaces", "/api/platform/memberships", "/api/platform/tokens"} else 200
            return self.response(environ, start_response, status, value, rid)
        except PermissionError as exc:
            error = HTTPError(403, "forbidden", str(exc))
        except HTTPError as exc:
            error = exc
        except KeyError as exc:
            error = HTTPError(404, "not_found", str(exc).strip("'") or "resource not found")
        except ValueError as exc:
            error = HTTPError(400, "bad_request", str(exc))
        body = self.base.json({"error": {"code": error.code, "message": error.message}, "request_id": rid})
        try:
            headers = [("Content-Type", "application/json; charset=utf-8"), *self.base.security_headers(environ, rid), *error.headers]
        except HTTPError:
            headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"), ("X-Request-ID", rid), *error.headers]
        headers.append(("Content-Length", str(len(body))))
        start_response(f"{error.status} {STATUS.get(error.status, 'Unknown')}", headers)
        return [body]


application = ProductionApplication()
