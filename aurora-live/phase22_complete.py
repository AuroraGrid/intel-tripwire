from __future__ import annotations

import uuid

from phase21_complete import Phase21Application
from phase22_forecasting import AutonomousForecastEngine
from platform_wsgi import HTTPError, RID_RE


class Phase22Application(Phase21Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.autonomous_forecasts = AutonomousForecastEngine(
            self.store, self.forecasts, self.detection, self.routes
        )

    def _autonomous_forecast_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/autonomous-forecasts/base-rates":
            if method == "GET":
                domain = value("domain")
                outcome_type = value("outcome_type")
                if not domain or not outcome_type:
                    raise ValueError("domain and outcome_type required")
                return 200, self.autonomous_forecasts.base_rate(
                    actor, domain, outcome_type
                )
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.autonomous_forecasts.set_base_rate(actor, body())

        if path == "/api/platform/autonomous-forecasts/propose" and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 201, self.autonomous_forecasts.propose(
                actor,
                str(payload.get("subject_type") or ""),
                str(payload.get("subject_id") or ""),
            )

        if path == "/api/platform/autonomous-forecasts/process" and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 200, self.autonomous_forecasts.process(
                actor, int(payload.get("limit", 100))
            )

        if path == "/api/platform/autonomous-forecasts/candidates" and method == "GET":
            return 200, {
                "candidates": self.autonomous_forecasts.candidates(
                    actor, value("state"), int(value("limit", "100"))
                )
            }

        if path == "/api/platform/autonomous-forecasts/scorecard" and method == "GET":
            return 200, self.autonomous_forecasts.scorecard(actor)

        if (
            len(parts) == 5
            and parts[:4]
            == ["api", "platform", "autonomous-forecasts", "candidates"]
            and method == "GET"
        ):
            return 200, self.autonomous_forecasts.candidate(actor, parts[4])

        if (
            len(parts) == 6
            and parts[:4]
            == ["api", "platform", "autonomous-forecasts", "candidates"]
        ):
            candidate_id, action = parts[4], parts[5]
            if action == "revisions" and method == "GET":
                return 200, {
                    "revisions": self.autonomous_forecasts.revisions(
                        actor, candidate_id
                    )
                }
            if method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                rationale = str(payload.get("rationale") or "")
                if action == "approve":
                    return 200, self.autonomous_forecasts.approve(
                        actor, candidate_id, rationale
                    )
                if action == "suppress":
                    return 200, self.autonomous_forecasts.suppress(
                        actor, candidate_id, rationale
                    )
                if action == "reopen":
                    return 200, self.autonomous_forecasts.reopen(
                        actor, candidate_id, rationale
                    )
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/autonomous-forecasts"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._autonomous_forecast_response(
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


application = Phase22Application()
