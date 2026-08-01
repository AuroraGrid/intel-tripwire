from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy

from phase32_product_spec import STATUSES
from phase42_complete import Phase42Application
from phase43_public import AbuseLimiter, NotificationStore, cache_headers_for, public_config, public_mode_enabled
from platform_wsgi import HTTPError, RID_RE


class Phase43Application(Phase42Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.abuse = AbuseLimiter()
        self.notifications = NotificationStore()

    def _product_manifest(self):
        manifest = super()._product_manifest()
        items = list(manifest.get("capabilities") or [])
        by_key = {item["key"]: item for item in items}
        config = public_config()
        for key, reason in (
            (
                "pwa",
                "PWA shell (manifest + service worker) is present; installable public client is available.",
            ),
            (
                "free-public",
                "Public no-paywall mode and abuse controls are implemented; production host evidence remains operator-run.",
            ),
        ):
            if key not in by_key:
                continue
            item = by_key[key]
            item["declared_status"] = item.get("declared_status", item.get("status", "NOT_VERIFIED"))
            item["status"] = "PARTIAL"
            item["status_source"] = "runtime-evidence"
            item["runtime_evidence"] = [
                f"public_mode={config['public_mode']}",
                f"pwa_shell={config['pwa_shell']}",
                f"abuse_controls={config['abuse_controls']}",
            ]
            item["qualification_reason"] = reason
            if item["status"] not in STATUSES:
                item["status"] = "PARTIAL"
        counts = {status: sum(item["status"] == status for item in items) for status in sorted(STATUSES)}
        manifest = deepcopy(manifest)
        manifest["phase"] = max(int(manifest.get("phase") or 0), 43)
        manifest["counts"] = counts
        manifest["capabilities"] = items
        manifest["public"] = config
        return manifest

    def _client_key(self, environ) -> str:
        forwarded = str(environ.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        remote = forwarded or str(environ.get("REMOTE_ADDR") or "unknown")
        path = str(environ.get("PATH_INFO") or "")
        return f"{remote}:{path}"

    def _wrap_start_response(self, start_response, extra_headers: list[tuple[str, str]]):
        def wrapped(status, headers, exc_info=None):
            merged = list(headers) + extra_headers
            return start_response(status, merged, exc_info)

        return wrapped

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        extra = cache_headers_for(path) + [("X-Request-ID", rid)]
        if path.startswith("/api/public") or path.startswith("/.well-known/") or path in {"/", "/gop", "/incident-room", "/source-health"}:
            decision = self.abuse.check(self._client_key(environ))
            extra.extend(decision.headers())
            if not decision.allowed:
                body = json.dumps(
                    {
                        "error": {
                            "code": "rate_limited",
                            "message": "public rate limit exceeded",
                            "retry_after_seconds": decision.reset_seconds,
                        }
                    }
                ).encode("utf-8")
                headers = [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                    ("Retry-After", str(decision.reset_seconds)),
                    *extra,
                ]
                start_response("429 Too Many Requests", headers)
                return [body]

        try:
            if path == "/.well-known/aurora-phase43.json" and method == "GET":
                return self._json_document(
                    environ,
                    self._wrap_start_response(start_response, extra),
                    {
                        "phase": 43,
                        "capability": "PWA shell, public no-paywall deployment controls, caching, abuse controls, notification scaffolding",
                        "public": public_config(),
                        "service_worker": "/static/sw.js",
                        "manifest": "/static/manifest.webmanifest",
                    },
                    rid,
                )

            if path == "/api/public/deployment" and method == "GET":
                return self._response(
                    environ,
                    self._wrap_start_response(start_response, extra),
                    200,
                    public_config(),
                    rid,
                )

            if path == "/api/public/notifications/config" and method == "GET":
                return self._response(
                    environ,
                    self._wrap_start_response(start_response, extra),
                    200,
                    {
                        "vapid_public_key_configured": bool(str(os.getenv("AURORA_VAPID_PUBLIC_KEY") or "").strip()),
                        "subscriptions": self.notifications.count(),
                        "delivery_enabled": bool(str(os.getenv("AURORA_VAPID_PUBLIC_KEY") or "").strip()),
                    },
                    rid,
                )

            if path == "/api/public/notifications/subscribe" and method == "POST":
                if not public_mode_enabled():
                    raise HTTPError(403, "forbidden", "public mode disabled")
                body = self.platform.body(environ)
                result = self.notifications.add(body if isinstance(body, dict) else {})
                return self._response(
                    environ,
                    self._wrap_start_response(start_response, extra),
                    201,
                    result,
                    rid,
                )

            if method == "GET" and path == "/static/manifest.webmanifest":
                return self._static_file(environ, self._wrap_start_response(start_response, extra), "manifest.webmanifest", rid)
            if method == "GET" and path == "/static/sw.js":
                return self._static_file(environ, self._wrap_start_response(start_response, extra), "sw.js", rid)

        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))

        return super().__call__(environ, self._wrap_start_response(start_response, extra))


application = Phase43Application()
