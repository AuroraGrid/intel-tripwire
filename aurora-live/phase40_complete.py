from __future__ import annotations

import os
import uuid

from phase36_operations import regional_baseline
from phase39_complete import Phase39Application
from phase40_capabilities import reconciled_gaps, reconciled_manifest
from phase40_markets import MarketStore
from phase40_repairs import ProductionMarketCoordinator
from platform_wsgi import HTTPError, RID_RE


class Phase40Application(Phase39Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target = (
            os.getenv("AURORA_MARKETS_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ":memory:"
        )
        self.market_store = MarketStore(target)
        self.markets = ProductionMarketCoordinator(self.market_store)

    def _product_manifest(self):
        return reconciled_manifest(
            webcam_coverage=self.webcams.coverage(),
            imagery_baseline=regional_baseline(self.imagery, self.operational_store),
            unified_health=self.unified_health.snapshot(),
            transport_health=self.transport_store.health(),
            infrastructure_health=self.infrastructure_store.health(),
            market_health=self.market_store.health(),
        )

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO") or "")
        method = str(environ.get("REQUEST_METHOD") or "GET").upper()
        supplied = str(environ.get("HTTP_X_REQUEST_ID") or "")
        rid = supplied if RID_RE.fullmatch(supplied) else uuid.uuid4().hex

        try:
            if path == "/.well-known/aurora-phase40.json" and method == "GET":
                return self._json_document(
                    environ,
                    start_response,
                    {
                        "phase": 40,
                        "capability": "evidence-gated global markets, economics and prediction markets",
                        "domains": [
                            "equities",
                            "energy",
                            "commodities",
                            "fx",
                            "crypto",
                            "economic_indicators",
                            "prediction_markets",
                        ],
                        "registration_is_not_live_evidence": True,
                        "configured_is_not_operational": True,
                        "qualification_requires_recent_retrieval_and_durable_numeric_observations": True,
                        "publication_or_market_age_is_reported_separately": True,
                        "market_data_is_not_investment_advice": True,
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
                        market_health=self.market_store.health(),
                        priority=self._value(query, "priority", ""),
                    ),
                    rid,
                )

            if path == "/api/public/markets/coverage" and method == "GET":
                return self._response(environ, start_response, 200, self.market_store.coverage(), rid)

            if path == "/api/public/markets/health" and method == "GET":
                query = self._query(environ)
                maximum_age = int(self._value(query, "max_age_seconds", "0") or 0)
                return self._response(environ, start_response, 200, self.market_store.health(maximum_age or None), rid)

            if path == "/api/public/markets/providers" and method == "GET":
                query = self._query(environ)
                domain = self._value(query, "domain", "")
                rows = self.market_store.providers(domain)
                return self._response(environ, start_response, 200, {"providers": rows, "total": len(rows)}, rid)

            if path == "/api/public/markets/runs" and method == "GET":
                query = self._query(environ)
                rows = self.market_store.runs(
                    domain=self._value(query, "domain", ""),
                    provider=self._value(query, "provider", ""),
                    limit=int(self._value(query, "limit", "100")),
                )
                return self._response(environ, start_response, 200, {"runs": rows, "total": len(rows)}, rid)

            if path == "/api/public/markets/observations" and method == "GET":
                query = self._query(environ)
                rows = self.market_store.observations(
                    domain=self._value(query, "domain", ""),
                    provider=self._value(query, "provider", ""),
                    instrument=self._value(query, "instrument", ""),
                    limit=int(self._value(query, "limit", "250")),
                )
                return self._response(environ, start_response, 200, {"observations": rows, "total": len(rows)}, rid)

            if path == "/api/public/markets/configuration" and method == "GET":
                return self._response(environ, start_response, 200, self.markets.configuration(), rid)

            if path.startswith("/api/platform/markets/run/") and method == "POST":
                self._user(environ)
                provider = path[len("/api/platform/markets/run/") :].strip("/")
                if not provider:
                    raise HTTPError(404, "not_found", "route not found")
                body = self.platform.body(environ)
                result = self.markets.run(provider, timeout=int(body.get("timeout") or 30))
                status = 200 if result.successful else 409 if not result.configured else 502
                return self._response(environ, start_response, status, result.value(), rid)

            if path == "/api/public/global-operating-picture" and method == "GET":
                transport_health = self.transport_store.health()
                infrastructure_health = self.infrastructure_store.health()
                market_health = self.market_store.health()
                payload = {
                    "phase": 40,
                    "product": self._product_manifest(),
                    "transport_coverage": self.transport_store.coverage(),
                    "transport_health": transport_health,
                    "transport_configuration": self.transport_providers.configuration(),
                    "transport_providers": self.transport_store.providers(),
                    "recent_transport_runs": self.transport_store.provider_runs(limit=20),
                    "recent_transport_observations": self.transport_store.observations(limit=100),
                    "transport_workers": transport_health["workers"],
                    "infrastructure_coverage": self.infrastructure_store.coverage(),
                    "infrastructure_health": infrastructure_health,
                    "infrastructure_configuration": self.infrastructure.configuration(),
                    "infrastructure_providers": self.infrastructure_store.providers(),
                    "recent_infrastructure_runs": self.infrastructure_store.runs(limit=40),
                    "recent_infrastructure_observations": self.infrastructure_store.observations(limit=200),
                    "market_coverage": self.market_store.coverage(),
                    "market_health": market_health,
                    "market_configuration": self.markets.configuration(),
                    "market_providers": self.market_store.providers(),
                    "recent_market_runs": self.market_store.runs(limit=40),
                    "recent_market_observations": self.market_store.observations(limit=300),
                    "webcam_coverage": self.webcams.coverage(),
                    "webcam_matrix": self.webcams.matrix(),
                    "webcams_fully_qualified": self.webcams.coverage()["fully_qualified"],
                }
                return self._response(environ, start_response, 200, payload, rid)

        except PermissionError as exc:
            return self._error(environ, start_response, rid, HTTPError(403, "forbidden", str(exc)))
        except KeyError as exc:
            return self._error(environ, start_response, rid, HTTPError(404, "not_found", str(exc).strip("'") or "resource not found"))
        except (TypeError, ValueError) as exc:
            return self._error(environ, start_response, rid, HTTPError(400, "bad_request", str(exc)))
        except HTTPError as exc:
            return self._error(environ, start_response, rid, exc)

        return super().__call__(environ, start_response)


application = Phase40Application()
