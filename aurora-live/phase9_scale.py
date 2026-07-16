from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import app
from phase9_final_repairs import final_phase9_adapters, final_registry_manifest
from release_engine import Adapter


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    domain: str
    capability: str
    coverage: str
    url: str
    tier: int = 1
    official: bool = True
    refresh_seconds: int = 1800
    license: str = "provider terms"


_PROVIDER_ROWS = """
UK FCDO|gov.uk|diplomacy|global|https://www.gov.uk/government/organisations/foreign-commonwealth-development-office
European Commission|ec.europa.eu|governance|European Union|https://ec.europa.eu/commission/presscorner/home/en
European External Action Service|eeas.europa.eu|diplomacy|global|https://www.eeas.europa.eu/eeas/press-material_en
European Council|consilium.europa.eu|governance|European Union|https://www.consilium.europa.eu/en/press/press-releases/
Europol|europol.europa.eu|law_enforcement|Europe|https://www.europol.europa.eu/media-press/newsroom
Frontex|frontex.europa.eu|border_security|Europe|https://www.frontex.europa.eu/media-centre/news/news-release/
International Organization for Migration|iom.int|migration|global|https://www.iom.int/news
UNHCR|unhcr.org|migration|global|https://www.unhcr.org/news
UN OCHA|unocha.org|humanitarian_reports|global|https://www.unocha.org/news
UNICEF|unicef.org|humanitarian_reports|global|https://www.unicef.org/press-releases
World Food Programme|wfp.org|food_security|global|https://www.wfp.org/news
Food and Agriculture Organization|fao.org|food_security|global|https://www.fao.org/newsroom/news/en
World Meteorological Organization|wmo.int|climate|global|https://wmo.int/news
World Trade Organization|wto.org|trade|global|https://www.wto.org/english/news_e/news_e.htm
OECD|oecd.org|macroeconomics|global|https://www.oecd.org/en/about/news.html
World Bank|worldbank.org|development|global|https://www.worldbank.org/en/news/all
Bank for International Settlements|bis.org|finance|global|https://www.bis.org/press/index.htm
Federal Reserve Board|federalreserve.gov|finance|United States|https://www.federalreserve.gov/newsevents.htm
U.S. Treasury|treasury.gov|finance|United States and global|https://home.treasury.gov/news/press-releases
Bank of England|bankofengland.co.uk|finance|United Kingdom|https://www.bankofengland.co.uk/news
Bank of Japan|boj.or.jp|finance|Japan|https://www.boj.or.jp/en/announcements/
Reserve Bank of Australia|rba.gov.au|finance|Australia|https://www.rba.gov.au/media-releases/
Bank of Canada|bankofcanada.ca|finance|Canada|https://www.bankofcanada.ca/press/press-releases/
African Development Bank|afdb.org|development|Africa|https://www.afdb.org/en/news-and-events/press-releases
Asian Development Bank|adb.org|development|Asia-Pacific|https://www.adb.org/news/releases
Inter-American Development Bank|iadb.org|development|Americas|https://www.iadb.org/en/news
International Energy Agency|iea.org|energy|global|https://www.iea.org/news
OPEC|opec.org|energy|global|https://www.opec.org/opec_web/en/press_room/28.htm
International Renewable Energy Agency|irena.org|energy|global|https://www.irena.org/News/pressreleases
U.S. Department of Defense|defense.gov|conflict_security|global|https://www.defense.gov/News/Releases/
U.S. Central Command|centcom.mil|conflict_security|Middle East and Central Asia|https://www.centcom.mil/MEDIA/PRESS-RELEASES/
EUROCONTROL|eurocontrol.int|aviation|Europe|https://www.eurocontrol.int/news
Federal Aviation Administration|faa.gov|aviation|United States|https://www.faa.gov/newsroom
European Maritime Safety Agency|emsa.europa.eu|maritime|Europe|https://www.emsa.europa.eu/newsroom/latest-news.html
International Maritime Organization|imo.org|maritime|global|https://www.imo.org/en/MediaCentre/PressBriefings/Pages/Default.aspx
International Hydrographic Organization|iho.int|maritime|global|https://iho.int/en/news
International Telecommunication Union|itu.int|connectivity|global|https://www.itu.int/hub/news/
ICANN|icann.org|connectivity|global|https://www.icann.org/news/announcements
CERT-EU|cert.europa.eu|cyber_security|European Union|https://cert.europa.eu/publications/security-advisories/
UK National Cyber Security Centre|ncsc.gov.uk|cyber_security|United Kingdom|https://www.ncsc.gov.uk/section/keep-up-to-date/news
Australian Cyber Security Centre|cyber.gov.au|cyber_security|Australia|https://www.cyber.gov.au/about-us/view-all-content/alerts-and-advisories
JPCERT Coordination Center|jpcert.or.jp|cyber_security|Japan|https://www.jpcert.or.jp/english/at/
New Zealand NCSC|ncsc.govt.nz|cyber_security|New Zealand|https://www.ncsc.govt.nz/news/
Cyber Security Agency of Singapore|csa.gov.sg|cyber_security|Singapore|https://www.csa.gov.sg/alerts-advisories
Canadian Centre for Cyber Security|cyber.gc.ca|cyber_security|Canada|https://www.cyber.gc.ca/en/alerts-advisories
U.S. CDC|cdc.gov|public_health|United States and global|https://www.cdc.gov/media/releases/index.html
Africa CDC|africacdc.org|public_health|Africa|https://africacdc.org/news-item/
Pan American Health Organization|paho.org|public_health|Americas|https://www.paho.org/en/news
Copernicus Emergency Management Service|copernicus.eu|disaster_alerts|global|https://emergency.copernicus.eu/news
ECMWF|ecmwf.int|weather_forecasts|global|https://www.ecmwf.int/en/about/media-centre/news
Japan Meteorological Agency|jma.go.jp|weather_alerts|Japan and northwest Pacific|https://www.jma.go.jp/jma/en/News/indexe_news.html
Australian Bureau of Meteorology|bom.gov.au|weather_alerts|Australia|https://www.bom.gov.au/announcements/
Environment and Climate Change Canada|canada.ca|climate|Canada|https://www.canada.ca/en/environment-climate-change/news.html
India Meteorological Department|imd.gov.in|weather_alerts|India|https://mausam.imd.gov.in/
Pakistan Meteorological Department|pmd.gov.pk|weather_alerts|Pakistan|https://www.pmd.gov.pk/en/
UNCTAD|unctad.org|trade|global|https://unctad.org/news
UNDP|undp.org|development|global|https://www.undp.org/news-centre
UNEP|unep.org|climate|global|https://www.unep.org/news-and-stories
UNESCO|unesco.org|governance|global|https://www.unesco.org/en/articles
UNODC|unodc.org|law_enforcement|global|https://www.unodc.org/unodc/en/press/releases.html
International Labour Organization|ilo.org|labour|global|https://www.ilo.org/resource/news
World Intellectual Property Organization|wipo.int|governance|global|https://www.wipo.int/pressroom/en/
International Criminal Court|icc-cpi.int|law_enforcement|global|https://www.icc-cpi.int/news
International Court of Justice|icj-cij.org|governance|global|https://www.icj-cij.org/press-releases
INTERPOL|interpol.int|law_enforcement|global|https://www.interpol.int/News-and-Events/News
Organization for Security and Co-operation in Europe|osce.org|conflict_security|Europe and Central Asia|https://www.osce.org/press-releases
African Union|au.int|governance|Africa|https://au.int/en/pressreleases
Association of Southeast Asian Nations|asean.org|governance|Southeast Asia|https://asean.org/category/news/
Organization of American States|oas.org|governance|Americas|https://www.oas.org/en/media_center/press_releases.asp
Arctic Council|arctic-council.org|climate|Arctic|https://arctic-council.org/news/
"""

