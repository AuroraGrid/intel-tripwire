from __future__ import annotations

import concurrent.futures
import os
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Callable

import app

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"}


@dataclass(frozen=True)
class Adapter:
    name: str
    family: str
    tier: int
    official: bool
    capability: str
    fetcher: Callable[[], list[app.Evidence]]


def canonical_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(value or "").strip()
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = (parsed.path or "/").rstrip("/") or "/"
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), host, path, urllib.parse.urlencode(sorted(query)), ""))


def reliability(tier: int, official: bool, status: str = "online", records: int = 1, latency_ms: int = 0) -> int:
    score = {1: 82, 2: 68, 3: 52}.get(int(tier), 42) + (10 if official else 0)
    score -= 8 if records < 1 else 0
    score -= 5 if latency_ms > 3000 else 0
    score -= 5 if latency_ms > 8000 else 0
    score -= 35 if status == "degraded" else 0
    return max(0, min(100, score))


def origin(record: app.Evidence) -> str:
    explicit = getattr(record, "source_origin", "")
    return explicit or record.source_family


def duplicate(left: app.Evidence, right: app.Evidence) -> bool:
    if origin(left) == origin(right):
        return True
    if left.source_family != right.source_family:
        return False
    close = abs((app.parse_date(left.published_at) - app.parse_date(right.published_at)).total_seconds()) <= 86400
    return close and (canonical_url(left.url) == canonical_url(right.url) or app.jaccard(app.tokens(left.title), app.tokens(right.title)) >= 0.82)


def collapse(records: list[app.Evidence]) -> tuple[list[app.Evidence], int]:
    retained: list[app.Evidence] = []
    suppressed = 0
    for record in sorted(records, key=lambda x: (x.official, -x.source_tier, app.parse_date(x.published_at)), reverse=True):
        if any(duplicate(record, current) for current in retained):
            suppressed += 1
        else:
            retained.append(record)
    return retained, suppressed


