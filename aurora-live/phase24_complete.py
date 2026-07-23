from __future__ import annotations

import time
import uuid

from phase23_complete import Phase23Application
from phase24_ecosystem import DeveloperEcosystem
from phase24_mcp import AuroraMCPServer
from platform_wsgi import HTTPError, RID_RE


class Phase24Application(Phase23Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.developer = DeveloperEcosystem(self.store)
        self.mcp = AuroraMCPServer(
            self.command_center,
            self.detection,
            self.routes,
            self.autonomous_forecasts,
            self.integrity,
            self.mesh,
            self.fabric,
        )

    def _developer_actor(self, environ):
        supplied = str(environ.get("HTTP_X_AURORA_API_KEY") or "").strip()
        authorization = str(environ.get("HTTP_AUTHORIZATION") or "")
        if not supplied and authorization.lower().startswith("bearer "):
            candidate = authorization.split(None, 1)[1].strip()
            if candidate.startswith("aurora_sk_"):
                supplied = candidate
        if supplied:
            return self.developer.authenticate(supplied)
        return self._user(environ)

    def _page(self, items, cursor, limit):
        offset = self.developer.decode_cursor(cursor)
        limit = max(1, min(200, int(limit)))
        page = items[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "data": page,
            "meta": {
                "limit": limit,
                "returned": len(page),
                "next_cursor": (
                    self.developer.encode_cursor(next_offset)
                    if next_offset < len(items)
                    else None
                ),
            },
        }

    def _developer_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        parts = [part for part in path.split("/") if part]
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/developer/clients":
            if method == "GET":
                return 200, {"clients": self.developer.clients(actor)}
            if method == "POST":
                self.store.identity.require(actor, "admin")
                return 201, self.developer.create_client(actor, body())
        if path == "/api/platform/developer/scorecard" and method == "GET":
            return 200, self.developer.scorecard(actor)
        if (
            len(parts) == 6
            and parts[:4] == ["api", "platform", "developer", "clients"]
            and parts[5] == "revoke"
            and method == "POST"
        ):
            self.store.identity.require(actor, "admin")
            return 200, self.developer.revoke(actor, parts[4])
        raise HTTPError(404, "not_found", "route not found")

    def _v1_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        parts = [part for part in path.split("/") if part]
        cursor = value("cursor")
        limit = int(value("limit", "100"))

        if method == "GET":
            self.developer.require_scope(actor, "read")
            if path == "/api/v1/detections":
                items = self.detection.detections(
                    actor, value("state"), value("domain"), 500
                )
                return 200, self._page(items, cursor, limit)
            if len(parts) == 4 and parts[:3] == ["api", "v1", "detections"]:
                return 200, {"data": self.detection.detection(actor, parts[3])}
            if path == "/api/v1/routes":
                items = self.routes.plans(actor, value("status"), 500)
                return 200, self._page(items, cursor, limit)
            if len(parts) == 4 and parts[:3] == ["api", "v1", "routes"]:
                return 200, {"data": self.routes.plan(actor, parts[3])}
            if path == "/api/v1/forecast-candidates":
                items = self.autonomous_forecasts.candidates(
                    actor, value("state"), 500
                )
                return 200, self._page(items, cursor, limit)
            if (
                len(parts) == 4
                and parts[:3] == ["api", "v1", "forecast-candidates"]
            ):
                return 200, {
                    "data": self.autonomous_forecasts.candidate(
                        actor, parts[3]
                    )
                }
            if path == "/api/v1/events":
                return 200, {
                    "data": self.fabric.stream(
                        actor,
                        int(value("after", "0")),
                        limit,
                        value("event_type"),
                    )
                }
            if path == "/api/v1/search":
                return 200, {
                    "data": self.command_center.search(
                        actor, value("query"), limit
                    )
                }
            if path == "/api/v1/source-health":
                return 200, {
                    "data": {
                        "health": self.mesh.health(actor),
                        "coverage": self.mesh.coverage(actor),
                    }
                }
            if path == "/api/v1/contradictions":
                return 200, {
                    "data": self.integrity.contradictions(
                        actor, value("state", "OPEN"), limit
                    )
                }

        if (
            method == "POST"
            and len(parts) == 5
            and parts[:3] == ["api", "v1", "forecast-candidates"]
            and parts[4] == "approve"
        ):
            self.developer.require_scope(actor, "forecasts:write")
            payload = self.platform.body(environ)
            key = str(environ.get("HTTP_IDEMPOTENCY_KEY") or "")
            status, response, replayed = self.developer.idempotent(
                actor,
                key,
                f"forecast-candidates/{parts[3]}/approve",
                payload,
                lambda: (
                    200,
                    {
                        "data": self.autonomous_forecasts.approve(
                            actor,
                            parts[3],
                            str(payload.get("rationale") or ""),
                        )
                    },
                ),
            )
            response["meta"] = {"idempotent_replay": replayed}
            return status, response
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        if path == "/api/v1/openapi.json" and method == "GET":
            return self._json_document(
                environ, start_response, self.developer.openapi(), rid
            )
        if path == "/.well-known/aurora-agent.json" and method == "GET":
            return self._json_document(
                environ, start_response, self.developer.agent_manifest(), rid
            )
        if path == "/mcp/manifest.json" and method == "GET":
            return self._json_document(
                environ, start_response, self.mcp.manifest(), rid
            )

        managed_developer = path.startswith("/api/platform/developer")
        managed_v1 = path.startswith("/api/v1/")
        managed_mcp = path == "/mcp"
        if not (managed_developer or managed_v1 or managed_mcp):
            return super().__call__(environ, start_response)

        started = time.perf_counter()
        actor = None
        surface = "developer" if managed_developer else "mcp" if managed_mcp else "api"
        try:
            actor = self._user(environ) if managed_developer else self._developer_actor(environ)
            if managed_developer:
                status, payload = self._developer_response(
                    path, method, actor, environ
                )
            elif managed_mcp:
                if method == "GET":
                    status, payload = 200, self.mcp.manifest()
                elif method == "POST":
                    self.developer.require_scope(actor, "read")
                    status, payload = 200, self.mcp.handle(
                        actor, self.platform.body(environ)
                    )
                else:
                    raise HTTPError(
                        405, "method_not_allowed", "method not allowed"
                    )
            else:
                status, payload = self._v1_response(
                    path, method, actor, environ
                )
            self.developer.record_request(
                actor,
                surface,
                f"{method} {path}",
                "success",
                (time.perf_counter() - started) * 1000,
            )
            return self._response(
                environ, start_response, status, payload, rid
            )
        except PermissionError as exc:
            if actor:
                self.developer.record_request(
                    actor,
                    surface,
                    f"{method} {path}",
                    "forbidden",
                    (time.perf_counter() - started) * 1000,
                )
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


application = Phase24Application()
