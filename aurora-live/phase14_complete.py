from __future__ import annotations

import uuid

from phase13_complete import Phase13Application
from phase14_integrity import EvidenceIntegrity
from platform_wsgi import HTTPError, RID_RE


class Phase14Application(Phase13Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.integrity = EvidenceIntegrity(self.store)

    def _integrity_response(self, path, method, actor, environ):
        query = self._query(environ)
        value = lambda name, default="": self._value(query, name, default)
        parts = [part for part in path.split("/") if part]
        body = lambda: self.platform.body(environ)

        if path == "/api/platform/sources":
            if method == "GET": return 200, {"sources": self.integrity.sources(actor)}
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.integrity.register_source(actor, body())
        if path == "/api/platform/claims":
            if method == "GET": return 200, {"claims": self.integrity.claims(actor, value("status"), value("claim_type"), int(value("limit", "100")))}
            if method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.integrity.create_claim(actor, body())
        if path == "/api/platform/integrity/scorecard" and method == "GET":
            return 200, self.integrity.scorecard(actor)
        if path == "/api/platform/contradictions" and method == "GET":
            return 200, {"contradictions": self.integrity.contradictions(actor, value("state", "OPEN"), int(value("limit", "100")))}
        if len(parts) == 4 and parts[:3] == ["api", "platform", "claims"]:
            claim_id = parts[3]
            if method == "GET": return 200, self.integrity.claim(actor, claim_id)
        if len(parts) == 5 and parts[:3] == ["api", "platform", "claims"]:
            claim_id, action = parts[3], parts[4]
            if action == "evidence" and method == "POST":
                self.store.identity.require(actor, "write")
                return 201, self.integrity.add_evidence(actor, claim_id, body())
            if action == "assess" and method == "POST":
                self.store.identity.require(actor, "write")
                payload = body()
                return 200, self.integrity.assessment(actor, claim_id, True, payload.get("reason", "Automated zero-trust reassessment"))
            if action == "lineage" and method == "GET":
                return 200, self.integrity.lineage(actor, claim_id)
        if len(parts) == 5 and parts[:3] == ["api", "platform", "contradictions"] and parts[4] == "resolve" and method == "POST":
            self.store.identity.require(actor, "write")
            payload = body()
            return 200, self.integrity.resolve_contradiction(actor, parts[3], payload.get("reason", "Reviewed by analyst"))
        raise HTTPError(404, "not_found", "route not found")

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        managed = path.startswith("/api/platform/sources") or path.startswith("/api/platform/claims") or path.startswith("/api/platform/contradictions") or path.startswith("/api/platform/integrity")
        if not managed:
            return super().__call__(environ, start_response)
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex
        try:
            actor = self._user(environ)
            status, payload = self._integrity_response(path, method, actor, environ)
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


application = Phase14Application()
