from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import app
import phase9_sources as base
from release_engine import Adapter


@dataclass(frozen=True)
class RepairedSource:
    name: str
    provider: str
    family: str
    capability: str
    observation_type: str
    coverage: str
    refresh_seconds: int
    license: str
    url: str
    parser: str
    link_prefixes: tuple[str, ...] = ()
    fallback_url: str = ""
    fallback_parser: str = ""
    limit: int = 100


AWC_HUBS = "KJFK,KLAX,KORD,KATL,CYYZ,EGLL,EHAM,EDDF,LFPG,LEMD,OMDB,OTHH,WSSS,VHHH,RJTT,VIDP,OPIS,FAOR,SBGR,SCEL,YSSY,NZAA"

REPAIRED_SOURCES = [
    RepairedSource("AWC METAR", "NOAA Aviation Weather Center", "aviationweather.gov", "aviation_observations", "direct_observation", "major global aviation hubs", 60, "U.S. public domain", f"https://aviationweather.gov/api/data/metar?ids={AWC_HUBS}&format=json&hours=2", "json_rows"),
    RepairedSource("AWC TAF", "NOAA Aviation Weather Center", "aviationweather.gov", "aviation_forecasts", "official_forecast", "major global aviation hubs", 600, "U.S. public domain", f"https://aviationweather.gov/api/data/taf?ids={AWC_HUBS}&format=json", "json_rows"),
    RepairedSource("ECDC News", "European Centre for Disease Prevention and Control", "ecdc.europa.eu", "public_health", "official_report", "Europe", 1800, "EU reuse policy", "https://www.ecdc.europa.eu/en/taxonomy/term/1307/feed", "feed", ("/en/news-events/",), "https://www.ecdc.europa.eu/en/news-events", "html"),
    RepairedSource("ENISA Cybersecurity News", "European Union Agency for Cybersecurity", "enisa.europa.eu", "cyber_security", "official_report", "European Union", 1800, "EU reuse policy", "https://www.enisa.europa.eu/news", "html", ("/news/",)),
    RepairedSource("UN News Global", "United Nations News", "un.org", "geopolitical_reporting", "official_report", "global", 600, "UN terms", "https://news.un.org/feed/subscribe/en/news/topic/un-affairs/feed/rss.xml", "feed", (), "https://news.un.org/feed/subscribe/en/news/topic/climate-change/feed/rss.xml", "feed"),
    RepairedSource("IAEA News", "International Atomic Energy Agency", "iaea.org", "nuclear_security", "official_report", "global", 1800, "IAEA terms", "https://www.iaea.org/news", "html", ("/newscenter/news/", "/newscenter/pressreleases/", "/newscenter/statements/")),
    RepairedSource("NATO News", "North Atlantic Treaty Organization", "nato.int", "conflict_security", "official_release", "NATO area and global", 1800, "NATO terms", "https://www.nato.int/en/news-and-events/articles/news", "html", ("/en/news-and-events/articles/news/", "/cps/en/natohq/news_")),
    RepairedSource("ReliefWeb", "ReliefWeb", "reliefweb.int", "humanitarian_reports", "official_report", "global", 600, "ReliefWeb and source-provider terms", "https://reliefweb.int/updates", "html", ("/report/",)),
    RepairedSource("WHO Disease Outbreak News", "World Health Organization", "who.int", "public_health", "official_report", "global", 1800, "WHO terms", "https://www.who.int/emergencies/disease-outbreak-news", "html", ("/emergencies/disease-outbreak-news/item/",), "https://www.who.int/", "html"),
]

REPAIRED_SOURCE_NAMES = frozenset(source.name for source in REPAIRED_SOURCES)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "a" or self.current_href is not None:
            return
        values = dict(attrs)
        href = str(values.get("href") or "").strip()
        if href:
            self.current_href = href
            self.current_text = []

    def handle_data(self, data: str):
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self.current_href is not None:
            self.links.append((self.current_href, app.clean(" ".join(self.current_text), 300)))
            self.current_href = None
            self.current_text = []


class RepairRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, list[app.Evidence]]] = {}
        self.failures: dict[str, int] = {}
        self.backoff_until: dict[str, float] = {}
        self.details: dict[str, dict[str, Any]] = {}

    def fetch(self, spec: RepairedSource) -> list[app.Evidence]:
        stamp = time.monotonic()
        with self.lock:
            cached = self.cache.get(spec.name)
            if cached and stamp - cached[0] < spec.refresh_seconds:
                self.details[spec.name] = {"cache_hit": True, "attempts": 0, "repair": True}
                return copy.deepcopy(cached[1])
            if self.backoff_until.get(spec.name, 0) > stamp:
                if cached:
                    self.details[spec.name] = {"cache_hit": True, "stale_cache": True, "attempts": 0, "repair": True}
                    return copy.deepcopy(cached[1])
                raise RuntimeError(f"source backoff active: {spec.name}")
        attempts = max(1, int(os.getenv("AURORA_SOURCE_RETRIES", "2")))
        error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                records, endpoint = fetch_repaired_source(spec)
                with self.lock:
                    self.cache[spec.name] = (time.monotonic(), records)
                    self.failures.pop(spec.name, None)
                    self.backoff_until.pop(spec.name, None)
                    self.details[spec.name] = {"cache_hit": False, "attempts": attempt, "repair": True, "endpoint": endpoint}
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
            self.details[spec.name] = {"cache_hit": False, "attempts": attempts, "backoff_seconds": delay, "repair": True, "error": str(error)[:240]}
        raise error or RuntimeError(f"source failed: {spec.name}")

    def diagnostic(self, name: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.details.get(name) or {})


