from __future__ import annotations

import uuid

from phase15_complete import Phase15Application
from phase16_detection import DetectionEngine
from platform_wsgi import HTTPError, RID_RE


class Phase16Application(Phase15Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.detection = DetectionEngine(self.store, self.mesh, self.integrity)

    def _detection_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/detections":
            if method == "GET":
                return 200, {"detections": self.detection.detections(actor, value("state"), value("domain"), int(value("limit", "100")))}
        if path == "/api/platform/detections/process" and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 200, self.detection.process_pending(actor, int(payload.get("limit", 100)))
        if path == "/api/platform/detections/scorecard" and method == "GET":
            return 200, self.detection.scorecard(actor)
        if len(parts) == 4 and parts[:3] == ["api", "platform", "detections"] and method == "GET":
            return 200, self.detection.detection(actor, parts[3])
        if len(parts) == 5 and parts[:3] == ["api", "platform", "detections"]:
            detection_id, action = parts[3], parts[4]
            if action == "review" and method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                return 200, self.detection.review(actor, detection_id, payload.get("review_state", ""), payload.get("reason", ""))
            if action == "reassess" and method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                return 200, self.detection.reassess(actor, detection_id, payload.get("reason", "Manual reassessment"))
        if len(parts) == 5 and parts[:4] == ["api", "platform", "observations"] and parts[4] and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 200, self.detection.correlate(actor, parts[4], bool(payload.get("force_new", False)))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        managed = path.startswith("/api/platform/detections") or path.startswith("/api/platform/observations/")
        if not managed:
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._detection_response(path, method, actor, environ)
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


application = Phase16Application()
