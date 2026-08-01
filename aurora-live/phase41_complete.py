from __future__ import annotations

import base64
import os
import uuid

from phase36_operations import regional_baseline
from phase40_complete import Phase40Application
from phase41_capabilities import reconciled_gaps, reconciled_manifest
from phase41_media import MediaStore, MediaVerifier
from phase41_replay import ReplayStore
from platform_wsgi import HTTPError, RID_RE


class Phase41Application(Phase40Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = (
            os.getenv("AURORA_REPLAY_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ":memory:"
        )
        self.replay_store = ReplayStore(target)
        self.media_store = MediaStore(target)
        self.media = MediaVerifier(self.media_store)

    def _product_manifest(self):
        return reconciled_manifest(
            webcam_coverage=self.webcams.coverage(),
            imagery_baseline=regional_baseline(self.imagery, self.operational_store),
            unified_health=self.unified_health.snapshot(),
            transport_health=self.transport_store.health(),
            infrastructure_health=self.infrastructure_store.health(),
            markets_health=self.markets_store.health(),
            replay_coverage=self.replay_store.coverage(),
            media_coverage=self.media.coverage(),
        )

    def _sync_replay_snapshot(self) -> dict[str, int]:
        counts = {
            "transport": self.replay_store.merge_external(
                self.transport_store.observations(limit=100), domain="transport"
            ),
            "infrastructure": self.replay_store.merge_external(
                self.infrastructure_store.observations(limit=100), domain="infrastructure"
            ),
            "markets": self.replay_store.merge_external(
                self.markets_store.observations(limit=100), domain="markets"
            ),
            "webcams": self.replay_store.merge_external(self.webcams.list(limit=100), domain="webcams"),
            "media": self.replay_store.merge_external(self.media_store.list(limit=100), domain="media"),
        }
        return counts

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase41.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 41,
                        "capability": "unified replay and media lineage verification",
                        "domains": ["events", "transport", "infrastructure", "markets", "webcams", "media"],
                        "authenticity_never_claimed_from_hash_alone": True,
                        "registration_is_not_live_evidence": True,
                    },
                    rid,
                )

            if path in {"/.well-known/aurora-product.json", "/api/public/product/capabilities"} and method == "GET":
                payload = self._product_manifest()
                if path.startswith("/.well-known/"):
                    return self._json_document(environ, start_response, payload, rid)
                return self._response(environ, start_response, 200, payload, rid)

            if path == "/api/public/product/gaps" and method == "GET":
                query = self._query(environ)
                return self._response(
                    environ,
                    start_response,
                    200,
                    reconciled_gaps(
                        webcam_coverage=self.webcams.coverage(),
                        imagery_baseline=regional_baseline(self.imagery, self.operational_store),
                        unified_health=self.unified_health.snapshot(),
                        transport_health=self.transport_store.health(),
                        infrastructure_health=self.infrastructure_store.health(),
                        markets_health=self.markets_store.health(),
                        replay_coverage=self.replay_store.coverage(),
                        media_coverage=self.media.coverage(),
                        priority=self._value(query, "priority", ""),
                    ),
                    rid,
                )

            if path == "/api/public/replay" and method == "GET":
                query = self._query(environ)
                domains = [part for part in str(self._value(query, "domains", "")).split(",") if part]
                rows = self.replay_store.query(
                    domains=domains or None,
                    start=self._value(query, "from", ""),
                    end=self._value(query, "to", ""),
                    limit=int(self._value(query, "limit", "250")),
                )
                return self._response(
                    environ,
                    start_response,
                    200,
                    {"records": rows, "total": len(rows), "coverage": self.replay_store.coverage()},
                    rid,
                )

            if path == "/api/public/replay/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.replay_store.coverage(), rid)

            if path == "/api/public/media" and method == "GET":
                query = self._query(environ)
                rows = self.media_store.list(
                    limit=int(self._value(query, "limit", "250")),
                    state=self._value(query, "state", ""),
                )
                return self._response(
                    environ,
                    start_response,
                    200,
                    {"assets": rows, "total": len(rows), "coverage": self.media.coverage()},
                    rid,
                )

            if path == "/api/public/media/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.media.coverage(), rid)

            if path == "/api/platform/replay/sync" and method == "POST":
                self._user(environ)
                counts = self._sync_replay_snapshot()
                return self._response(
                    environ,
                    start_response,
                    200,
                    {"synced": counts, "coverage": self.replay_store.coverage()},
                    rid,
                )

            if path == "/api/platform/media/verify" and method == "POST":
                self._user(environ)
                body = self.platform.body(environ)
                raw = body.get("content_base64")
                if not raw:
                    raise ValueError("content_base64 is required")
                data = base64.b64decode(str(raw))
                result = self.media.verify_bytes(
                    data,
                    source_url=str(body.get("source_url") or "upload://local"),
                    content_type=str(body.get("content_type") or "application/octet-stream"),
                    license_note=str(body.get("license_note") or "unspecified"),
                    parent_event_id=str(body.get("parent_event_id") or ""),
                    captured_at=str(body.get("captured_at") or ""),
                )
                self.replay_store.merge_external([result], domain="media")
                return self._response(environ, start_response, 201, result, rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                # reuse phase40 payload and append phase41 fields
                payload_holder = {}

                def capture(environ2, start_response2):
                    def starter(status, headers, exc_info=None):
                        payload_holder["status"] = status
                        payload_holder["headers"] = headers
                        return lambda b: None

                    # Call parent path logic by temporarily using super for non-matching routes
                    # Simpler: build directly
                    return []

                transport_health = self.transport_store.health()
                infrastructure_health = self.infrastructure_store.health()
                markets_health = self.markets_store.health()
                payload = {
                    "phase": 41,
                    "product": self._product_manifest(),
                    "transport_coverage": self.transport_store.coverage(),
                    "transport_health": transport_health,
                    "infrastructure_coverage": self.infrastructure_store.coverage(),
                    "infrastructure_health": infrastructure_health,
                    "markets_coverage": self.markets_store.coverage(),
                    "markets_health": markets_health,
                    "replay_coverage": self.replay_store.coverage(),
                    "recent_replay": self.replay_store.query(limit=50),
                    "media_coverage": self.media.coverage(),
                    "recent_media": self.media_store.list(limit=50),
                    "webcam_coverage": self.webcams.coverage(),
                    "webcam_matrix": self.webcams.matrix(),
                    "webcams_fully_qualified": self.webcams.coverage()["fully_qualified"],
                }
                return self._response(environ, start_response, 200, payload, rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(
                environ,
                start_response,
                rid,
                HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"),
            )
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase41Application()
