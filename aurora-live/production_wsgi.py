from __future__ import annotations

import uuid

from platform_wsgi import HTTPError, RID_RE, STATUS, create_application
from worker_state import WorkerState


class ProductionApplication:
    def __init__(self, base=None):
        self.base = base or create_application()
        self.worker_state = WorkerState(self.base.store)

    def __call__(self, environ, start_response):
        if str(environ.get("PATH_INFO") or "") != "/api/platform/workers":
            return self.base(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            self.base.origin(environ)
            user = self.base.user(environ)
            self.base.role(user, "admin")
            body = self.base.json(self.worker_state.status(int(__import__("os").getenv("AURORA_WORKER_STALE_SECONDS", "120"))))
            status = 200
            headers = [("Content-Type", "application/json; charset=utf-8"), *self.base.security_headers(environ, rid)]
        except HTTPError as exc:
            status = exc.status
            body = self.base.json({"error": {"code": exc.code, "message": exc.message}, "request_id": rid})
            try:
                headers = [("Content-Type", "application/json; charset=utf-8"), *self.base.security_headers(environ, rid), *exc.headers]
            except HTTPError:
                headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"), ("X-Request-ID", rid), *exc.headers]
        headers.append(("Content-Length", str(len(body))))
        start_response(f"{status} {STATUS.get(status, 'Unknown')}", headers)
        return [body]


application = ProductionApplication()
