from __future__ import annotations

import json
import uuid

from phase12_complete import Phase12Application
from phase13_delivery import DeliveryLayer
from platform_wsgi import HTTPError, RID_RE


class Phase13Application(Phase12Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivery = DeliveryLayer(self.store, self.fusion)

    def _json_document(self, environ, start_response, payload, rid):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers = [("Content-Type", "application/json; charset=utf-8"), *self.platform.security_headers(environ, rid, "public, max-age=60"), ("Content-Length", str(len(body)))]
        start_response("200 OK", headers)
        return [body]

    def _delivery_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        parts = [part for part in path.split("/") if part]
        if path == "/api/platform/map-layers":
            if method == "GET":
                return 200, {"layers": self.delivery.layers(actor)}
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.delivery.create_layer(actor, self.platform.body(environ))
        if path == "/api/platform/timeline" and method == "GET":
            return 200, self.delivery.timeline(actor, int(value("limit", "500")))
        if len(parts) == 5 and parts[:3] == ["api", "platform", "map-layers"] and parts[4] == "snapshot":
            layer_id = parts[3]
            if method == "GET":
                return 200, self.delivery.latest_snapshot(actor, layer_id)
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.delivery.build_snapshot(actor, layer_id)
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        if path == "/openapi.json" and method == "GET":
            return self._json_document(environ, start_response, self.delivery.openapi(), rid)
        managed = path.startswith("/api/platform/map-layers") or path == "/api/platform/timeline"
        if not managed:
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._delivery_response(path, method, actor, environ)
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


application = Phase13Application()
