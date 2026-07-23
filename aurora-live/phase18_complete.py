from __future__ import annotations

import uuid

from phase17_complete import Phase17Application
from phase18_graph import EntityGraph
from platform_wsgi import HTTPError, RID_RE


class Phase18Application(Phase17Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.graph_engine = EntityGraph(self.store, self.detection, self.fabric)

    def _graph_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/graph/entities":
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.graph_engine.upsert_entity(actor, body())
        if path == "/api/platform/graph/relations" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.graph_engine.relate(actor, body())
        if path == "/api/platform/graph/query" and method == "GET":
            return 200, self.graph_engine.graph(actor, value("entity_id"), int(value("limit", "500")))
        if path == "/api/platform/graph/process" and method == "POST":
            self.store.identity.require(actor, "write")
            return 200, self.graph_engine.process_fabric(actor, int(body().get("limit", 100)))
        if path == "/api/platform/graph/scorecard" and method == "GET":
            return 200, self.graph_engine.scorecard(actor)
        if len(parts) == 5 and parts[:4] == ["api", "platform", "graph", "entities"]:
            entity_id = parts[4]
            if method == "GET":
                return 200, self.graph_engine.entity(actor, entity_id, include_inactive=True)
        if len(parts) == 6 and parts[:4] == ["api", "platform", "graph", "entities"]:
            entity_id, action = parts[4], parts[5]
            if action == "aliases" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.graph_engine.add_alias(actor, entity_id, body())
            if action == "merge" and method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                return 200, self.graph_engine.merge(actor, entity_id, payload.get("target_id", ""), payload.get("reason", ""))
            if action == "split" and method == "POST":
                self.store.identity.require(actor, "write")
                return 200, self.graph_engine.split(actor, entity_id, body())
            if action == "revisions" and method == "GET":
                return 200, {"revisions": self.graph_engine.revisions(actor, "entity", entity_id, int(value("limit", "100")))}
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/graph"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._graph_response(path, method, actor, environ)
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


application = Phase18Application()
