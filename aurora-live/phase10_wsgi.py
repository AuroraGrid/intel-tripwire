from __future__ import annotations

import os
import urllib.parse
import uuid
from pathlib import Path

from phase8_wsgi import Phase8Application
from phase10_geo import GeoIndex, GeoQuery, parse_bbox
from platform_wsgi import HTTPError, RID_RE


ROOT = Path(__file__).resolve().parent


class Phase10Application(Phase8Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.geo_path = ROOT / "static" / "phase10.js"

    def _html(self, environ, start_response, rid):
        self.platform.origin(environ)
        try:
            page = self.dashboard_path.read_text(encoding="utf-8")
            phase8 = self.enhancement_path.read_text(encoding="utf-8")
            phase10 = self.geo_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HTTPError(404, "not_found", "dashboard enhancement not found") from exc
        body = page.replace("</body>", f"<script>{phase8}</script><script>{phase10}</script></body>").encode("utf-8")
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            *self.platform.security_headers(environ, rid, "public, max-age=60"),
            ("Content-Length", str(len(body))),
        ]
        start_response("200 OK", headers)
        return [body]

    @staticmethod
    def _value(query: dict[str, list[str]], name: str, default: str = "") -> str:
        return (query.get(name) or [default])[0]

    def _incidents(self, query: dict[str, list[str]]) -> list[dict]:
        limit = min(10000, max(1, int(self._value(query, "source_limit", "5000"))))
        return self.platform.ops.incidents(limit=limit)

    def _geo_query(self, query: dict[str, list[str]]) -> GeoQuery:
        csv = lambda name: frozenset(value.strip() for value in self._value(query, name).split(",") if value.strip())
        return GeoQuery(
            bbox=parse_bbox(self._value(query, "bbox")),
            categories=csv("categories"),
            severities=csv("severities"),
            since=self._value(query, "since"),
            until=self._value(query, "until"),
            zoom=max(0, min(12, int(self._value(query, "zoom", "2")))),
            limit=min(10000, max(1, int(self._value(query, "limit", "5000")))),
        )

    def _geo(self, path: str, query: dict[str, list[str]]):
        incidents = self._incidents(query)
        index = GeoIndex(incidents)
        geo_query = self._geo_query(query)
        if path == "/api/platform/geo/incidents":
            rows = index.filter(geo_query)
            return {"incidents": rows, "count": len(rows)}
        if path == "/api/platform/geo/clusters":
            rows = index.clusters(geo_query)
            return {"clusters": rows, "count": len(rows), "zoom": geo_query.zoom}
        if path == "/api/platform/geo/heat":
            rows = index.heat(geo_query, int(self._value(query, "precision", "3")))
            return {"cells": rows, "count": len(rows)}
        if path == "/api/platform/geo/summary":
            rows = index.filter(geo_query)
            return {
                "geolocated_incidents": len(index.items),
                "filtered_incidents": len(rows),
                "coverage_ratio": round(len(index.items) / max(1, len(incidents)), 4),
                "total_incidents": len(incidents),
            }
        if path.startswith("/api/platform/geo/dossiers/"):
            country = urllib.parse.unquote(path.rsplit("/", 1)[-1]).strip()
            if not country:
                raise HTTPError(400, "bad_request", "country is required")
            return index.dossier(country, incidents)
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        managed = path.startswith("/api/platform/geo/")
        if not managed:
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            if method != "GET":
                raise HTTPError(405, "method_not_allowed", "method not allowed", [("Allow", "GET")])
            self._user(environ)
            query = urllib.parse.parse_qs(str(environ.get("QUERY_STRING") or ""), keep_blank_values=True)
            return self._response(environ, start_response, 200, self._geo(path, query), rid)
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except ValueError as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except Exception:
            return self._error(environ, start_response, rid, HTTPError(500, "internal_error", "internal server error"))


application = Phase10Application()