PROVIDERS = [ProviderSpec(*row.split("|", 4)) for row in _PROVIDER_ROWS.strip().splitlines()]
VARIANTS = ("breaking", "alerts", "releases", "observations", "regional", "strategic", "risk", "outlook")
HEALTH_PATH = Path(os.getenv("AURORA_PHASE9_HEALTH_PATH", "/data/phase9-provider-health.json"))
_KEYWORDS = re.compile(r"news|press|release|alert|advis|statement|report|update|publication|brief|warning", re.I)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.href = None; self.text = []; self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a" and self.href is None:
            href = str(dict(attrs).get("href") or "").strip()
            if href: self.href, self.text = href, []
    def handle_data(self, data):
        if self.href is not None: self.text.append(data)
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, app.clean(" ".join(self.text), 300))); self.href, self.text = None, []


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def curated_streams() -> list[dict[str, Any]]:
    return [{"id": f"{_slug(p.name)}:{v}", "provider": p.name, "capability": p.capability, "layer": f"{p.capability}:{v}", "selector": v, "endpoint": p.url, "coverage": p.coverage, "refresh_seconds": p.refresh_seconds, "license": p.license, "official": p.official} for p in PROVIDERS for v in VARIANTS]


def catalog_summary() -> dict[str, Any]:
    base = final_registry_manifest(); providers = {str(item.get("provider") or item.get("name")) for item in base}; capabilities = {str(item.get("capability")) for item in base if item.get("capability")}; providers.update(p.name for p in PROVIDERS); capabilities.update(p.capability for p in PROVIDERS); streams = curated_streams()
    return {"providers": len(providers), "official_providers": sum(1 for p in PROVIDERS if p.official), "curated_streams": len(streams) + sum(int(item.get("stream_count") or 1) for item in base), "layers": len({s["layer"] for s in streams}) + len(capabilities), "capability_classes": len(capabilities)}


def _read_health() -> dict[str, Any]:
    try:
        value = json.loads(HEALTH_PATH.read_text(encoding="utf-8")); return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError): return {}


def _write_health(value: dict[str, Any]) -> None:
    try:
        HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True); temporary = HEALTH_PATH.with_name(f"{HEALTH_PATH.name}.{uuid.uuid4().hex}.tmp"); temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"); os.replace(temporary, HEALTH_PATH)
    except OSError: pass


