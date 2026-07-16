from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable

import app
import phase9_repairs as prior
from release_engine import Adapter


@dataclass(frozen=True)
class FinalSource:
    name: str
    provider: str
    family: str
    tier: int
    official: bool
    capability: str
    observation_type: str
    coverage: str
    refresh_seconds: int
    license: str
    url: str
    fetcher: Callable[[], list[app.Evidence]]


USER_AGENT = os.getenv("AURORA_USER_AGENT", "AURORA-LIVE/1.0 (+mailto:hr185882@gmail.com)")


def _feed_records(name: str, provider: str, family: str, capability: str, observation_type: str, url: str, official: bool = True, tier: int = 1, fallback: str = "") -> list[app.Evidence]:
    spec = prior.RepairedSource(name, provider, family, capability, observation_type, "global", 600, "provider terms", url, "feed", (), fallback, "feed")
    records, _ = prior.fetch_repaired_source(spec)
    for record in records:
        record.official = official
        record.source_tier = tier
    return records


def fetch_bbc_world() -> list[app.Evidence]:
    return _feed_records("BBC World News", "BBC News", "bbc.co.uk", "multilingual_news", "news_report", "https://feeds.bbci.co.uk/news/world/rss.xml", False, 2)


def fetch_iaea() -> list[app.Evidence]:
    endpoints = (
        "https://www.iaea.org/feeds/topnews",
        "https://www.iaea.org/newscenter/news/rss",
        "https://www.iaea.org/rss/news",
    )
    errors = []
    for endpoint in endpoints:
        try:
            return _feed_records("IAEA News", "International Atomic Energy Agency", "iaea.org", "nuclear_security", "official_report", endpoint)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("; ".join(errors)[:500])


def fetch_nato() -> list[app.Evidence]:
    spec = prior.RepairedSource(
        "NATO News",
        "North Atlantic Treaty Organization",
        "nato.int",
        "conflict_security",
        "official_release",
        "NATO area and global",
        1800,
        "NATO terms",
        "https://www.nato.int/en/news-and-events/articles/news",
        "html",
        ("/en/news-and-events/", "/cps/en/natohq/"),
        "https://www.nato.int/cps/en/natohq/news.htm",
        "html",
    )
    records, _ = prior.fetch_repaired_source(spec)
    return records


def fetch_reliefweb() -> list[app.Evidence]:
    url = "https://api.reliefweb.int/v1/reports?appname=hr185882%40gmail.com"
    body = json.dumps({
        "limit": 100,
        "profile": "list",
        "preset": "latest",
        "fields": {"include": ["title", "url_alias", "date", "source", "country", "body-html"]},
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=app.TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    output = []
    for item in (data.get("data") or [])[:100]:
        fields = item.get("fields") or {}
        title = app.clean(fields.get("title"), 300)
        if not title:
            continue
        identifier = str(item.get("id") or "")
        link = fields.get("url_alias") or (f"https://reliefweb.int/node/{identifier}" if identifier else url)
        date = (fields.get("date") or {}).get("created") or (fields.get("date") or {}).get("original")
        sources = ", ".join(source.get("name", "") for source in fields.get("source") or [])
        countries = ", ".join(country.get("name", "") for country in fields.get("country") or [])
        summary = app.clean(f"Sources: {sources}. Countries: {countries}. {fields.get('body-html') or ''}", 500)
        record = app.Evidence(app.stable_id("reliefweb-repaired", identifier, title), title, link, "ReliefWeb", "reliefweb.int", 1, app.parse_date(date).isoformat(), "official_report", True, summary, raw_source="ReliefWeb Reports API")
        setattr(record, "source_origin", f"ReliefWeb:{identifier or link}")
        output.append(record)
    if not output:
        raise ValueError("ReliefWeb API returned no reports")
    return output


def fetch_un_humanitarian() -> list[app.Evidence]:
    return _feed_records(
        "UN News Humanitarian",
        "United Nations News",
        "un.org",
        "humanitarian_reports",
        "official_report",
        "https://news.un.org/feed/subscribe/en/news/topic/humanitarian-aid/feed/rss.xml",
        True,
        1,
        "https://news.un.org/feed/subscribe/en/news/topic/refugees-and-migrants/feed/rss.xml",
    )


FINAL_SOURCES = [
    FinalSource("BBC World News", "BBC News", "bbc.co.uk", 2, False, "multilingual_news", "news_report", "global", 300, "BBC terms", "https://feeds.bbci.co.uk/news/world/rss.xml", fetch_bbc_world),
    FinalSource("IAEA News", "International Atomic Energy Agency", "iaea.org", 1, True, "nuclear_security", "official_report", "global", 1800, "IAEA terms", "https://www.iaea.org/feeds/topnews", fetch_iaea),
    FinalSource("NATO News", "North Atlantic Treaty Organization", "nato.int", 1, True, "conflict_security", "official_release", "NATO area and global", 1800, "NATO terms", "https://www.nato.int/en/news-and-events/articles/news", fetch_nato),
    FinalSource("ReliefWeb", "ReliefWeb", "reliefweb.int", 1, True, "humanitarian_reports", "official_report", "global", 600, "ReliefWeb and source-provider terms", "https://api.reliefweb.int/v1/reports", fetch_reliefweb),
    FinalSource("UN News Humanitarian", "United Nations News", "un.org", 1, True, "humanitarian_reports", "official_report", "global", 600, "UN terms", "https://news.un.org/feed/subscribe/en/news/topic/humanitarian-aid/feed/rss.xml", fetch_un_humanitarian),
]

REPLACED_NAMES = frozenset({"GDELT", "IAEA News", "NATO News", "ReliefWeb", "UN News Humanitarian"})
FINAL_REPAIR_NAMES = frozenset(source.name for source in FINAL_SOURCES)


def final_phase9_adapters(query: str) -> list[Adapter]:
    output = [adapter for adapter in prior.repaired_phase9_adapters(query) if adapter.name not in REPLACED_NAMES]
    output.extend(Adapter(source.name, source.family, source.tier, source.official, source.capability, source.fetcher) for source in FINAL_SOURCES)
    return output


def final_registry_manifest(query: str = app.DEFAULT_QUERY) -> list[dict[str, Any]]:
    output = [item for item in prior.repaired_registry_manifest(query) if item.get("name") not in REPLACED_NAMES]
    for source in FINAL_SOURCES:
        item = asdict(source)
        item.pop("fetcher", None)
        item["final_repair"] = True
        item["runtime"] = {}
        output.append(item)
    return output
