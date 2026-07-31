from __future__ import annotations

import json
import re
import urllib.parse

from phase40_markets import (
    AlphaVantageEquitiesAdapter,
    BaseAdapter,
    CoinbaseCryptoAdapter,
    ECBFXAdapter,
    EIAEnergyAdapter,
    KalshiPredictionMarketsAdapter,
    MarketObservation,
    MarketStore,
    _event_time,
    _float,
    _json,
    _now,
)
from phase40_repairs import (
    ProductionMarketCoordinator,
    ResilientWorldBankCommoditiesAdapter,
    ResilientWorldBankIndicatorsAdapter,
    _normalized,
)


class RevisionAwareMarketStore(MarketStore):
    """Refresh an existing market identity when a provider publishes a revision."""

    def record(self, observation: MarketObservation) -> int:
        if observation.domain not in self._domains():
            raise ValueError("invalid market domain")
        if not observation.provider or not observation.instrument or not observation.external_id:
            raise ValueError("provider, instrument and external_id are required")

        payload_json = json.dumps(observation.payload, sort_keys=True, separators=(",", ":"), default=str)
        provenance_json = json.dumps(observation.provenance, sort_keys=True, separators=(",", ":"), default=str)
        identity = (observation.domain, observation.provider, observation.external_id)
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute(
                f"SELECT observation_id FROM market_observations WHERE domain={self._p} AND provider={self._p} AND external_id={self._p} LIMIT 1",
                identity,
            )
            existing = cursor.fetchone()
            if existing is not None:
                identifier = self._id(existing, "observation_id")
                cursor.execute(
                    f"UPDATE market_observations SET instrument={self._p}, observed_at={self._p}, event_time={self._p}, value={self._p}, unit={self._p}, status={self._p}, payload_json={self._p}, provenance_json={self._p} WHERE observation_id={self._p}",
                    (
                        observation.instrument,
                        observation.observed_at,
                        observation.event_time,
                        float(observation.value),
                        observation.unit,
                        observation.status,
                        payload_json,
                        provenance_json,
                        identifier,
                    ),
                )
                self._connection.commit()
                return identifier
        return super().record(observation)

    @staticmethod
    def _domains() -> tuple[str, ...]:
        from phase40_markets import DOMAINS

        return DOMAINS


class ResilientAlphaVantageEquitiesAdapter(AlphaVantageEquitiesAdapter):
    """Preserve valid scoped quotes when another configured symbol fails."""

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        if not self.configured():
            raise RuntimeError("Alpha Vantage equities provider is not configured")

        observed_at = _now()
        successes: list[tuple[str, dict, float, str]] = []
        failures: list[str] = []
        for symbol in self.symbols():
            url = f"{self.endpoint}?{urllib.parse.urlencode({'function': 'GLOBAL_QUOTE', 'symbol': symbol, 'apikey': self.api_key()})}"
            try:
                payload = _json(url, timeout=timeout)
            except Exception:
                failures.append(f"{symbol}:request_failed")
                continue
            quote = payload.get("Global Quote") if isinstance(payload, dict) else None
            if not isinstance(quote, dict):
                failures.append(f"{symbol}:quote_missing")
                continue
            price = _float(quote.get("05. price") or quote.get("price"))
            if price is None:
                failures.append(f"{symbol}:quote_invalid")
                continue
            event = _event_time(quote.get("07. latest trading day"), observed_at)
            successes.append((symbol, quote, price, event))

        if not successes:
            raise RuntimeError(f"Alpha Vantage returned no valid quotes ({len(failures)} scoped symbols failed)")

        partial = bool(failures)
        return [
            MarketObservation(
                self.domain,
                self.name,
                symbol,
                f"{symbol}:{event}",
                observed_at,
                event,
                price,
                "provider quote currency",
                "quote",
                quote,
                {
                    "provider": "Alpha Vantage",
                    "source": self.endpoint,
                    "credential_env": self.key_env,
                    "scope_env": self.symbols_env,
                    "credential_committed": False,
                    "request_url_persisted": False,
                    "partial_success": partial,
                    "failed_symbols": failures,
                    "license_note": self.license_note,
                },
            )
            for symbol, quote, price, event in successes
        ]


class NativeCodeWorldBankCommoditiesAdapter(ResilientWorldBankCommoditiesAdapter):
    """Use World Bank native series codes as identity; never substitute shared units."""

    @classmethod
    def _instrument(cls, labels: list[str], index: int) -> str:
        unit_markers = (
            "$",
            "usd",
            " per ",
            "/bbl",
            "/mt",
            "/kg",
            "/toz",
            "mmbtu",
            "cents",
            "2010=100",
        )
        candidates: list[tuple[str, str]] = []
        for original in labels:
            normalized = _normalized(original)
            if not normalized:
                continue
            lower = str(original).lower()
            if any(marker in lower for marker in unit_markers):
                continue
            candidates.append((str(original).strip(), normalized))

        alias_codes = {code for values in cls.aliases.values() for code in values}
        exact_native = [normalized for _original, normalized in candidates if normalized in alias_codes]
        if exact_native:
            return exact_native[-1][:160]

        underscored = [
            normalized
            for original, normalized in candidates
            if "_" in original and re.fullmatch(r"[A-Z0-9_]{3,}", normalized)
        ]
        if underscored:
            return underscored[-1][:160]

        code_like = [
            normalized
            for _original, normalized in candidates
            if "_" in normalized and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", normalized)
        ]
        if code_like:
            return code_like[-1][:160]

        descriptive = [normalized for _original, normalized in candidates]
        return (max(descriptive, key=len) if descriptive else f"SERIES_{index}")[:160]


class VerifiedMarketCoordinator(ProductionMarketCoordinator):
    def __init__(self, store) -> None:
        self.store = store
        self.adapters: list[BaseAdapter] = [
            ResilientAlphaVantageEquitiesAdapter(),
            EIAEnergyAdapter(),
            NativeCodeWorldBankCommoditiesAdapter(),
            ECBFXAdapter(),
            CoinbaseCryptoAdapter(),
            ResilientWorldBankIndicatorsAdapter(),
            KalshiPredictionMarketsAdapter(),
        ]
        self._ensure_registered()
