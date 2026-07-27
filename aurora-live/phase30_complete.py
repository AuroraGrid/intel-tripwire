from __future__ import annotations

import uuid

from phase29_complete import Phase29Application
from phase30_distribution import DistributionHub
from platform_wsgi import HTTPError, RID_RE


class Phase30Application(Phase29Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.distribution = DistributionHub(self.store)

    def _distribution_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/distribution/channels" and method == "GET":
            return 200, {"channels": self.distribution.channels(actor, value("active"), int(value("limit", "100")))}
        if path == "/api/platform/distribution/channels" and method == "POST":
            return 201, self.distribution.upsert_channel(actor, body())
        if path == "/api/platform/distribution/packages" and method == "GET":
            return 200, {"packages": self.distribution.packages(actor, value("classification"), int(value("limit", "100")))}
        if path == "/api/platform/distribution/packages" and method == "POST":
            return 201, self.distribution.create_package(actor, body())
        if path == "/api/platform/distribution/deliveries" and method == "GET":
            return 200, {"deliveries": self.distribution.deliveries(actor, value("package_id"), int(value("limit", "100")))}
        if path == "/api/platform/distribution/deliveries" and method == "POST":
            return 202, self.distribution.queue_delivery(actor, body())
        if path == "/api/platform/distribution/deliveries/status" and method == "POST":
            return 200, self.distribution.record_delivery(actor, body())
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if path == "/.well-known/aurora-distribution.json" and method == "GET":
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 30,
                    "purpose": "deterministic intelligence packaging and controlled distribution",
                    "controls": {
                        "workspace_scoped": True,
                        "sha256_manifests": True,
                        "classification_clearance_enforced": True,
                        "idempotent_delivery_queue": True,
                        "network_delivery_performed": False,
                        "external_ai_required": False,
                    },
                },
                rid,
            )

        namespace = path == "/api/platform/distribution" or path.startswith("/api/platform/distribution/")
        if not namespace:
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._distribution_response(path, method, actor, environ)
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


application = Phase30Application()
