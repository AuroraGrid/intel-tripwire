from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import app
from release_engine import Adapter, adapters as base_adapters


@dataclass(frozen=True)
class SourceSpec:
    name: str
    provider: str
    family: str
    capability: str
    observation_type: str
    coverage: str
    refresh_seconds: int
    license: str
    url: str
    parser: str = "feed"
    limit: int = 100


SOURCES = [
    SourceSpec("NWS Active Alerts", "NOAA National Weather Service", "weather.gov", "weather_alerts", "official_alert", "United States", 60, "U.S. public domain", "https://api.weather.gov/alerts/active", "nws"),
    SourceSpec("AWC SIGMET", "NOAA Aviation Weather Center", "aviationweather.gov", "aviation_warnings", "official_alert", "global", 60, "U.S. public domain", "https://aviationweather.gov/api/data/airsigmet?format=json", "json_rows"),
    SourceSpec("AWC METAR", "NOAA Aviation Weather Center", "aviationweather.gov", "aviation_observations", "direct_observation", "global", 60, "U.S. public domain", "https://aviationweather.gov/api/data/metar?format=json&hours=1", "json_rows"),
    SourceSpec("AWC TAF", "NOAA Aviation Weather Center", "aviationweather.gov", "aviation_forecasts", "official_forecast", "global", 600, "U.S. public domain", "https://aviationweather.gov/api/data/taf?format=json&time=valid", "json_rows"),
    SourceSpec("SWPC Alerts", "NOAA Space Weather Prediction Center", "swpc.noaa.gov", "space_weather", "official_alert", "global", 60, "U.S. public domain", "https://services.swpc.noaa.gov/products/alerts.json", "json_rows"),
    SourceSpec("NHC Atlantic Outlook", "NOAA National Hurricane Center", "nhc.noaa.gov", "tropical_cyclones", "official_forecast", "Atlantic", 300, "U.S. public domain", "https://www.nhc.noaa.gov/xml/TWOAT.xml"),
    SourceSpec("NHC Eastern Pacific Outlook", "NOAA National Hurricane Center", "nhc.noaa.gov", "tropical_cyclones", "official_forecast", "Eastern Pacific", 300, "U.S. public domain", "https://www.nhc.noaa.gov/xml/TWOEP.xml"),
    SourceSpec("NOAA Tsunami Alerts", "NOAA U.S. Tsunami Warning System", "tsunami.gov", "tsunami_alerts", "official_alert", "Pacific and United States", 60, "U.S. public domain", "https://www.tsunami.gov/events/xml/PAAQAtom.xml"),
    SourceSpec("FEMA Disaster Declarations", "Federal Emergency Management Agency", "fema.gov", "emergency_management", "official_record", "United States", 1800, "U.S. public domain", "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?$top=100&$orderby=declarationDate%20desc", "fema"),
    SourceSpec("NVD Recent Vulnerabilities", "NIST National Vulnerability Database", "nist.gov", "cyber_vulnerabilities", "official_record", "global", 1800, "U.S. public domain", "https://services.nvd.nist.gov/rest/json/cves/2.0/?resultsPerPage=100", "nvd"),
    SourceSpec("GeoNet Earthquakes", "GeoNet New Zealand", "geonet.org.nz", "earthquakes", "direct_observation", "New Zealand", 60, "GeoNet data policy", "https://api.geonet.org.nz/quake?MMI=3", "geonet"),
    SourceSpec("WHO Disease Outbreak News", "World Health Organization", "who.int", "public_health", "official_report", "global", 1800, "WHO terms", "https://www.who.int/feeds/entity/don/en/rss.xml"),
    SourceSpec("ECDC News", "European Centre for Disease Prevention and Control", "ecdc.europa.eu", "public_health", "official_report", "Europe", 1800, "EU reuse policy", "https://www.ecdc.europa.eu/en/news-events/rss"),
    SourceSpec("ENISA Cybersecurity News", "European Union Agency for Cybersecurity", "enisa.europa.eu", "cyber_security", "official_report", "European Union", 1800, "EU reuse policy", "https://www.enisa.europa.eu/news/enisa-news/RSS"),
    SourceSpec("UN News Global", "United Nations News", "un.org", "geopolitical_reporting", "official_report", "global", 600, "UN terms", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    SourceSpec("UN News Peace and Security", "United Nations News", "un.org", "conflict_security", "official_report", "global", 600, "UN terms", "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml"),
    SourceSpec("UN News Humanitarian", "United Nations News", "un.org", "humanitarian_reports", "official_report", "global", 600, "UN terms", "https://news.un.org/feed/subscribe/en/news/topic/humanitarian-aid/feed/rss.xml"),
    SourceSpec("IAEA News", "International Atomic Energy Agency", "iaea.org", "nuclear_security", "official_report", "global", 1800, "IAEA terms", "https://www.iaea.org/newscenter/news/rss"),
    SourceSpec("EIA Today in Energy", "U.S. Energy Information Administration", "eia.gov", "energy", "official_report", "global", 3600, "U.S. public domain", "https://www.eia.gov/rss/todayinenergy.xml"),
    SourceSpec("ECB Press Releases", "European Central Bank", "ecb.europa.eu", "macroeconomics", "official_release", "Euro area", 1800, "ECB reuse policy", "https://www.ecb.europa.eu/rss/press.html"),
    SourceSpec("IMF News", "International Monetary Fund", "imf.org", "macroeconomics", "official_release", "global", 3600, "IMF terms", "https://www.imf.org/external/rss/feeds.aspx?category=all"),
    SourceSpec("NATO News", "North Atlantic Treaty Organization", "nato.int", "conflict_security", "official_release", "NATO area and global", 1800, "NATO terms", "https://www.nato.int/cps/en/natohq/rss/news.rss"),
    SourceSpec("U.S. Travel Advisories", "U.S. Department of State", "state.gov", "travel_security", "official_advisory", "global", 1800, "U.S. public domain", "https://travel.state.gov/_res/rss/TAsTWs.xml"),
    SourceSpec("Met Office UK Warnings", "UK Met Office", "metoffice.gov.uk", "weather_alerts", "official_alert", "United Kingdom", 300, "Open Government Licence", "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK"),
]


class SourceRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, list[app.Evidence]]] = {}
        self.failures: dict[str, int] = {}
        self.backoff_until: dict[str, float] = {}
        self.details: dict[str, dict[str, Any]] = {}

    def fetch(self, spec: SourceSpec) -> list[app.Evidence]:
        stamp = time.monotonic()
        with self.lock:
            cached = self.cache.get(spec.name)
            if cached and stamp - cached[0] < spec.refresh_seconds:
                self.details[spec.name] = {"cache_hit": True, "attempts": 0}
                return copy.deepcopy(cached[1])
            if self.backoff_until.get(spec.name, 0) > stamp:
                if cached:
                    self.details[spec.name] = {"cache_hit": True, "stale_cache": True, "attempts": 0}
                    return copy.deepcopy(cached[1])
                raise RuntimeError(f"source backoff active: {spec.name}")
        attempts = max(1, int(os.getenv("AURORA_SOURCE_RETRIES", "2")))
        error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                records = fetch_source(spec)
                with self.lock:
                    self.cache[spec.name] = (time.monotonic(), records)
                    self.failures.pop(spec.name, None)
                    self.backoff_until.pop(spec.name, None)
                    self.details[spec.name] = {"cache_hit": False, "attempts": attempt}
                return copy.deepcopy(records)
            except Exception as exc:
                error = exc
                if attempt < attempts:
                    time.sleep(min(2.0, 0.25 * 2 ** (attempt - 1)))
        with self.lock:
            failures = self.failures.get(spec.name, 0) + 1
            self.failures[spec.name] = failures
            delay = min(3600, 15 * 2 ** min(failures - 1, 6))
            self.backoff_until[spec.name] = time.monotonic() + delay
            self.details[spec.name] = {"cache_hit": False, "attempts": attempts, "backoff_seconds": delay, "error": str(error)[:240]}
        raise error or RuntimeError(f"source failed: {spec.name}")

    def diagnostic(self, name: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.details.get(name) or {})