def fetch_cisa() -> list[app.Evidence]:
    data = app.fetch_json("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    output = []
    for item in sorted(data.get("vulnerabilities", []), key=lambda x: x.get("dateAdded", ""), reverse=True)[:100]:
        cve = app.clean(item.get("cveID"), 40)
        if not cve:
            continue
        title = app.clean(f"CISA KEV: {item.get('vendorProject', '')} {item.get('product', '')} - {item.get('vulnerabilityName', cve)}", 300)
        url = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext=" + urllib.parse.quote(cve)
        record = app.Evidence(app.stable_id("cisa", cve), title, url, "CISA KEV", "cisa.gov", 1, app.parse_date(item.get("dateAdded")).isoformat(), "official_cyber_advisory", True, app.clean(item.get("shortDescription"), 400), raw_source="CISA KEV")
        setattr(record, "source_origin", "cisa-kev")
        output.append(record)
    return output


def fetch_reliefweb() -> list[app.Evidence]:
    query = urllib.parse.urlencode({"appname": "aurora-live", "profile": "list", "preset": "latest", "limit": 100})
    data = app.fetch_json("https://api.reliefweb.int/v1/reports?" + query)
    output = []
    for item in data.get("data", []):
        fields = item.get("fields", {})
        title = app.clean(fields.get("title"), 300)
        if not title:
            continue
        record_id = str(item.get("id", ""))
        url = fields.get("url_alias") or "https://reliefweb.int/node/" + record_id
        record = app.Evidence(app.stable_id("reliefweb", record_id, title), title, url, "ReliefWeb", "reliefweb.int", 1, app.parse_date((fields.get("date") or {}).get("created")).isoformat(), "humanitarian_report", True, "Humanitarian report indexed by ReliefWeb.", raw_source="ReliefWeb Reports API")
        setattr(record, "source_origin", "reliefweb:" + record_id)
        output.append(record)
    return output


def adapters(query: str) -> list[Adapter]:
    return [
        Adapter("GDELT", "gdeltproject.org", 2, False, "multilingual_news", lambda: app.fetch_gdelt(query)),
        Adapter("USGS", "usgs.gov", 1, True, "earthquakes", app.fetch_usgs),
        Adapter("NASA EONET", "nasa.gov", 1, True, "natural_events", app.fetch_eonet),
        Adapter("GDACS", "gdacs.org", 1, True, "disaster_alerts", app.fetch_gdacs),
        Adapter("CISA KEV", "cisa.gov", 1, True, "cyber_vulnerabilities", fetch_cisa),
        Adapter("ReliefWeb", "reliefweb.int", 1, True, "humanitarian_reports", fetch_reliefweb),
    ]


class ReleaseAggregator:
    def __init__(self, adapter_factory=adapters, fixture_loader=app.load_fixtures, cache_ttl=app.CACHE_TTL):
        self.adapter_factory = adapter_factory
        self.fixture_loader = fixture_loader
        self.cache_ttl = cache_ttl
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.cached_at = 0.0
        self.cache = None

    def collect(self, query: str = app.DEFAULT_QUERY, force: bool = False):
        with self.lock:
            if not force and self.cache and self.cache.get("query") == query and time.time() - self.cached_at < self.cache_ttl:
                return self.cache
        with self.refresh_lock:
            with self.lock:
                if not force and self.cache and self.cache.get("query") == query and time.time() - self.cached_at < self.cache_ttl:
                    return self.cache
            records, health, metadata = [], [], {}
            source_list = self.adapter_factory(query)
            offline = os.getenv("AURORA_OFFLINE", "").lower() in {"1", "true", "yes"}
            if not offline:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(source_list))) as pool:
                    starts = {pool.submit(source.fetcher): (source, time.monotonic()) for source in source_list}
                    for future in concurrent.futures.as_completed(starts):
                        source, started = starts[future]
                        latency = round((time.monotonic() - started) * 1000)
                        try:
                            items = future.result()
                            score = reliability(source.tier, source.official, records=len(items), latency_ms=latency)
                            for record in items:
                                setattr(record, "source_origin", getattr(record, "source_origin", "") or record.source_family)
                                metadata[record.id] = {"canonical_url": canonical_url(record.url), "lineage_id": app.stable_id("origin", origin(record)), "source_origin": origin(record), "reliability_score": score}
                            records.extend(items)
                            health.append({"source": source.name, "family": source.family, "capability": source.capability, "tier": source.tier, "official": source.official, "status": "online", "records": len(items), "latency_ms": latency, "reliability_score": score, "error": None})
                        except Exception as exc:
                            health.append({"source": source.name, "family": source.family, "capability": source.capability, "tier": source.tier, "official": source.official, "status": "degraded", "records": 0, "latency_ms": latency, "reliability_score": reliability(source.tier, source.official, "degraded", 0, latency), "error": app.clean(exc, 160)})
            raw_count = len(records)
            records, suppressed = collapse(records)
            if not records:
                records = self.fixture_loader()
                raw_count = len(records)
                for record in records:
                    metadata[record.id] = {"canonical_url": canonical_url(record.url), "lineage_id": app.stable_id("origin", record.source_family), "source_origin": record.source_family, "reliability_score": 0}
                health.append({"source": "Bundled fixtures", "family": "local", "capability": "demonstration", "tier": 0, "official": False, "status": "offline_fallback", "records": len(records), "latency_ms": 0, "reliability_score": 0, "error": None})
            claims = [app.assess_cluster(group) for group in app.cluster_evidence(records)]
            rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            claims.sort(key=lambda value: (rank[value.severity], value.confidence_score, app.parse_date(value.updated_at)), reverse=True)
            events = []
            for claim in claims:
                event = asdict(claim)
                for evidence in event.get("evidence", []):
                    evidence.update(metadata.get(evidence["id"], {}))
                events.append(event)
            fallback = any(item["status"] == "offline_fallback" for item in health)
            degraded = any(item["status"] == "degraded" for item in health)
            payload = {"schema_version": "1.0", "generated_at": app.now_iso(), "mode": "offline_fallback" if fallback else "live_degraded" if degraded else "live", "query": query, "event_count": len(events), "evidence_count": len(records), "raw_evidence_count": raw_count, "duplicates_suppressed": suppressed, "sources": sorted(health, key=lambda x: x["source"]), "events": events, "methodology": {"status": "SUPPORTED requires an official record or 3 independent source families including Tier 1/2; PLAUSIBLE requires 2; otherwise NOT_PROVEN.", "grade": "G1 directional, G2 high-confidence assessment, G3 directly verified observation/outcome.", "lineage": "Shared origins and near-identical same-family records are collapsed before corroboration is counted.", "warning": "Scores are triage aids. Verification applies only to the claim actually evidenced."}}
            with self.lock:
                self.cache, self.cached_at = payload, time.time()
            return payload
