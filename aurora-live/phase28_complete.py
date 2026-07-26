from __future__ import annotations

import uuid

from phase27_complete import Phase27Application
from phase28_accuracy import AccuracyHistory
from platform_wsgi import HTTPError, RID_RE


class Phase28Application(Phase27Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accuracy = AccuracyHistory(
            self.store,
            self.integrity,
            self.detection,
            self.forecasts,
        )

    def _accuracy_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/accuracy/outcomes" and method == "GET":
            return 200, {
                "outcomes": self.accuracy.outcomes(
                    actor,
                    value("subject_type"),
                    value("subject_id"),
                    value("domain"),
                    int(value("limit", "100")),
                )
            }
        if path == "/api/platform/accuracy/outcomes" and method == "POST":
            return 201, self.accuracy.record_outcome(actor, body())
        if path == "/api/platform/accuracy/scorecard" and method == "GET":
            return 200, self.accuracy.scorecard(actor, value("domain"))
        if path == "/api/platform/accuracy/cases" and method == "GET":
            return 200, {
                "cases": self.accuracy.cases(
                    actor,
                    value("domain"),
                    int(value("limit", "100")),
                )
            }
        if path == "/api/platform/accuracy/cases" and method == "POST":
            return 201, self.accuracy.record_case(actor, body())
        if path == "/api/platform/accuracy/analogs" and method == "GET":
            return 200, {
                "analogs": self.accuracy.analogs(
                    actor,
                    value("query"),
                    value("domain"),
                    int(value("limit", "10")),
                )
            }
        if (
            path == "/api/platform/accuracy/fingerprints"
            and method == "GET"
        ):
            return 200, {
                "fingerprints": self.accuracy.fingerprints(
                    actor, int(value("limit", "100"))
                )
            }
        if (
            path == "/api/platform/accuracy/fingerprints"
            and method == "POST"
        ):
            return 201, self.accuracy.record_fingerprint(actor, body())
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if (
            path == "/.well-known/aurora-accuracy-history.json"
            and method == "GET"
        ):
            return self._json_document(
                environ,
                start_response,
                {
                    "phase": 28,
                    "purpose": "accuracy and historical data moat",
                    "methodology": {
                        "outcomes_are_append_only": True,
                        "same_evidence_is_idempotent": True,
                        "forecast_metrics_reuse_phase11": True,
                        "analogs_are_deterministic": True,
                        "external_ai_required": False,
                        "source_reliability_is_not_silently_overwritten": True,
                    },
                },
                rid,
            )

        namespace = (
            path == "/api/platform/accuracy"
            or path.startswith("/api/platform/accuracy/")
        )
        if not namespace:
            return super().__call__(environ, start_response)
        try:
            actor = self._user(environ)
            status, payload = self._accuracy_response(
                path, method, actor, environ
            )
            return self._response(
                environ, start_response, status, payload, rid
            )
        except PermissionError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(403, "forbidden", str(exc)),
            )
        except KeyError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(
                    404,
                    "not_found",
                    str(exc).strip("'") or "resource not found",
                ),
            )
        except ValueError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(400, "bad_request", str(exc)),
            )
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except Exception:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(500, "internal_error", "internal server error"),
            )


application = Phase28Application()
