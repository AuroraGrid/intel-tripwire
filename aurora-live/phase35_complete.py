from __future__ import annotations

import os
import uuid

from phase34_complete import Phase34Application
from phase35_ingestion import ImageryIngestionEngine, IngestionStore
from phase35_sources import adapter_names, build_adapter
from platform_wsgi import HTTPError, RID_RE


class Phase35Application(Phase34Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ingestion_store = IngestionStore(os.getenv("AURORA_INGESTION_DB", ":memory:"))
        self.ingestion = ImageryIngestionEngine(self.imagery, self.ingestion_store)

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-ingestion.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 35,
                        "capability": "real official-source imagery ingestion with validation, persistence, and health qualification",
                        "adapters": list(adapter_names()),
                        "pipeline": [
                            "discover",
                            "fetch",
                            "allowlist",
                            "validate",
                            "hash",
                            "timestamp",
                            "freshness",
                            "persist",
                            "publish",
                        ],
                        "registration_is_not_evidence": True,
                    },
                    rid,
                )

            if path == "/api/public/ingestion/adapters" and method == "GET":
                return self._response(environ, start_response, 200, {"adapters": list(adapter_names())}, rid)

            if path == "/api/public/ingestion/runs" and method == "GET":
                query = self._query(environ)
                rows = self.ingestion_store.runs(int(self._value(query, "limit", "50")))
                return self._response(environ, start_response, 200, {"runs": rows, "total": len(rows)}, rid)

            if path == "/api/public/ingestion/observations" and method == "GET":
                query = self._query(environ)
                rows = self.ingestion_store.observations(
                    self._value(query, "source_id", ""),
                    int(self._value(query, "limit", "100")),
                )
                return self._response(environ, start_response, 200, {"observations": rows, "total": len(rows)}, rid)

            if path == "/api/platform/ingestion/run" and method == "POST":
                self._user(environ)
                query = self._query(environ)
                requested = self._value(query, "adapter", "")
                names = [requested] if requested else list(adapter_names())
                adapters = [build_adapter(name) for name in names]
                result = self.ingestion.run_many(adapters)
                status = 200 if result["all_successful"] else 207
                return self._response(environ, start_response, status, result, rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase35Application()
