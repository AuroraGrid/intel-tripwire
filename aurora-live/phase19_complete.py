from __future__ import annotations

import uuid

from phase18_complete import Phase18Application
from phase19_ai import GroqPublicEvidenceAssistant
from phase19_verification import MultimodalVerification
from platform_wsgi import HTTPError, RID_RE


class Phase19Application(Phase18Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.media = MultimodalVerification(self.store)
        self.groq = GroqPublicEvidenceAssistant(self.media)

    def _media_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        body = lambda: self.platform.body(environ)
        parts = [part for part in path.split("/") if part]

        if path == "/api/platform/media/assets" and method == "POST":
            self.store.identity.require(actor, "write")
            return 201, self.media.register_asset(actor, body())
        if path == "/api/platform/media/assets" and method == "GET":
            return 200, {"assets": self.media.list_assets(actor, {
                "media_type": value("media_type"),
                "status": value("status"),
                "review_state": value("review_state"),
                "classification": value("classification"),
                "limit": value("limit", "100"),
            })}
        if path == "/api/platform/media/process" and method == "POST":
            self.store.identity.require(actor, "write")
            return 200, self.media.process_pending(actor, int(body().get("limit", 100)))
        if path == "/api/platform/media/scorecard" and method == "GET":
            return 200, self.media.scorecard(actor)
        if path == "/api/platform/media/ai/status" and method == "GET":
            return 200, self.groq.status()

        if len(parts) == 5 and parts[:4] == ["api", "platform", "media", "assets"]:
            asset_id = parts[4]
            if method == "GET":
                return 200, self.media.asset(actor, asset_id)

        if len(parts) == 6 and parts[:4] == ["api", "platform", "media", "assets"]:
            asset_id, action = parts[4], parts[5]
            if action == "checks" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.media.record_check(actor, asset_id, body())
            if action == "derivatives" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.media.add_derivative(actor, asset_id, body())
            if action == "links" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.media.link(actor, asset_id, body())
            if action == "review" and method == "POST":
                self.store.identity.require(actor, "write")
                return 200, self.media.review(actor, asset_id, body())
            if action == "revisions" and method == "GET":
                return 200, {"revisions": self.media.revisions(actor, asset_id)}
            if action == "groq" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.groq.analyze(actor, asset_id, body())
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith("/api/platform/media"):
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._media_response(path, method, actor, environ)
            return self._response(environ, start_response, status, payload, rid)
        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except ValueError as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except RuntimeError as exc:
            return self._error(environ, start_response, rid, HTTPError(503, "ai_unavailable", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except Exception:
            return self._error(environ, start_response, rid, HTTPError(500, "internal_error", "internal server error"))


application = Phase19Application()
