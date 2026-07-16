from __future__ import annotations

import uuid
from pathlib import Path

from phase11_complete import Phase11Application
from phase11_operations import DecisionOperations
from platform_wsgi import HTTPError, RID_RE

ROOT = Path(__file__).resolve().parent


class Phase11CApplication(Phase11Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decisions = DecisionOperations(self.store, self.forecasts, self.outputs)
        self.interface_path = ROOT / "static" / "phase11.js"

    def _html(self, environ, start_response, rid):
        self.platform.origin(environ)
        try:
            page = self.dashboard_path.read_text(encoding="utf-8")
            phase8 = self.enhancement_path.read_text(encoding="utf-8")
            phase10 = self.geo_path.read_text(encoding="utf-8")
            phase11 = self.interface_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HTTPError(404, "not_found", "dashboard enhancement not found") from exc
        body = page.replace("</body>", f"<script>{phase8}</script><script>{phase10}</script><script>{phase11}</script></body>").encode("utf-8")
        headers = [("Content-Type", "text/html; charset=utf-8"), *self.platform.security_headers(environ, rid, "public, max-age=60"), ("Content-Length", str(len(body)))]
        start_response("200 OK", headers)
        return [body]

    def _decision_response(self, path, method, actor, environ):
        parts = [part for part in path.split("/") if part]
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        if method == "GET" and path == "/api/platform/forecast-portfolio":
            return 200, self.decisions.portfolio(actor)
        if method == "GET" and path == "/api/platform/hall-of-record":
            return 200, self.decisions.hall_of_record(actor, int(value("limit", "200")))
        if method == "GET" and path == "/api/platform/alert-performance":
            return 200, self.decisions.performance(actor)
        if method == "POST" and path == "/api/platform/alert-outcomes":
            self.store.identity.require(actor, "write")
            return 201, self.decisions.record_alert_outcome(actor, self.platform.body(environ))
        if method == "POST" and path == "/api/platform/route-risk":
            self.store.identity.require(actor, "write")
            return 200, self.decisions.route_risk(actor, self.platform.body(environ))
        if len(parts) >= 5 and parts[:3] == ["api", "platform", "forecasts"] and parts[4] == "scenarios":
            forecast_id = parts[3]
            if method == "GET":
                return 200, self.decisions.scenario_graph(actor, forecast_id)
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.decisions.create_scenario(actor, forecast_id, self.platform.body(environ))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        managed = path in {
            "/api/platform/forecast-portfolio", "/api/platform/hall-of-record",
            "/api/platform/alert-performance", "/api/platform/alert-outcomes",
            "/api/platform/route-risk",
        } or (path.startswith("/api/platform/forecasts/") and path.endswith("/scenarios"))
        if not managed:
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._decision_response(path, method, actor, environ)
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


application = Phase11CApplication()
