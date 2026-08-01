from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYERS = (
    "global_stocks",
    "energy",
    "commodities",
    "currencies",
    "crypto",
    "economic_indicators",
    "prediction_markets",
)
PROVIDER_STATES = {"ONLINE", "DEGRADED", "OFFLINE", "NOT_CONFIGURED"}
USER_AGENT = "AURORA-LIVE/40 (+https://github.com/hr185882-creator/intel-tripwire)"

# capability key in product registry -> layer id
LAYER_CAPABILITY = {
    "global_stocks": "global-stocks",
    "energy": "energy",
    "commodities": "commodities",
    "currencies": "currencies",
    "crypto": "crypto",
    "economic_indicators": "economic-indicators",
    "prediction_markets": "prediction-markets",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


def _request(url: str, *, timeout: int = 30, accept: str = "application/json") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _json(url: str, *, timeout: int = 30) -> Any:
    return json.loads(_request(url, timeout=timeout).decode("utf-8", "replace"))


@dataclass(frozen=True)
class MarketObservation:
    layer: str
    provider: str
    external_id: str
    observed_at: str
    event_time: str
    symbol: str
    title: str
    value: float | None
    currency: str
    unit: str
    source_url: str
    payload: dict[str, Any]
    provenance: dict[str, Any]

    def value_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderRun:
    provider: str
    layer: str
    configured: bool
    successful: bool
    observations: int
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0

    def value(self) -> dict[str, Any]:
        return asdict(self)


class MarketStore:
    """Durable SQLite/PostgreSQL store for market observations, health, and runs."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL market storage") from exc
            self._connection = psycopg.connect(self.target, row_factory=dict_row)
            self._p = "%s"
        else:
            if self.target != ":memory:":
                Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.target, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._p = "?"
        self._initialize()

    def _initialize(self) -> None:
        observation_pk = (
            "observation_id BIGSERIAL PRIMARY KEY" if self.postgres else "observation_id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        run_pk = "run_id BIGSERIAL PRIMARY KEY" if self.postgres else "run_id INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            f"""CREATE TABLE IF NOT EXISTS market_observations (
                {observation_pk}, layer TEXT NOT NULL, provider TEXT NOT NULL,
                external_id TEXT NOT NULL, observed_at TEXT NOT NULL, event_time TEXT NOT NULL,
                symbol TEXT NOT NULL, title TEXT NOT NULL, value DOUBLE PRECISION,
                currency TEXT NOT NULL, unit TEXT NOT NULL, source_url TEXT NOT NULL,
                payload_json TEXT NOT NULL, provenance_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS market_provider_health (
                provider TEXT PRIMARY KEY, layer TEXT NOT NULL, state TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL, last_success_at TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL, freshness_seconds INTEGER NOT NULL,
                last_error TEXT NOT NULL, completeness_note TEXT NOT NULL,
                license_note TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS market_provider_runs (
                {run_pk}, provider TEXT NOT NULL, layer TEXT NOT NULL,
                configured INTEGER NOT NULL, successful INTEGER NOT NULL,
                observations INTEGER NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                error TEXT NOT NULL, metadata_json TEXT NOT NULL)""",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_market_observation_identity ON market_observations(layer,provider,external_id)",
            "CREATE INDEX IF NOT EXISTS idx_market_layer_time ON market_observations(layer,observation_id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_market_runs_provider ON market_provider_runs(provider,run_id DESC)",
        ]
        with self._lock:
            cursor = self._connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            self._connection.commit()

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    @staticmethod
    def _id(row: Any, key: str) -> int:
        if isinstance(row, dict):
            return int(row[key])
        try:
            return int(row[key])
        except (TypeError, KeyError, IndexError):
            return int(row[0])

    def record(self, observation: MarketObservation) -> int:
        if observation.layer not in LAYERS:
            raise ValueError("invalid market layer")
        if not observation.provider or not observation.external_id:
            raise ValueError("provider and external_id are required")
        values = (
            observation.layer,
            observation.provider,
            observation.external_id,
            observation.observed_at,
            observation.event_time,
            observation.symbol,
            observation.title,
            observation.value,
            observation.currency,
            observation.unit,
            observation.source_url,
            json.dumps(observation.payload, sort_keys=True, separators=(",", ":")),
            json.dumps(observation.provenance, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute(
                f"SELECT observation_id FROM market_observations WHERE layer={self._p} AND provider={self._p} AND external_id={self._p} LIMIT 1",
                (observation.layer, observation.provider, observation.external_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return self._id(existing, "observation_id")
            if self.postgres:
                cursor.execute(
                    "INSERT INTO market_observations(layer,provider,external_id,observed_at,event_time,symbol,title,value,currency,unit,source_url,payload_json,provenance_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING observation_id",
                    values,
                )
                identifier = self._id(cursor.fetchone(), "observation_id")
            else:
                cursor.execute(
                    "INSERT INTO market_observations(layer,provider,external_id,observed_at,event_time,symbol,title,value,currency,unit,source_url,payload_json,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                identifier = int(cursor.lastrowid)
            self._connection.commit()
            return identifier

    def observations(self, layer: str = "", provider: str = "", limit: int = 250) -> list[dict[str, Any]]:
        if layer and layer not in LAYERS:
            raise ValueError("invalid market layer")
        clauses: list[str] = []
        values: list[Any] = []
        if layer:
            clauses.append(f"layer={self._p}")
            values.append(layer)
        if provider:
            clauses.append(f"provider={self._p}")
            values.append(provider)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 2000)))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM market_observations{where} ORDER BY observation_id DESC LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            for source, target in (("payload_json", "payload"), ("provenance_json", "provenance")):
                try:
                    item[target] = json.loads(item.pop(source, "{}"))
                except json.JSONDecodeError:
                    item[target] = {}
            output.append(item)
        return output

    def upsert_provider(self, value: dict[str, Any]) -> None:
        layer = str(value.get("layer") or "")
        state = str(value.get("state") or "")
        if layer not in LAYERS:
            raise ValueError("invalid market layer")
        if state not in PROVIDER_STATES:
            raise ValueError("invalid provider state")
        columns = (
            "provider",
            "layer",
            "state",
            "last_attempt_at",
            "last_success_at",
            "consecutive_failures",
            "freshness_seconds",
            "last_error",
            "completeness_note",
            "license_note",
            "updated_at",
        )
        payload = {**value, "updated_at": value.get("updated_at") or _now()}
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        sql = f"INSERT INTO market_provider_health({','.join(columns)}) VALUES ({','.join([self._p] * len(columns))}) ON CONFLICT(provider) DO UPDATE SET {updates}"
        with self._lock:
            self._connection.execute(sql, tuple(payload.get(column, "") for column in columns))
            self._connection.commit()

    def providers(self, layer: str = "") -> list[dict[str, Any]]:
        if layer and layer not in LAYERS:
            raise ValueError("invalid market layer")
        with self._lock:
            if layer:
                rows = self._connection.execute(
                    f"SELECT * FROM market_provider_health WHERE layer={self._p} ORDER BY provider",
                    (layer,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM market_provider_health ORDER BY layer,provider"
                ).fetchall()
        return [self._dict(row) for row in rows]

    def record_run(self, run: ProviderRun, metadata: dict[str, Any] | None = None) -> int:
        values = (
            run.provider,
            run.layer,
            1 if run.configured else 0,
            1 if run.successful else 0,
            max(0, int(run.observations)),
            run.started_at or _now(),
            run.completed_at or _now(),
            max(0, int(run.duration_ms)),
            run.error,
            json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            cursor = self._connection.cursor()
            if self.postgres:
                cursor.execute(
                    "INSERT INTO market_provider_runs(provider,layer,configured,successful,observations,started_at,completed_at,duration_ms,error,metadata_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id",
                    values,
                )
                identifier = self._id(cursor.fetchone(), "run_id")
            else:
                cursor.execute(
                    "INSERT INTO market_provider_runs(provider,layer,configured,successful,observations,started_at,completed_at,duration_ms,error,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                identifier = int(cursor.lastrowid)
            self._connection.commit()
            return identifier

    def runs(self, layer: str = "", provider: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if layer and layer not in LAYERS:
            raise ValueError("invalid market layer")
        clauses: list[str] = []
        values: list[Any] = []
        if layer:
            clauses.append(f"layer={self._p}")
            values.append(layer)
        if provider:
            clauses.append(f"provider={self._p}")
            values.append(provider)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 1000)))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM market_provider_runs{where} ORDER BY run_id DESC LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            item["configured"] = bool(item.get("configured"))
            item["successful"] = bool(item.get("successful"))
            try:
                item["metadata"] = json.loads(item.pop("metadata_json", "{}"))
            except json.JSONDecodeError:
                item["metadata"] = {}
            output.append(item)
        return output

    def health(self, max_age_seconds: int | None = None) -> dict[str, Any]:
        maximum_age = max(60, int(max_age_seconds or os.getenv("AURORA_MARKETS_STALE_SECONDS", "3600")))
        now = datetime.now(timezone.utc)
        providers = self.providers()
        observations = self.observations(limit=2000)
        runs = self.runs(limit=200)
        provider_health: list[dict[str, Any]] = []
        for provider in providers:
            last_success = _parse_time(provider.get("last_success_at"))
            age = int((now - last_success).total_seconds()) if last_success else None
            provider_observations = [row for row in observations if row["provider"] == provider["provider"]]
            latest_run = next((row for row in runs if row["provider"] == provider["provider"]), None)
            fresh = bool(
                provider["state"] == "ONLINE"
                and age is not None
                and age <= maximum_age
                and int(provider.get("freshness_seconds") or 0) <= maximum_age
            )
            operational = bool(
                fresh and provider_observations and latest_run and latest_run["successful"] and latest_run["observations"] > 0
            )
            provider_health.append(
                {
                    **provider,
                    "seconds_since_success": age,
                    "fresh": fresh,
                    "operational": operational,
                    "observations": len(provider_observations),
                    "latest_run": latest_run,
                }
            )
        layer_health = []
        for layer in LAYERS:
            rows = [row for row in provider_health if row["layer"] == layer]
            layer_health.append(
                {
                    "layer": layer,
                    "capability_key": LAYER_CAPABILITY.get(layer, layer),
                    "providers": len(rows),
                    "operational_providers": sum(1 for row in rows if row["operational"]),
                    "operational": any(row["operational"] for row in rows),
                }
            )
        return {
            "max_age_seconds": maximum_age,
            "providers": provider_health,
            "layers": layer_health,
            "operational_layers": sum(1 for row in layer_health if row["operational"]),
            "fully_operational": all(row["operational"] for row in layer_health),
            "generated_at": _now(),
        }

    def coverage(self) -> dict[str, Any]:
        health = self.health()
        return {
            "requirement": "each market layer requires a configured provider, a recent successful run, and fresh durably persisted observations",
            "layers": health["layers"],
            "qualified_layers": health["operational_layers"],
            "fully_qualified": health["fully_operational"],
            "generated_at": health["generated_at"],
        }


class BaseAdapter:
    name = ""
    layer = ""
    endpoint = ""
    license_note = "Provider terms apply."
    completeness_note = "Coverage is provider-dependent and is not assumed global."

    def configured(self) -> bool:
        return True

    def configuration(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "layer": self.layer,
            "configured": self.configured(),
            "endpoint": self.endpoint,
        }


class CoinGeckoCryptoAdapter(BaseAdapter):
    name = "coingecko-markets"
    layer = "crypto"
    endpoint = "https://api.coingecko.com/api/v3/coins/markets"
    license_note = "CoinGecko API terms and attribution apply; free tier rate limits apply."
    completeness_note = "Top market-cap cryptocurrencies only; not a complete crypto universe."

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        query = urllib.parse.urlencode(
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": "50",
                "page": "1",
                "sparkline": "false",
            }
        )
        url = f"{self.endpoint}?{query}"
        data = _json(url, timeout=timeout)
        observed_at = _now()
        output: list[MarketObservation] = []
        if not isinstance(data, list):
            raise RuntimeError("unexpected CoinGecko payload")
        for item in data[:50]:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            identifier = str(item.get("id") or symbol).strip()
            if not identifier:
                continue
            price = item.get("current_price")
            value = float(price) if isinstance(price, (int, float)) else None
            title = f"{item.get('name') or identifier} ({symbol})"
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    identifier,
                    observed_at,
                    observed_at,
                    symbol or identifier,
                    title,
                    value,
                    "USD",
                    "price",
                    url,
                    item,
                    {
                        "provider": "CoinGecko",
                        "source": url,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class StooqIndexAdapter(BaseAdapter):
    name = "stooq-global-indexes"
    layer = "global_stocks"
    endpoint = "https://stooq.com/q/l/"
    license_note = "Stooq free delayed quotes; redistribution subject to Stooq terms."
    completeness_note = "Selected major indexes only; delayed free-tier quotes are not full global equities coverage."

    SYMBOLS = (
        ("^spx", "S&P 500"),
        ("^ndq", "NASDAQ Composite"),
        ("^dji", "Dow Jones Industrial Average"),
        ("^ftse", "FTSE 100"),
        ("^n225", "Nikkei 225"),
    )

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        symbols = ",".join(symbol for symbol, _ in self.SYMBOLS)
        query = urllib.parse.urlencode({"s": symbols, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
        url = f"{self.endpoint}?{query}"
        body = _request(url, timeout=timeout, accept="text/csv,text/plain,*/*").decode("utf-8", "replace")
        observed_at = _now()
        output: list[MarketObservation] = []
        reader = csv.DictReader(io.StringIO(body))
        labels = {symbol: label for symbol, label in self.SYMBOLS}
        for row in reader:
            symbol = str(row.get("Symbol") or row.get("symbol") or "").strip()
            if not symbol or symbol.upper() == "N/D":
                continue
            close_raw = str(row.get("Close") or row.get("close") or "").strip()
            try:
                value = float(close_raw)
            except ValueError:
                continue
            date = str(row.get("Date") or row.get("date") or observed_at)
            title = labels.get(symbol.lower(), symbol)
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    symbol.lower(),
                    observed_at,
                    date,
                    symbol.upper(),
                    title,
                    value,
                    "INDEX",
                    "index_level",
                    url,
                    dict(row),
                    {
                        "provider": "Stooq",
                        "source": url,
                        "license_note": self.license_note,
                        "delay": "provider free-tier delay",
                    },
                )
            )
        return output


class FrankfurterFXAdapter(BaseAdapter):
    name = "frankfurter-fx"
    layer = "currencies"
    endpoint = "https://api.frankfurter.app/latest"
    license_note = "Frankfurter.app uses ECB reference rates; ECB terms apply."
    completeness_note = "ECB euro reference rates only; not a complete FX universe or real-time feed."

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        url = f"{self.endpoint}?from=EUR"
        data = _json(url, timeout=timeout)
        observed_at = _now()
        rates = data.get("rates") if isinstance(data, dict) else None
        if not isinstance(rates, dict):
            raise RuntimeError("unexpected Frankfurter payload")
        base = str(data.get("base") or "EUR")
        event_time = str(data.get("date") or observed_at)
        output: list[MarketObservation] = []
        for currency, rate in list(rates.items())[:40]:
            if not isinstance(rate, (int, float)):
                continue
            symbol = f"{base}/{currency}"
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    symbol,
                    observed_at,
                    event_time,
                    symbol,
                    f"{base} to {currency} reference rate",
                    float(rate),
                    str(currency),
                    "fx_rate",
                    url,
                    {"base": base, "quote": currency, "rate": rate, "date": event_time},
                    {
                        "provider": "Frankfurter / ECB",
                        "source": url,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class StooqCommoditiesAdapter(BaseAdapter):
    name = "stooq-commodities"
    layer = "commodities"
    endpoint = "https://stooq.com/q/l/"
    license_note = "Stooq free delayed quotes; redistribution subject to Stooq terms."
    completeness_note = "Selected commodity proxies only; not a complete commodity complex."

    SYMBOLS = (
        ("gc.f", "Gold futures proxy"),
        ("si.f", "Silver futures proxy"),
        ("cl.f", "Crude oil futures proxy"),
    )

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        symbols = ",".join(symbol for symbol, _ in self.SYMBOLS)
        query = urllib.parse.urlencode({"s": symbols, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
        url = f"{self.endpoint}?{query}"
        body = _request(url, timeout=timeout, accept="text/csv,text/plain,*/*").decode("utf-8", "replace")
        observed_at = _now()
        output: list[MarketObservation] = []
        labels = {symbol: label for symbol, label in self.SYMBOLS}
        reader = csv.DictReader(io.StringIO(body))
        for row in reader:
            symbol = str(row.get("Symbol") or row.get("symbol") or "").strip()
            close_raw = str(row.get("Close") or row.get("close") or "").strip()
            try:
                value = float(close_raw)
            except ValueError:
                continue
            if not symbol:
                continue
            date = str(row.get("Date") or row.get("date") or observed_at)
            title = labels.get(symbol.lower(), symbol)
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    symbol.lower(),
                    observed_at,
                    date,
                    symbol.upper(),
                    title,
                    value,
                    "USD",
                    "price",
                    url,
                    dict(row),
                    {
                        "provider": "Stooq",
                        "source": url,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class PolymarketPredictionAdapter(BaseAdapter):
    name = "polymarket-gamma"
    layer = "prediction_markets"
    endpoint = "https://gamma-api.polymarket.com/markets"
    license_note = "Polymarket public API terms apply; markets are not official forecasts."
    completeness_note = "Selected open markets only; not a complete prediction-market universe."

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        query = urllib.parse.urlencode({"limit": "25", "active": "true", "closed": "false"})
        url = f"{self.endpoint}?{query}"
        data = _json(url, timeout=timeout)
        observed_at = _now()
        rows = data if isinstance(data, list) else (data.get("data") or data.get("markets") or [])
        output: list[MarketObservation] = []
        for item in rows[:25]:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or item.get("conditionId") or item.get("slug") or "").strip()
            title = str(item.get("question") or item.get("title") or identifier).strip()
            if not identifier or not title:
                continue
            price = item.get("lastTradePrice")
            if price is None:
                prices = item.get("outcomePrices")
                if isinstance(prices, list) and prices:
                    try:
                        price = float(prices[0])
                    except (TypeError, ValueError):
                        price = None
                elif isinstance(prices, str):
                    try:
                        parsed = json.loads(prices)
                        price = float(parsed[0]) if parsed else None
                    except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                        price = None
            value = float(price) if isinstance(price, (int, float)) else None
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    identifier,
                    observed_at,
                    str(item.get("updatedAt") or item.get("endDate") or observed_at),
                    str(item.get("slug") or identifier)[:80],
                    title[:500],
                    value,
                    "PROB",
                    "probability",
                    url,
                    item,
                    {
                        "provider": "Polymarket Gamma API",
                        "source": url,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class FredEconomicAdapter(BaseAdapter):
    name = "fred-economic-indicators"
    layer = "economic_indicators"
    endpoint = "https://api.stlouisfed.org/fred/series/observations"
    license_note = "FRED® API terms apply; attribution to Federal Reserve Bank of St. Louis required."
    completeness_note = "Configured FRED series only; not a complete macroeconomic dataset."
    env_key = "AURORA_FRED_API_KEY"
    series = ("GDP", "UNRATE", "CPIAUCSL", "DFF")

    def configured(self) -> bool:
        return bool(str(os.getenv(self.env_key) or "").strip())

    def configuration(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "layer": self.layer,
            "configured": self.configured(),
            "api_key_env": self.env_key,
            "credentials_never_returned": True,
            "series": list(self.series),
        }

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        key = str(os.getenv(self.env_key) or "").strip()
        if not key:
            raise RuntimeError(f"{self.env_key} is not configured")
        observed_at = _now()
        output: list[MarketObservation] = []
        for series_id in self.series:
            query = urllib.parse.urlencode(
                {
                    "series_id": series_id,
                    "api_key": key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": "1",
                }
            )
            url = f"{self.endpoint}?{query}"
            data = _json(url, timeout=timeout)
            observations = (data.get("observations") or []) if isinstance(data, dict) else []
            if not observations:
                continue
            row = observations[0]
            raw = str(row.get("value") or "").strip()
            try:
                value = float(raw)
            except ValueError:
                continue
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    series_id,
                    observed_at,
                    str(row.get("date") or observed_at),
                    series_id,
                    f"FRED series {series_id}",
                    value,
                    "USD",
                    "index_or_rate",
                    f"https://fred.stlouisfed.org/series/{series_id}",
                    row,
                    {
                        "provider": "Federal Reserve Bank of St. Louis FRED",
                        "source": self.endpoint,
                        "series_id": series_id,
                        "credential_env": self.env_key,
                        "credential_committed": False,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class ConfiguredEnergyAdapter(BaseAdapter):
    name = "configured-energy-feed"
    layer = "energy"
    endpoint = "environment-configured"
    license_note = "License must be supplied and reviewed by the operator before public redistribution."
    completeness_note = "Coverage is limited to the operator-configured official energy feed."
    url_env = "AURORA_ENERGY_FEED_URL"
    license_env = "AURORA_ENERGY_FEED_LICENSE"
    api_key_env = "AURORA_ENERGY_API_KEY"

    def configured(self) -> bool:
        return bool(str(os.getenv(self.url_env) or "").strip())

    def configuration(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "layer": self.layer,
            "configured": self.configured(),
            "url_env": self.url_env,
            "api_key_env": self.api_key_env,
            "credentials_never_returned": True,
        }

    def fetch(self, timeout: int = 30) -> list[MarketObservation]:
        url = str(os.getenv(self.url_env) or "").strip()
        key = str(os.getenv(self.api_key_env) or "").strip()
        if not url:
            raise RuntimeError(f"{self.url_env} is not configured")
        if "{api_key}" in url:
            url = url.replace("{api_key}", urllib.parse.quote(key, safe=""))
        data = _json(url, timeout=timeout)
        rows = data if isinstance(data, list) else (data.get("data") or data.get("items") or data.get("series") or [])
        observed_at = _now()
        output: list[MarketObservation] = []
        for index, item in enumerate(rows[:200]):
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or item.get("symbol") or item.get("series_id") or index)
            title = str(item.get("title") or item.get("name") or item.get("symbol") or f"energy {identifier}")
            raw = item.get("value") if item.get("value") is not None else item.get("price")
            value = float(raw) if isinstance(raw, (int, float)) else None
            output.append(
                MarketObservation(
                    self.layer,
                    self.name,
                    identifier,
                    observed_at,
                    str(item.get("event_time") or item.get("date") or observed_at),
                    str(item.get("symbol") or identifier)[:80],
                    title[:500],
                    value,
                    str(item.get("currency") or "USD"),
                    str(item.get("unit") or "price"),
                    url,
                    item,
                    {
                        "provider": self.name,
                        "source": self.url_env,
                        "credential_env": self.api_key_env,
                        "credential_committed": False,
                        "license_note": str(os.getenv(self.license_env) or self.license_note),
                    },
                )
            )
        return output


class MarketCoordinator:
    def __init__(self, store: MarketStore) -> None:
        self.store = store
        self.adapters: list[BaseAdapter] = [
            StooqIndexAdapter(),
            ConfiguredEnergyAdapter(),
            StooqCommoditiesAdapter(),
            FrankfurterFXAdapter(),
            CoinGeckoCryptoAdapter(),
            FredEconomicAdapter(),
            PolymarketPredictionAdapter(),
        ]
        self._ensure_registered()

    def _ensure_registered(self) -> None:
        existing = {row["provider"] for row in self.store.providers()}
        for adapter in self.adapters:
            if adapter.name not in existing:
                self.store.upsert_provider(
                    {
                        "provider": adapter.name,
                        "layer": adapter.layer,
                        "state": "NOT_CONFIGURED",
                        "last_attempt_at": "",
                        "last_success_at": "",
                        "consecutive_failures": 0,
                        "freshness_seconds": 0,
                        "last_error": "",
                        "completeness_note": adapter.completeness_note,
                        "license_note": adapter.license_note,
                    }
                )

    def _observe(self, adapter: BaseAdapter, *, successful: bool, configured: bool, freshness_seconds: int, error: str) -> None:
        existing = next(row for row in self.store.providers() if row["provider"] == adapter.name)
        now = _now()
        failures = 0 if successful else int(existing.get("consecutive_failures") or 0) + (1 if configured else 0)
        if not configured:
            state = "NOT_CONFIGURED"
        elif successful:
            state = "ONLINE"
        else:
            state = "OFFLINE" if failures >= 3 else "DEGRADED"
        self.store.upsert_provider(
            {
                **existing,
                "state": state,
                "last_attempt_at": now if configured else existing.get("last_attempt_at", ""),
                "last_success_at": now if successful else existing.get("last_success_at", ""),
                "consecutive_failures": failures,
                "freshness_seconds": max(0, int(freshness_seconds)),
                "last_error": "" if successful or not configured else error,
            }
        )

    def run(self, provider: str, *, timeout: int = 30) -> ProviderRun:
        adapter = next((row for row in self.adapters if row.name == provider), None)
        if adapter is None:
            raise KeyError("market provider not found")
        started_at = _now()
        started_clock = time.monotonic()
        configured = adapter.configured()
        observations: list[MarketObservation] = []
        error = ""
        if configured:
            try:
                observations = adapter.fetch(timeout=timeout)
                if not observations:
                    error = "provider returned no valid observations"
            except Exception as exc:
                error = str(exc)
        else:
            error = "provider is not configured"
        successful = configured and bool(observations) and not error
        freshness = 0
        if observations:
            times = [_parse_time(row.event_time) for row in observations]
            valid = [value for value in times if value is not None]
            freshness = max(0, int((datetime.now(timezone.utc) - max(valid)).total_seconds())) if valid else 0
        if successful:
            for observation in observations:
                self.store.record(observation)
        completed_at = _now()
        run = ProviderRun(
            adapter.name,
            adapter.layer,
            configured,
            successful,
            len(observations),
            error,
            started_at,
            completed_at,
            max(0, int((time.monotonic() - started_clock) * 1000)),
        )
        self._observe(adapter, successful=successful, configured=configured, freshness_seconds=freshness, error=error)
        self.store.record_run(run, {"freshness_seconds": freshness})
        return run

    def run_all(self, *, timeout: int = 30) -> list[ProviderRun]:
        return [self.run(adapter.name, timeout=timeout) for adapter in self.adapters]

    def configuration(self) -> dict[str, Any]:
        return {
            "providers": [adapter.configuration() for adapter in self.adapters],
            "credentials_never_returned": True,
            "registration_is_not_live_evidence": True,
        }
