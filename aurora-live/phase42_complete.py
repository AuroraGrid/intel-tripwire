from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path

from phase41_complete import Phase41Application
from platform_wsgi import HTTPError, RID_RE

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Phase42Application(Phase41Application):
    """Production Global Operating Picture / Incident Room / Source Health public UI."""

    def _static_file(self, environ, start_response, relative: str, rid: str):
        path = (STATIC_DIR / relative).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.is_file():
            raise HTTPError(404, "not_found", "static asset not found")
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(data))),
            ("X-Request-ID", rid),
            ("Cache-Control", "public, max-age=60"),
        ]
        start_response("200 OK", headers)
        return [data]

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase42.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 42,
                        "capability": "production Global Operating Picture, Incident Room, and Source Health interfaces",
                        "public_ui": ["/", "/gop", "/incident-room", "/source-health"],
                        "ui_binds_to_runtime_apis": True,
                        "no_hardcoded_live_badges": True,
                    },
                    rid,
                )

            if method == "GET" and path in {"/", "/gop", "/gop.html"}:
                return self._static_file(environ, start_response, "gop.html", rid)
            if method == "GET" and path in {"/incident-room", "/incident-room.html"}:
                return self._static_file(environ, start_response, "gop.html", rid)
            if method == "GET" and path in {"/source-health", "/source-health.html"}:
                return self._static_file(environ, start_response, "gop.html", rid)
            if method == "GET" and path == "/static/aurora-live.js":
                return self._static_file(environ, start_response, "aurora-live.js", rid)
            if method == "GET" and path == "/static/gop.html":
                return self._static_file(environ, start_response, "gop.html", rid)
            if method == "GET" and path.startswith("/static/"):
                return self._static_file(environ, start_response, path[len("/static/") :], rid)

            if path == "/api/public/ui/bootstrap" and method == "GET":
                product = self._product_manifest()
                return self._response(
                    environ,
                    start_response,
                    200,
                    {
                        "phase": 42,
                        "product_phase": product.get("phase"),
                        "counts": product.get("counts"),
                        "interfaces": ["Global Operating Picture", "Incident Room", "Source Health"],
                        "endpoints": {
                            "capabilities": "/api/public/product/capabilities",
                            "gaps": "/api/public/product/gaps",
                            "gop": "/api/public/global-operating-picture",
                            "source_health": "/api/public/product/capabilities",
                            "transport_health": "/api/public/transport/health",
                            "infrastructure_health": "/api/public/infrastructure/health",
                            "markets_health": "/api/public/markets/health",
                            "replay": "/api/public/replay",
                        },
                    },
                    rid,
                )

        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))

        return super().__call__(environ, start_response)


application = Phase42Application()