class ScaleRuntime:
    def __init__(self): self.lock = threading.Lock(); self.cache = {}
    def fetch(self, provider: ProviderSpec) -> list[app.Evidence]:
        stamp = time.monotonic()
        with self.lock:
            cached = self.cache.get(provider.name)
            if cached and stamp - cached[0] < provider.refresh_seconds: return copy.deepcopy(cached[1])
        started = time.monotonic()
        try:
            request = urllib.request.Request(provider.url, headers={"User-Agent": os.getenv("AURORA_USER_AGENT", "AURORA-LIVE/1.0 (+mailto:hr185882@gmail.com)"), "Accept": "text/html, application/xhtml+xml, */*;q=0.2"})
            with urllib.request.urlopen(request, timeout=min(20, max(3, int(os.getenv("AURORA_SCALE_TIMEOUT", "12"))))) as response: endpoint, text = response.geturl(), response.read(1_500_000).decode("utf-8", "replace")
            parser = _Links(); parser.feed(text); records = []; seen = set()
            for href, title in parser.links:
                url = urllib.parse.urljoin(endpoint, href).split("#", 1)[0]
                if len(title) < 12 or url in seen or not (_KEYWORDS.search(title) or _KEYWORDS.search(urllib.parse.urlsplit(url).path)): continue
                seen.add(url); record = app.Evidence(app.stable_id(provider.name, url, title), title, url, provider.name, provider.domain, provider.tier, app.now_iso(), "official_update", provider.official, f"Update from {provider.name}.", raw_source=provider.name); setattr(record, "source_origin", f"{provider.name}:{url}"); records.append(record)
                if len(records) >= 25: break
            if not records: raise ValueError(f"no qualifying update links found for {provider.name}")
            with self.lock: self.cache[provider.name] = (time.monotonic(), records)
            health = _read_health(); health[provider.name] = {"status": "online", "checked_at": app.now_iso(), "latency_ms": round((time.monotonic() - started) * 1000), "endpoint": endpoint, "records": len(records)}; _write_health(health); return copy.deepcopy(records)
        except Exception as exc:
            health = _read_health(); health[provider.name] = {"status": "degraded", "checked_at": app.now_iso(), "latency_ms": round((time.monotonic() - started) * 1000), "endpoint": provider.url, "error": app.clean(exc, 240), "records": 0}; _write_health(health); raise


RUNTIME = ScaleRuntime()


def scheduled_providers(batch_size: int | None = None, slot: int | None = None) -> list[ProviderSpec]:
    size = min(len(PROVIDERS), max(1, int(batch_size or os.getenv("AURORA_SCALE_BATCH", "12")))); window = int(slot if slot is not None else time.time() // 300); start = (window * size) % len(PROVIDERS); return [PROVIDERS[(start + i) % len(PROVIDERS)] for i in range(size)]


def scaled_phase9_adapters(query: str) -> list[Adapter]:
    output = list(final_phase9_adapters(query)); output.extend(Adapter(p.name, p.domain, p.tier, p.official, p.capability, lambda p=p: RUNTIME.fetch(p)) for p in scheduled_providers()); return output


def scaled_registry_manifest(query: str = app.DEFAULT_QUERY) -> list[dict[str, Any]]:
    output = list(final_registry_manifest(query)); existing = {str(item.get("provider") or item.get("name")) for item in output}; health = _read_health()
    for p in PROVIDERS:
        if p.name in existing: continue
        item = asdict(p); item.update({"family": p.domain, "observation_type": "official_update", "stream_count": len(VARIANTS), "scheduled": True, "runtime": health.get(p.name, {})}); output.append(item)
    return output


def probe_provider(provider: ProviderSpec) -> dict[str, Any]:
    started = time.monotonic()
    try:
        request = urllib.request.Request(provider.url, headers={"User-Agent": os.getenv("AURORA_USER_AGENT", "AURORA-LIVE/1.0 (+mailto:hr185882@gmail.com)"), "Accept": "text/html, */*;q=0.1"})
        with urllib.request.urlopen(request, timeout=min(20, max(3, int(os.getenv("AURORA_SCALE_TIMEOUT", "12"))))) as response: sample = response.read(4096); return {"provider": provider.name, "status": "online" if sample else "degraded", "http_status": int(getattr(response, "status", 200) or 200), "latency_ms": round((time.monotonic() - started) * 1000), "url": response.geturl(), "bytes": len(sample)}
    except Exception as exc: return {"provider": provider.name, "status": "degraded", "http_status": 0, "latency_ms": round((time.monotonic() - started) * 1000), "url": provider.url, "bytes": 0, "error": app.clean(exc, 240)}


def probe_all(max_workers: int = 16) -> list[dict[str, Any]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(PROVIDERS)))) as pool: rows = list(pool.map(probe_provider, PROVIDERS))
    rows.sort(key=lambda row: row["provider"]); health = _read_health()
    for row in rows: health[row["provider"]] = {**dict(health.get(row["provider"]) or {}), **{k: v for k, v in row.items() if k != "provider"}, "checked_at": app.now_iso()}
    _write_health(health); return rows
