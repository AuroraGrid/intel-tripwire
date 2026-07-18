from __future__ import annotations

import uuid

from phase14_complete import Phase14Application
from phase15_mesh import SensorMesh
from platform_wsgi import HTTPError, RID_RE


class Phase15Application(Phase14Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mesh = SensorMesh(self.store)

    def _mesh_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/mesh/sensors":
            if method == "GET":
                return 200, {"sensors": self.mesh.sensors(actor, value("domain"))}
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.mesh.register(actor, body())
        if path == "/api/platform/mesh/health" and method == "GET":
            return 200, self.mesh.health(actor, value("sensor_id"))
        if path == "/api/platform/mesh/coverage" and method == "GET":
            return 200, self.mesh.coverage(actor)
        if path == "/api/platform/mesh/observations" and method == "GET":
            return 200, {"observations": self.mesh.observations(actor, value("domain"), int(value("limit", "100")))}
        if len(parts) == 6 and parts[:4] == ["api", "platform", "mesh", "sensors"]:
            sensor_id, action = parts[4], parts[5]
            if action == "health" and method == "POST":
                self.store.identity.require(actor, "write")
                return 200, self.mesh.record_health(actor, sensor_id, body())
            if action == "observations" and method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                return 201, self.mesh.ingest_observations(actor, sensor_id, list(payload.get("observations") or []))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/mesh"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._mesh_response(path, method, actor, environ)
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


application = Phase15Application()
