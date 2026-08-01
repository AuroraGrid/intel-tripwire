from __future__ import annotations

import os
import uuid

from phase43_complete import Phase43Application
from phase44_benchmark import build_benchmark_report
from phase44_operations import OperationsHistoryStore, evaluate_redundancy
from platform_wsgi import HTTPError, RID_RE


class Phase44Application(Phase43Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = (
            os.getenv("AURORA_OPS_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ":memory:"
        )
        self.ops_store = OperationsHistoryStore(target)

    def _product_manifest(self):
        manifest = super()._product_manifest()
        manifest["phase"] = max(int(manifest.get("phase") or 0), 44)
        return manifest

    def _sample_ops(self) -> dict:
        product = self._product_manifest()
        transport = self.transport_store.health()
        infrastructure = self.infrastructure_store.health()
        markets = self.markets_store.health()
        primary_ok = True
        try:
            primary_ok = bool(product.get("phase"))
        except Exception:
            primary_ok = False
        secondary = os.getenv("AURORA_SECONDARY_HEARTBEAT_OK")
        secondary_ok = None if secondary is None or secondary == "" else str(secondary).lower() in {"1", "true", "yes"}
        redundancy = evaluate_redundancy(primary_ok=primary_ok, secondary_ok=secondary_ok)
        detail = {
            "product_phase": product.get("phase"),
            "transport_operational_layers": transport.get("operational_layers") if isinstance(transport, dict) else None,
            "infrastructure_operational_layers": infrastructure.get("operational_layers"),
            "markets_operational_layers": markets.get("operational_layers"),
            "redundancy": redundancy,
        }
        status = "UP" if primary_ok else "DOWN"
        return self.ops_store.record(
            status=status,
            uptime_ok=primary_ok,
            redundancy_ok=bool(redundancy.get("ok")),
            detail=detail,
        )

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase44.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 44,
                        "capability": "operational proof history and reproducible competitive benchmark harness",
                        "ten_of_ten_auto_promotion": False,
                        "long_run_proof_is_operator_run": True,
                    },
                    rid,
                )

            if path == "/api/public/ops/summary" and method == "GET":
                return self._response(environ, start_response, 200, self.ops_store.summary(), rid)

            if path == "/api/public/ops/history" and method == "GET":
                query = self._query(environ)
                rows = self.ops_store.history(limit=int(self._value(query, "limit", "100")))
                return self._response(environ, start_response, 200, {"samples": rows, "total": len(rows)}, rid)

            if path == "/api/public/ops/sample" and method == "POST":
                # Unauthenticated sample writes are disabled; use authenticated platform route.
                raise HTTPError(403, "forbidden", "authenticate via POST /api/platform/ops/sample")

            if path == "/api/platform/ops/sample" and method == "POST":
                self._user(environ)
                sample = self._sample_ops()
                return self._response(environ, start_response, 201, sample, rid)

            if path == "/api/public/benchmark" and method == "GET":
                report = build_benchmark_report(
                    product=self._product_manifest(),
                    transport_health=self.transport_store.health(),
                    infrastructure_health=self.infrastructure_store.health(),
                    markets_health=self.markets_store.health(),
                    ops_summary=self.ops_store.summary(),
                )
                return self._response(environ, start_response, 200, report, rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                # extend parent payload with ops/benchmark
                transport_health = self.transport_store.health()
                infrastructure_health = self.infrastructure_store.health()
                markets_health = self.markets_store.health()
                product = self._product_manifest()
                payload = {
                    "phase": 44,
                    "product": product,
                    "transport_health": transport_health,
                    "infrastructure_health": infrastructure_health,
                    "markets_health": markets_health,
                    "replay_coverage": self.replay_store.coverage(),
                    "media_coverage": self.media.coverage(),
                    "webcam_coverage": self.webcams.coverage(),
                    "ops_summary": self.ops_store.summary(),
                    "benchmark": build_benchmark_report(
                        product=product,
                        transport_health=transport_health,
                        infrastructure_health=infrastructure_health,
                        markets_health=markets_health,
                        ops_summary=self.ops_store.summary(),
                    ),
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


application = Phase44Application()
