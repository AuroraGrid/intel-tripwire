from __future__ import annotations

import re

from phase40_markets import (
    AlphaVantageEquitiesAdapter,
    BaseAdapter,
    CoinbaseCryptoAdapter,
    ECBFXAdapter,
    EIAEnergyAdapter,
    KalshiPredictionMarketsAdapter,
)
from phase40_repairs import (
    ProductionMarketCoordinator,
    ResilientWorldBankCommoditiesAdapter,
    ResilientWorldBankIndicatorsAdapter,
    _normalized,
)


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
            AlphaVantageEquitiesAdapter(),
            EIAEnergyAdapter(),
            NativeCodeWorldBankCommoditiesAdapter(),
            ECBFXAdapter(),
            CoinbaseCryptoAdapter(),
            ResilientWorldBankIndicatorsAdapter(),
            KalshiPredictionMarketsAdapter(),
        ]
        self._ensure_registered()