RUNTIME = SourceRuntime()


def _request(url: str, accept: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": app.USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=app.TIMEOUT) as response:
        return response.read()


def _json(url: str) -> Any:
    return json.loads(_request(url, "application/json, application/geo+json").decode("utf-8", "replace"))


def _text(url: str) -> str:
    return _request(url, "application/rss+xml, application/atom+xml, application/xml, text/xml").decode("utf-8", "replace")


def _date(value: Any) -> str:
    try:
        return parsedate_to_datetime(str(value)).isoformat()
    except (TypeError, ValueError, OverflowError):
        return app.parse_date(value).isoformat()


def _local(tag: str) -> str:
    return tag.split("}")[-1].lower()


def _value(node: ET.Element, names: set[str]) -> str:
    for child in node.iter():
        if _local(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _link(node: ET.Element) -> str:
    for child in node.iter():
        if _local(child.tag) == "link":
            return (child.attrib.get("href") or child.text or "").strip()
    return ""


def parse_feed(spec: SourceSpec, text: str) -> list[app.Evidence]:
    root = ET.fromstring(text)
    output = []
    for node in [n for n in root.iter() if _local(n.tag) in {"item", "entry"}][:spec.limit]:
        title = app.clean(_value(node, {"title"}), 300)
        if not title:
            continue
        link = app.clean(_link(node), 700) or spec.url
        published = _value(node, {"pubdate", "published", "updated", "date"})
        summary = app.clean(_value(node, {"description", "summary", "content"}), 500)
        record = app.Evidence(app.stable_id(spec.name, link, title, published), title, link, spec.name, spec.family, 1, _date(published), spec.observation_type, True, summary, raw_source=spec.provider)
        setattr(record, "source_origin", f"{spec.provider}:{link}")
        output.append(record)
    return output


def parse_json_rows(spec: SourceSpec, data: Any) -> list[app.Evidence]:
    rows = data if isinstance(data, list) else list((data or {}).get("data") or [])
    output = []
    for item in rows[:spec.limit]:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or item.get("icaoId") or item.get("product_id") or item.get("seriesId") or "")
        raw = app.clean(item.get("rawOb") or item.get("rawTAF") or item.get("rawSigmet") or item.get("message") or item.get("text") or json.dumps(item, sort_keys=True), 500)
        title = app.clean(f"{spec.name}: {identifier} {raw[:180]}", 300)
        published = item.get("issue_datetime") or item.get("issueTime") or item.get("obsTime") or item.get("time_tag")
        record = app.Evidence(app.stable_id(spec.name, identifier, published, raw), title, spec.url, spec.name, spec.family, 1, _date(published), spec.observation_type, True, raw, raw_source=spec.provider)
        setattr(record, "source_origin", f"{spec.provider}:{identifier or app.stable_id(raw)}")
        output.append(record)
    return output


def parse_nws(spec: SourceSpec, data: dict[str, Any]) -> list[app.Evidence]:
    output = []
    for feature in (data.get("features") or [])[:spec.limit]:
        props = feature.get("properties") or {}
        title = app.clean(props.get("headline") or props.get("event") or props.get("description"), 300)
        if not title:
            continue
        url = props.get("@id") or props.get("id") or spec.url
        summary = app.clean(props.get("description") or props.get("instruction") or props.get("areaDesc"), 500)
        record = app.Evidence(app.stable_id(spec.name, url, title), title, url, spec.name, spec.family, 1, _date(props.get("sent") or props.get("effective")), spec.observation_type, True, summary, raw_source=spec.provider)
        setattr(record, "source_origin", str(props.get("sender") or url))
        output.append(record)
    return output


def parse_fema(spec: SourceSpec, data: dict[str, Any]) -> list[app.Evidence]:
    output = []
    for item in (data.get("DisasterDeclarationsSummaries") or [])[:spec.limit]:
        number = str(item.get("disasterNumber") or "")
        title = app.clean(f"FEMA {item.get('declarationType', 'disaster')} declaration: {item.get('declarationTitle', '')} - {item.get('state', '')}", 300)
        url = f"https://www.fema.gov/disaster/{number}" if number else spec.url
        record = app.Evidence(app.stable_id(spec.name, number, item.get("declarationDate")), title, url, spec.name, spec.family, 1, _date(item.get("declarationDate")), spec.observation_type, True, app.clean(str(item.get("incidentType") or ""), 300), raw_source=spec.provider)
        setattr(record, "source_origin", f"FEMA:{number}")
        output.append(record)
    return output


def parse_nvd(spec: SourceSpec, data: dict[str, Any]) -> list[app.Evidence]:
    output = []
    for wrapper in (data.get("vulnerabilities") or [])[:spec.limit]:
        cve = (wrapper or {}).get("cve") or {}
        identifier = str(cve.get("id") or "")
        description = next((str(v.get("value")) for v in cve.get("descriptions") or [] if v.get("lang") == "en"), "")
        if not identifier:
            continue
        record = app.Evidence(app.stable_id(spec.name, identifier), app.clean(f"NVD {identifier}: {description}", 300), f"https://nvd.nist.gov/vuln/detail/{identifier}", spec.name, spec.family, 1, _date(cve.get("published")), spec.observation_type, True, app.clean(description, 500), raw_source=spec.provider)
        setattr(record, "source_origin", f"NVD:{identifier}")
        output.append(record)
    return output


def parse_geonet(spec: SourceSpec, data: dict[str, Any]) -> list[app.Evidence]:
    output = []
    for feature in (data.get("features") or [])[:spec.limit]:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        identifier = str(feature.get("id") or props.get("publicID") or "")
        title = app.clean(f"GeoNet M{props.get('magnitude')} earthquake - {props.get('locality') or 'New Zealand'}", 300)
        record = app.Evidence(app.stable_id(spec.name, identifier, props.get("time")), title, f"https://www.geonet.org.nz/earthquake/{identifier}" if identifier else spec.url, spec.name, spec.family, 1, _date(props.get("time")), spec.observation_type, True, app.clean(f"Depth {props.get('depth')} km; MMI {props.get('mmi')}", 300), coords[1] if len(coords) > 1 else None, coords[0] if coords else None, spec.provider)
        setattr(record, "source_origin", f"GeoNet:{identifier}")
        output.append(record)
    return output


def fetch_source(spec: SourceSpec) -> list[app.Evidence]:
    if spec.parser == "feed":
        return parse_feed(spec, _text(spec.url))
    data = _json(spec.url)
    return {"nws": parse_nws, "json_rows": parse_json_rows, "fema": parse_fema, "nvd": parse_nvd, "geonet": parse_geonet}[spec.parser](spec, data)


def phase9_adapters(query: str) -> list[Adapter]:
    output = list(base_adapters(query))
    output.extend(Adapter(spec.name, spec.family, 1, True, spec.capability, lambda spec=spec: RUNTIME.fetch(spec)) for spec in SOURCES)
    return output


def registry_manifest(query: str = app.DEFAULT_QUERY) -> list[dict[str, Any]]:
    specs = {spec.name: spec for spec in SOURCES}
    output = []
    for adapter in phase9_adapters(query):
        if adapter.name in specs:
            item = asdict(specs[adapter.name])
            item.pop("parser", None)
            item["tier"] = 1
            item["official"] = True
        else:
            item = {"name": adapter.name, "provider": adapter.name, "family": adapter.family, "tier": adapter.tier, "official": adapter.official, "capability": adapter.capability, "observation_type": "existing_adapter", "coverage": "global", "refresh_seconds": 300, "license": "provider terms", "url": "built-in", "limit": 100}
        item["runtime"] = RUNTIME.diagnostic(adapter.name)
        output.append(item)
    return output
