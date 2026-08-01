from __future__ import annotations

import json
import os
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

LAYERS = (
    "severe_weather",
    "wildfire",
    "outage",
    "bgp",
    "power",
    "cyber",
    "sanctions",
    "government_alerts",
)
PROVIDER_STATES = {"ONLINE", "DEGRADED", "OFFLINE", "NOT_CONFIGURED"}
USER_AGENT = "AURORA-LIVE/1.0 (+https://github.com/hr185882-creator/intel-tripwire)"


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


def _ssl_contexts() -> list[ssl.SSLContext]:
    """Prefer system trust, then certifi when available (Windows/corporate MITM hosts)."""
    contexts: list[ssl.SSLContext] = [ssl.create_default_context()]
    try:
        import certifi

        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    return contexts


def _request(url: str, *, timeout: int = 30, accept: str = "application/json") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    errors: list[str] = []
    for context in _ssl_contexts():
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return response.read()
        except (ssl.SSLError, urllib.error.URLError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
    # Final attempt without an explicit context preserves historical behavior.
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        raise RuntimeError(f"HTTP request failed for {url}; attempts: {'; '.join(errors)}") from exc


def _json(url: str, *, timeout: int = 30) -> Any:
    return json.loads(_request(url, timeout=timeout).decode("utf-8", "replace"))


def _local(tag: str) -> str:
    return str(tag).split("}")[-1]


def _first_text(node: ET.Element, names: set[str]) -> str:
    for child in node.iter():
        if _local(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _coordinate(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, list):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            return float(value[1]), float(value[0])
        for item in value:
            latitude, longitude = _coordinate(item)
            if latitude is not None and longitude is not None:
                return latitude, longitude
    return None, None


@dataclass(frozen=True)
class InfrastructureObservation:
    layer: str
    provider: str
    external_id: str
    observed_at: str
    event_time: str
    severity: str
    title: str
    summary: str
    source_url: str
    payload: dict[str, Any]
    provenance: dict[str, Any]
    latitude: float | None = None
    longitude: float | None = None

    def value(self) -> dict[str, Any]:
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


class InfrastructureStore:
    """Durable SQLite/PostgreSQL evidence, provider health, and run history."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL infrastructure storage") from exc
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
        observation_pk = "observation_id BIGSERIAL PRIMARY KEY" if self.postgres else "observation_id INTEGER PRIMARY KEY AUTOINCREMENT"
        run_pk = "run_id BIGSERIAL PRIMARY KEY" if self.postgres else "run_id INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            f"""CREATE TABLE IF NOT EXISTS infrastructure_observations (
                {observation_pk}, layer TEXT NOT NULL, provider TEXT NOT NULL,
                external_id TEXT NOT NULL, observed_at TEXT NOT NULL, event_time TEXT NOT NULL,
                severity TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                source_url TEXT NOT NULL, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION,
                payload_json TEXT NOT NULL, provenance_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS infrastructure_provider_health (
                provider TEXT PRIMARY KEY, layer TEXT NOT NULL, state TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL, last_success_at TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL, freshness_seconds INTEGER NOT NULL,
                last_error TEXT NOT NULL, completeness_note TEXT NOT NULL,
                license_note TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS infrastructure_provider_runs (
                {run_pk}, provider TEXT NOT NULL, layer TEXT NOT NULL,
                configured INTEGER NOT NULL, successful INTEGER NOT NULL,
                observations INTEGER NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                error TEXT NOT NULL, metadata_json TEXT NOT NULL)""",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_infra_observation_identity ON infrastructure_observations(layer,provider,external_id)",
            "CREATE INDEX IF NOT EXISTS idx_infra_layer_time ON infrastructure_observations(layer,observation_id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_infra_runs_provider ON infrastructure_provider_runs(provider,run_id DESC)",
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

    def record(self, observation: InfrastructureObservation) -> int:
        if observation.layer not in LAYERS:
            raise ValueError("invalid infrastructure layer")
        if not observation.provider or not observation.external_id:
            raise ValueError("provider and external_id are required")
        values = (
            observation.layer,
            observation.provider,
            observation.external_id,
            observation.observed_at,
            observation.event_time,
            observation.severity,
            observation.title,
            observation.summary,
            observation.source_url,
            observation.latitude,
            observation.longitude,
            json.dumps(observation.payload, sort_keys=True, separators=(",", ":")),
            json.dumps(observation.provenance, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute(
                f"SELECT observation_id FROM infrastructure_observations WHERE layer={self._p} AND provider={self._p} AND external_id={self._p} LIMIT 1",
                (observation.layer, observation.provider, observation.external_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return self._id(existing, "observation_id")
            if self.postgres:
                cursor.execute(
                    "INSERT INTO infrastructure_observations(layer,provider,external_id,observed_at,event_time,severity,title,summary,source_url,latitude,longitude,payload_json,provenance_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING observation_id",
                    values,
                )
                identifier = self._id(cursor.fetchone(), "observation_id")
            else:
                cursor.execute(
                    "INSERT INTO infrastructure_observations(layer,provider,external_id,observed_at,event_time,severity,title,summary,source_url,latitude,longitude,payload_json,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                identifier = int(cursor.lastrowid)
            self._connection.commit()
            return identifier

    def observations(self, layer: str = "", provider: str = "", limit: int = 250) -> list[dict[str, Any]]:
        if layer and layer not in LAYERS:
            raise ValueError("invalid infrastructure layer")
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
                f"SELECT * FROM infrastructure_observations{where} ORDER BY observation_id DESC LIMIT {self._p}",
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
            raise ValueError("invalid infrastructure layer")
        if state not in PROVIDER_STATES:
            raise ValueError("invalid provider state")
        columns = (
            "provider", "layer", "state", "last_attempt_at", "last_success_at",
            "consecutive_failures", "freshness_seconds", "last_error",
            "completeness_note", "license_note", "updated_at",
        )
        payload = {**value, "updated_at": value.get("updated_at") or _now()}
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        sql = f"INSERT INTO infrastructure_provider_health({','.join(columns)}) VALUES ({','.join([self._p] * len(columns))}) ON CONFLICT(provider) DO UPDATE SET {updates}"
        with self._lock:
            self._connection.execute(sql, tuple(payload.get(column, "") for column in columns))
            self._connection.commit()

    def providers(self, layer: str = "") -> list[dict[str, Any]]:
        if layer and layer not in LAYERS:
            raise ValueError("invalid infrastructure layer")
        with self._lock:
            if layer:
                rows = self._connection.execute(
                    f"SELECT * FROM infrastructure_provider_health WHERE layer={self._p} ORDER BY provider",
                    (layer,),
                ).fetchall()
            else:
                rows = self._connection.execute("SELECT * FROM infrastructure_provider_health ORDER BY layer,provider").fetchall()
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
                    "INSERT INTO infrastructure_provider_runs(provider,layer,configured,successful,observations,started_at,completed_at,duration_ms,error,metadata_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id",
                    values,
                )
                identifier = self._id(cursor.fetchone(), "run_id")
            else:
                cursor.execute(
                    "INSERT INTO infrastructure_provider_runs(provider,layer,configured,successful,observations,started_at,completed_at,duration_ms,error,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                identifier = int(cursor.lastrowid)
            self._connection.commit()
            return identifier

    def runs(self, layer: str = "", provider: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if layer and layer not in LAYERS:
            raise ValueError("invalid infrastructure layer")
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
                f"SELECT * FROM infrastructure_provider_runs{where} ORDER BY run_id DESC LIMIT {self._p}",
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
        maximum_age = max(60, int(max_age_seconds or os.getenv("AURORA_INFRASTRUCTURE_STALE_SECONDS", "3600")))
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
            operational = bool(fresh and provider_observations and latest_run and latest_run["successful"] and latest_run["observations"] > 0)
            provider_health.append({**provider, "seconds_since_success": age, "fresh": fresh, "operational": operational, "observations": len(provider_observations), "latest_run": latest_run})
        layer_health = []
        for layer in LAYERS:
            rows = [row for row in provider_health if row["layer"] == layer]
            layer_health.append({"layer": layer, "providers": len(rows), "operational_providers": sum(1 for row in rows if row["operational"]), "operational": any(row["operational"] for row in rows)})
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
            "requirement": "each layer requires a configured provider, a recent successful run, and fresh durably persisted observations",
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
        return {"provider": self.name, "layer": self.layer, "configured": self.configured(), "endpoint": self.endpoint}


class NWSAlertsAdapter(BaseAdapter):
    name = "nws-active-alerts"
    layer = "severe_weather"
    endpoint = "https://api.weather.gov/alerts/active"
    license_note = "U.S. government public-domain data; weather.gov API terms and attribution apply."
    completeness_note = "Official NWS alerts cover the United States and its territories, not global severe weather."

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        data = _json(self.endpoint, timeout=timeout)
        observed_at = _now()
        output = []
        for feature in (data.get("features") or [])[:250]:
            props = feature.get("properties") or {}
            identifier = str(props.get("id") or feature.get("id") or "").strip()
            title = str(props.get("headline") or props.get("event") or "").strip()
            if not identifier or not title:
                continue
            latitude, longitude = _coordinate((feature.get("geometry") or {}).get("coordinates"))
            output.append(InfrastructureObservation(
                self.layer, self.name, identifier, observed_at,
                str(props.get("sent") or props.get("effective") or observed_at),
                str(props.get("severity") or "Unknown").upper(), title,
                str(props.get("description") or props.get("instruction") or props.get("areaDesc") or "")[:2000],
                str(props.get("@id") or props.get("id") or self.endpoint), props,
                {"provider": "NOAA National Weather Service", "source": self.endpoint, "license_note": self.license_note},
                latitude, longitude,
            ))
        return output


class EONETWildfireAdapter(BaseAdapter):
    name = "nasa-eonet-wildfires"
    layer = "wildfire"
    endpoint = "https://eonet.gsfc.nasa.gov/api/v3/events?category=wildfires&status=open&limit=200"
    license_note = "NASA EONET metadata is subject to the EONET disclaimer and source-specific terms."
    completeness_note = "EONET is a curated natural-event catalog and is not a complete global fire-detection product."

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        data = _json(self.endpoint, timeout=timeout)
        observed_at = _now()
        output = []
        for event in (data.get("events") or [])[:200]:
            identifier = str(event.get("id") or "").strip()
            title = str(event.get("title") or "").strip()
            if not identifier or not title:
                continue
            geometries = event.get("geometry") or []
            latest = geometries[-1] if geometries else {}
            latitude, longitude = _coordinate(latest.get("coordinates"))
            source_url = next((str(item.get("url")) for item in event.get("sources") or [] if item.get("url")), event.get("link") or self.endpoint)
            output.append(InfrastructureObservation(
                self.layer, self.name, identifier, observed_at,
                str(latest.get("date") or observed_at), "UNKNOWN", title,
                str(event.get("description") or "")[:2000], str(source_url), event,
                {"provider": "NASA EONET", "source": self.endpoint, "license_note": self.license_note},
                latitude, longitude,
            ))
        return output


class CISAKEVAdapter(BaseAdapter):
    name = "cisa-kev"
    layer = "cyber"
    endpoint = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    license_note = "Official U.S. government CISA catalog; public-use and attribution requirements apply."
    completeness_note = "KEV covers known exploited vulnerabilities meeting CISA criteria, not all cyber incidents or vulnerabilities."

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        data = _json(self.endpoint, timeout=timeout)
        observed_at = _now()
        output = []
        for item in (data.get("vulnerabilities") or [])[:1000]:
            identifier = str(item.get("cveID") or "").strip()
            if not identifier:
                continue
            title = f"{identifier}: {item.get('vulnerabilityName') or 'Known exploited vulnerability'}"
            output.append(InfrastructureObservation(
                self.layer, self.name, identifier, observed_at,
                str(item.get("dateAdded") or observed_at), "HIGH", title,
                str(item.get("shortDescription") or item.get("requiredAction") or "")[:2000],
                f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext={urllib.parse.quote(identifier)}",
                item, {"provider": "Cybersecurity and Infrastructure Security Agency", "source": self.endpoint, "license_note": self.license_note},
            ))
        return output


class OFACSDNAdapter(BaseAdapter):
    name = "ofac-sdn"
    layer = "sanctions"
    endpoint = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
    # Secondary official mirror used when the primary host fails TLS verification on
    # some operator networks (self-signed interceptors / incomplete CA stores).
    fallback_endpoints = (
        "https://www.treasury.gov/ofac/downloads/sdn.xml",
    )
    license_note = "Official U.S. Treasury OFAC sanctions-list data; screening and legal interpretation remain the user's responsibility."
    completeness_note = "The SDN list is one OFAC product and does not represent every sanctions regime or legal restriction worldwide."

    def fetch(self, timeout: int = 45) -> list[InfrastructureObservation]:
        endpoints = (self.endpoint, *self.fallback_endpoints)
        last_error: Exception | None = None
        body = b""
        source_url = self.endpoint
        for url in endpoints:
            try:
                body = _request(url, timeout=timeout, accept="application/xml,text/xml,*/*")
                if body and body.lstrip().startswith(b"<"):
                    source_url = url
                    break
                last_error = RuntimeError(f"empty or non-XML body from {url}")
            except Exception as exc:  # noqa: BLE001 - try next official endpoint
                last_error = exc
                continue
        else:
            raise RuntimeError(f"OFAC SDN fetch failed across endpoints: {last_error}")

        root = ET.fromstring(body)
        observed_at = _now()
        output = []
        for entry in [node for node in root.iter() if _local(node.tag) == "sdnEntry"][:1000]:
            identifier = _first_text(entry, {"uid"})
            if not identifier:
                continue
            first = _first_text(entry, {"firstName"})
            last = _first_text(entry, {"lastName"})
            entity_type = _first_text(entry, {"sdnType"})
            programs = [child.text.strip() for child in entry.iter() if _local(child.tag) == "program" and child.text and child.text.strip()]
            title = " ".join(part for part in (first, last) if part).strip() or f"OFAC SDN {identifier}"
            output.append(InfrastructureObservation(
                self.layer, self.name, identifier, observed_at, observed_at, "LEGAL", title,
                f"Type: {entity_type or 'unspecified'}; programs: {', '.join(programs[:20])}",
                source_url, {"uid": identifier, "first_name": first, "last_name": last, "type": entity_type, "programs": programs},
                {"provider": "U.S. Treasury Office of Foreign Assets Control", "source": source_url, "license_note": self.license_note},
            ))
        if not output:
            raise RuntimeError("OFAC SDN XML parsed but contained no sdnEntry nodes")
        return output


class FEMAGovernmentAlertsAdapter(BaseAdapter):
    name = "fema-disaster-declarations"
    layer = "government_alerts"
    endpoint = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?%24top=100&%24orderby=declarationDate%20desc"
    license_note = "Official FEMA OpenFEMA public data."
    completeness_note = "This layer contains U.S. disaster declarations, not a universal real-time government alert feed."

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        data = _json(self.endpoint, timeout=timeout)
        observed_at = _now()
        output = []
        for item in (data.get("DisasterDeclarationsSummaries") or [])[:100]:
            number = str(item.get("disasterNumber") or "").strip()
            state = str(item.get("state") or "").strip()
            if not number:
                continue
            title = f"FEMA {item.get('declarationType') or 'disaster'} declaration {number}: {item.get('declarationTitle') or state}"
            output.append(InfrastructureObservation(
                self.layer, self.name, f"{number}:{state}", observed_at,
                str(item.get("declarationDate") or observed_at), "GOVERNMENT", title,
                str(item.get("incidentType") or "")[:2000], f"https://www.fema.gov/disaster/{number}", item,
                {"provider": "Federal Emergency Management Agency", "source": self.endpoint, "license_note": self.license_note},
            ))
        return output


class RIPEBGPAdapter(BaseAdapter):
    name = "ripe-stat-routing-status"
    layer = "bgp"
    endpoint = "https://stat.ripe.net/data/routing-status/data.json"
    license_note = "RIPE NCC RIPEstat terms and attribution apply."
    completeness_note = "Results are scoped to configured prefixes or ASNs and the RIPE RIS collector view; they are not a complete Internet-outage detector."
    env_key = "AURORA_BGP_RESOURCES"

    def resources(self) -> list[str]:
        return [item.strip() for item in str(os.getenv(self.env_key) or "").split(",") if item.strip()][:100]

    def configured(self) -> bool:
        return bool(self.resources())

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        observed_at = _now()
        output = []
        for resource in self.resources():
            url = f"{self.endpoint}?{urllib.parse.urlencode({'resource': resource})}"
            payload = _json(url, timeout=timeout)
            if payload.get("status") != "ok":
                raise RuntimeError(f"RIPEstat query failed for {resource}")
            data = payload.get("data") or {}
            announced = bool(data.get("announced_space") or data.get("visibility") or data.get("first_seen"))
            title = f"RIPEstat routing status for {resource}: {'visible' if announced else 'not visibly announced'}"
            output.append(InfrastructureObservation(
                self.layer, self.name, resource, observed_at,
                str(data.get("query_time") or observed_at), "INFO" if announced else "HIGH", title,
                f"Observed from RIPE RIS collectors; scoped resource {resource}", url, data,
                {"provider": "RIPE NCC", "source": url, "scope_env": self.env_key, "license_note": self.license_note},
            ))
        return output


class ConfiguredJSONAdapter(BaseAdapter):
    def __init__(self, *, name: str, layer: str, url_env: str, license_env: str, api_key_env: str = "") -> None:
        self.name = name
        self.layer = layer
        self.url_env = url_env
        self.license_env = license_env
        self.api_key_env = api_key_env
        self.endpoint = "environment-configured"
        self.completeness_note = "Coverage is limited to the operator-configured official feed and must not be generalized beyond that scope."
        self.license_note = "License must be supplied and reviewed by the operator before public redistribution."

    def configured(self) -> bool:
        return bool(str(os.getenv(self.url_env) or "").strip()) and (not self.api_key_env or bool(str(os.getenv(self.api_key_env) or "").strip()))

    def configuration(self) -> dict[str, Any]:
        return {"provider": self.name, "layer": self.layer, "configured": self.configured(), "url_env": self.url_env, "api_key_env": self.api_key_env or None, "credentials_never_returned": True}

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        url = str(os.getenv(self.url_env) or "").strip()
        key = str(os.getenv(self.api_key_env) or "").strip() if self.api_key_env else ""
        if not self.configured():
            raise RuntimeError(f"{self.url_env} is not configured")
        if "{api_key}" in url:
            url = url.replace("{api_key}", urllib.parse.quote(key, safe=""))
        data = _json(url, timeout=timeout)
        rows = data if isinstance(data, list) else (data.get("events") or data.get("outages") or data.get("data") or data.get("items") or [])
        observed_at = _now()
        output = []
        for index, item in enumerate(rows[:500]):
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or item.get("event_id") or item.get("outage_id") or item.get("timestamp") or index)
            title = str(item.get("title") or item.get("name") or item.get("event") or item.get("status") or f"{self.layer} observation {identifier}")
            event_time = str(item.get("event_time") or item.get("updated_at") or item.get("timestamp") or observed_at)
            latitude = item.get("latitude") if isinstance(item.get("latitude"), (int, float)) else None
            longitude = item.get("longitude") if isinstance(item.get("longitude"), (int, float)) else None
            output.append(InfrastructureObservation(
                self.layer, self.name, identifier, observed_at, event_time,
                str(item.get("severity") or "UNKNOWN").upper(), title[:500],
                str(item.get("summary") or item.get("description") or "")[:2000], url, item,
                {"provider": self.name, "source": self.url_env, "credential_env": self.api_key_env or None, "credential_committed": False, "license_note": str(os.getenv(self.license_env) or self.license_note)},
                float(latitude) if latitude is not None else None,
                float(longitude) if longitude is not None else None,
            ))
        return output


class InfrastructureCoordinator:
    def __init__(self, store: InfrastructureStore) -> None:
        self.store = store
        self.adapters: list[BaseAdapter] = [
            NWSAlertsAdapter(),
            EONETWildfireAdapter(),
            ConfiguredJSONAdapter(name="configured-official-outage-feed", layer="outage", url_env="AURORA_OUTAGE_FEED_URL", license_env="AURORA_OUTAGE_FEED_LICENSE"),
            RIPEBGPAdapter(),
            ConfiguredJSONAdapter(name="configured-official-power-feed", layer="power", url_env="AURORA_POWER_FEED_URL", license_env="AURORA_POWER_FEED_LICENSE", api_key_env="AURORA_POWER_API_KEY"),
            CISAKEVAdapter(),
            OFACSDNAdapter(),
            FEMAGovernmentAlertsAdapter(),
        ]
        self._ensure_registered()

    def _ensure_registered(self) -> None:
        existing = {row["provider"] for row in self.store.providers()}
        for adapter in self.adapters:
            if adapter.name not in existing:
                self.store.upsert_provider({
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
                })

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
        self.store.upsert_provider({
            **existing,
            "state": state,
            "last_attempt_at": now if configured else existing.get("last_attempt_at", ""),
            "last_success_at": now if successful else existing.get("last_success_at", ""),
            "consecutive_failures": failures,
            "freshness_seconds": max(0, int(freshness_seconds)),
            "last_error": "" if successful or not configured else error,
        })

    def run(self, provider: str, *, timeout: int = 30) -> ProviderRun:
        adapter = next((row for row in self.adapters if row.name == provider), None)
        if adapter is None:
            raise KeyError("infrastructure provider not found")
        started_at = _now()
        started_clock = time.monotonic()
        configured = adapter.configured()
        observations: list[InfrastructureObservation] = []
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
        run = ProviderRun(adapter.name, adapter.layer, configured, successful, len(observations), error, started_at, completed_at, max(0, int((time.monotonic() - started_clock) * 1000)))
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
