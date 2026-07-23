from __future__ import annotations

import uuid

from phase20_complete import Phase20Application
from phase21_routes import RouteIntelligence
from platform_wsgi import HTTPError, RID_RE


class Phase21Application(Phase20Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.routes = RouteIntelligence(self.store, self.operating_picture)

    def _route_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/routes/nodes" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.routes.upsert_node(actor, body())
        if path == "/api/platform/routes/edges" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.routes.upsert_edge(actor, body())
        if path == "/api/platform/routes/disruptions" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.routes.record_disruption(actor, body())
        if path == "/api/platform/routes/plans" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.routes.create_plan(actor, body())
        if path == "/api/platform/routes/plans" and method == "GET":
            return 200, {"plans": self.routes.plans(
                actor, value("status", "ACTIVE"), int(value("limit", "100"))
            )}
        if path == "/api/platform/routes/import-infrastructure" and method == "POST":
            self.store.identity.require(actor, "write")
            return 200, self.routes.import_infrastructure(
                actor, int(body().get("limit", 5000))
            )
        if path == "/api/platform/routes/recalculate" and method == "POST":
            self.store.identity.require(actor, "write")
            return 200, self.routes.recalculate_active(
                actor, int(body().get("limit", 100))
            )
        if path == "/api/platform/routes/scorecard" and method == "GET":
            return 200, self.routes.scorecard(actor)

        if len(parts) == 5 and parts[:4] == ["api", "platform", "routes", "nodes"]:
            if method == "GET":
                return 200, self.routes.node(actor, parts[4])
        if len(parts) == 5 and parts[:4] == ["api", "platform", "routes", "edges"]:
            if method == "GET":
                return 200, self.routes.edge(actor, parts[4])
        if len(parts) == 5 and parts[:4] == ["api", "platform", "routes", "disruptions"]:
            if method == "GET":
                return 200, self.routes.disruption(actor, parts[4])
        if len(parts) == 5 and parts[:4] == ["api", "platform", "routes", "plans"]:
            if method == "GET":
                return 200, self.routes.plan(actor, parts[4])
        if len(parts) == 6 and parts[:4] == ["api", "platform", "routes", "plans"]:
            plan_id, action = parts[4], parts[5]
            if action == "recalculate" and method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                return 200, self.routes.recalculate(
                    actor, plan_id, str(payload.get("reason") or "Analyst reassessment")
                )
            if action == "revisions" and method == "GET":
                return 200, {"revisions": self.routes.revisions(actor, plan_id)}
            if action == "geojson" and method == "GET":
                return 200, self.routes.geojson(actor, plan_id)
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/routes"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._route_response(path, method, actor, environ)
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


application = Phase21Application()