RUNTIME = RepairRuntime()


def _request(url: str, accept: str) -> bytes:
    user_agent = os.getenv("AURORA_USER_AGENT", "Mozilla/5.0 (compatible; AURORA-LIVE/1.0; +mailto:hr185882@gmail.com)")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept, "Accept-Language": "en-US,en;q=0.8"})
    with urllib.request.urlopen(request, timeout=app.TIMEOUT) as response:
        return response.read()


def _date_from_context(context: str) -> str:
    clean = app.clean(context, 1400)
    patterns = (
        (r"\b\d{1,2} [A-Z][a-z]+,? \d{4}\b", ("%d %B %Y", "%d %B, %Y")),
        (r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b", ("%B %d, %Y",)),
        (r"\b\d{4}-\d{2}-\d{2}\b", ("%Y-%m-%d",)),
    )
    for pattern, formats in patterns:
        match = re.search(pattern, clean)
        if not match:
            continue
        for fmt in formats:
            try:
                return datetime.strptime(match.group(0), fmt).replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
    return app.now_iso()


def parse_html(spec: RepairedSource, text: str) -> list[app.Evidence]:
    parser = _LinkParser()
    parser.feed(text)
    output: list[app.Evidence] = []
    seen: set[str] = set()
    for href, title in parser.links:
        if len(title) < 12:
            continue
        url = urllib.parse.urljoin(spec.url, href)
        path = urllib.parse.urlsplit(url).path
        prefixes = spec.link_prefixes
        if prefixes and not any(prefix in path for prefix in prefixes):
            continue
        canonical = url.split("#", 1)[0]
        if canonical in seen:
            continue
        seen.add(canonical)
        position = text.find(href)
        context = text[max(0, position - 500):position + 900] if position >= 0 else title
        published = _date_from_context(context)
        summary = app.clean(re.sub(r"<[^>]+>", " ", context), 500)
        record = app.Evidence(app.stable_id(spec.name, canonical, title, published), title, canonical, spec.name, spec.family, 1, published, spec.observation_type, True, summary, raw_source=spec.provider)
        setattr(record, "source_origin", f"{spec.provider}:{canonical}")
        output.append(record)
        if len(output) >= spec.limit:
            break
    if not output:
        raise ValueError(f"no qualifying links found for {spec.name}")
    return output


def _parse(spec: RepairedSource, parser: str, url: str) -> list[app.Evidence]:
    if parser == "json_rows":
        raw = _request(url, "application/json")
        data = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else []
        return base.parse_json_rows(spec, data)
    if parser == "feed":
        text = _request(url, "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.1").decode("utf-8", "replace")
        return base.parse_feed(spec, text)
    if parser == "html":
        text = _request(url, "text/html, application/xhtml+xml").decode("utf-8", "replace")
        return parse_html(spec, text)
    raise ValueError(f"unsupported repaired parser: {parser}")


def fetch_repaired_source(spec: RepairedSource) -> tuple[list[app.Evidence], str]:
    try:
        return _parse(spec, spec.parser, spec.url), spec.url
    except Exception:
        if not spec.fallback_url:
            raise
        parser = spec.fallback_parser or spec.parser
        return _parse(spec, parser, spec.fallback_url), spec.fallback_url


def repaired_phase9_adapters(query: str) -> list[Adapter]:
    output = [adapter for adapter in base.phase9_adapters(query) if adapter.name not in REPAIRED_SOURCE_NAMES]
    output.extend(Adapter(spec.name, spec.family, 1, True, spec.capability, lambda spec=spec: RUNTIME.fetch(spec)) for spec in REPAIRED_SOURCES)
    return output


def repaired_registry_manifest(query: str = app.DEFAULT_QUERY) -> list[dict[str, Any]]:
    output = [item for item in base.registry_manifest(query) if item.get("name") not in REPAIRED_SOURCE_NAMES]
    for spec in REPAIRED_SOURCES:
        item = asdict(spec)
        item.pop("parser", None)
        item.pop("link_prefixes", None)
        item.pop("fallback_parser", None)
        item["tier"] = 1
        item["official"] = True
        item["repair"] = True
        item["runtime"] = RUNTIME.diagnostic(spec.name)
        output.append(item)
    return output
