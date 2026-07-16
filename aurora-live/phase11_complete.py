from __future__ import annotations

import urllib.parse
import uuid

from phase10_complete import Phase10Application
from phase11_outputs import SystemOutputStore
from phase11_store import ForecastLedger
from platform_wsgi import HTTPError, RID_RE


class Phase11Application(Phase10Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.forecasts = ForecastLedger(self.store)
        self.outputs = SystemOutputStore(self.store)

    @staticmethod
    def _query(environ):
        return urllib.parse.parse_qs(str(environ.get("QUERY_STRING") or ""), keep_blank_values=True)

    @staticmethod
    def _value(query, name, default=""):
        return (query.get(name) or [default])[0]

    def _forecast_response(self, path, method, actor, environ):
        parts = [part for part in path.split("/") if part]
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        if method == "GET" and path == "/api/platform/forecasts":
            return 200, {"forecasts": self.forecasts.list(actor, value("status"), int(value("limit", "100")))}
        if method == "GET" and path == "/api/platform/forecasts/metrics":
            return 200, self.forecasts.metrics(actor, value("source"))
        if method == "POST" and path == "/api/platform/forecasts":
            self.store.identity.require(actor, "write")
            return 201, self.forecasts.create(actor, self.platform.body(environ))
        if len(parts) >= 4 and parts[:3] == ["api", "platform", "forecasts"]:
            forecast_id = parts[3]
            if method == "GET" and len(parts) == 4:
                return 200, self.forecasts.get(actor, forecast_id)
            if method == "POST" and len(parts) == 5 and parts[4] == "revisions":
                self.store.identity.require(actor, "write")
                return 201, self.forecasts.revise(actor, forecast_id, self.platform.body(environ))
            if method == "POST" and len(parts) == 5 and parts[4] == "resolve":
                self.store.identity.require(actor, "write")
                payload = self.platform.body(environ)
                if "outcome" not in payload:
                    raise ValueError("outcome required")
                return 200, self.forecasts.resolve(actor, forecast_id, payload["outcome"], payload.get("note", ""))
        raise HTTPError(404, "not_found", "route not found")

    def _output_response(self, path, method, actor, environ):
        parts = [part for part in path.split("/") if part]
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        if method == "GET" and path == "/api/platform/system-outputs":
            return 200, {"outputs": self.outputs.list(actor, value("module"), value("subject_type"), value("subject_id"), int(value("limit", "100")))}
        if method == "POST" and path == "/api/platform/system-outputs":
            self.store.identity.require(actor, "write")
            return 201, self.outputs.create(actor, self.platform.body(environ))
        if len(parts) >= 4 and parts[:3] == ["api", "platform", "system-outputs"]:
            output_id = parts[3]
            if method == "GET" and len(parts) == 4:
                return 200, self.outputs.get(actor, output_id)
            if method == "POST" and len(parts) == 5 and parts[4] == "reviews":
                self.store.identity.require(actor, "write")
                return 201, self.outputs.review(actor, output_id, self.platform.body(environ))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        managed_forecast = path.startswith("/api/platform/forecasts")
        managed_outputs = path.startswith("/api/platform/system-outputs")
        if not (managed_forecast or managed_outputs):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            if managed_forecast:
                status, payload = self._forecast_response(path, method, actor, environ)
            else:
                status, payload = self._output_response(path, method, actor, environ)
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


application = Phase11Application()
