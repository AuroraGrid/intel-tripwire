from __future__ import annotations

import uuid

from phase19_complete import Phase19Application
from phase20_operating_picture import LiveOperatingPicture
from platform_wsgi import HTTPError, RID_RE


class Phase20Application(Phase19Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.operating_picture = LiveOperatingPicture(self.store, self.mesh)

    def _operating_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]
        filters = {
            "asset_type": value("asset_type"),
            "infrastructure_type": value("infrastructure_type"),
            "country_code": value("country_code"),
            "after": value("after"),
            "min_lat": value("min_lat"),
            "max_lat": value("max_lat"),
            "min_lon": value("min_lon"),
            "max_lon": value("max_lon"),
            "include_infrastructure": value("include_infrastructure", "true"),
            "limit": value("limit", "1000"),
        }

        if path == "/api/platform/operating/assets" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.operating_picture.upsert_asset(actor, body())
        if path == "/api/platform/operating/positions" and method == "GET":
            return 200, {"positions": self.operating_picture.positions(actor, filters)}
        if path == "/api/platform/operating/infrastructure" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.operating_picture.upsert_infrastructure(actor, body())
        if path == "/api/platform/operating/infrastructure" and method == "GET":
            return 200, {"infrastructure": self.operating_picture.infrastructure_list(actor, filters)}
        if path == "/api/platform/operating/anomalies" and method == "GET":
            return 200, {"anomalies": self.operating_picture.anomalies(
                actor, value("state", "OPEN"), int(value("limit", "100"))
            )}
        if path == "/api/platform/operating/geojson" and method == "GET":
            return 200, self.operating_picture.geojson(actor, filters)
        if path == "/api/platform/operating/process" and method == "POST":
            self.store.identity.require(actor, "write")
            return 200, self.operating_picture.process_mesh(
                actor, int(body().get("limit", 1000))
            )
        if path == "/api/platform/operating/scorecard" and method == "GET":
            return 200, self.operating_picture.scorecard(actor)

        if len(parts) == 5 and parts[:4] == ["api", "platform", "operating", "assets"]:
            if method == "GET":
                return 200, self.operating_picture.asset(actor, parts[4])
        if len(parts) == 6 and parts[:4] == ["api", "platform", "operating", "assets"]:
            asset_id, action = parts[4], parts[5]
            if action == "positions" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.operating_picture.ingest_position(actor, asset_id, body())
            if action == "track" and method == "GET":
                return 200, self.operating_picture.track(
                    actor, asset_id, int(value("limit", "1000"))
                )
        if len(parts) == 5 and parts[:4] == ["api", "platform", "operating", "infrastructure"]:
            if method == "GET":
                return 200, self.operating_picture.infrastructure(actor, parts[4])
        if len(parts) == 6 and parts[:4] == ["api", "platform", "operating", "anomalies"]:
            anomaly_id, action = parts[4], parts[5]
            if action == "review" and method == "POST":
                self.store.identity.require(actor, "write")
                return 200, self.operating_picture.review_anomaly(actor, anomaly_id, body())
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/operating"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._operating_response(path, method, actor, environ)
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


application = Phase20Application()
