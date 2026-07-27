from __future__ import annotations

import uuid

from phase30_complete import Phase30Application
from phase31_benchmarking import ContinuousBenchmark
from platform_wsgi import HTTPError, RID_RE


class Phase31Application(Phase30Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.benchmarking = ContinuousBenchmark(self.store)

    def _benchmark_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/benchmarks/targets" and method == "GET":
            return 200, {"targets": self.benchmarking.targets(actor, int(value("limit", "100")))}
        if path == "/api/platform/benchmarks/targets" and method == "POST":
            return 201, self.benchmarking.upsert_target(actor, body())
        if path == "/api/platform/benchmarks/runs" and method == "GET":
            return 200, {"runs": self.benchmarking.runs(actor, value("target_id"), int(value("limit", "100")))}
        if path == "/api/platform/benchmarks/runs" and method == "POST":
            return 201, self.benchmarking.create_run(actor, body())
        if path == "/api/platform/benchmarks/latest" and method == "GET":
            return 200, self.benchmarking.latest(actor, value("target_id"))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if path == "/.well-known/aurora-benchmarking.json" and method == "GET":
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 31,
                    "purpose": "continuous evidence-backed competitive benchmarking",
                    "controls": {
                        "workspace_scoped": True,
                        "immutable_runs": True,
                        "finite_numeric_validation": True,
                        "external_evidence_required": True,
                        "deficit_alerts": True,
                        "superiority_self_certification": False,
                        "external_ai_required": False,
                    },
                },
                rid,
            )

        namespace = path == "/api/platform/benchmarks" or path.startswith("/api/platform/benchmarks/")
        if not namespace:
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._benchmark_response(path, method, actor, environ)
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


application = Phase31Application()
