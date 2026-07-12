#!/usr/bin/env python3
"""AURORA LIVE: evidence-first global event browser.

Runs on Python's standard library. Public adapters feed normalized evidence
records into source-family clustering, K-ALIGN status, confidence grading and
AURORA action routing. Upstream failures fall back to labeled fixtures.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
FIXTURES = ROOT / "fixtures" / "events.json"
USER_AGENT = "AuroraLiveOSINT/0.1"
DEFAULT_QUERY = "conflict OR explosion OR earthquake OR flood OR wildfire OR protest OR coup OR outage OR cyberattack OR missile OR strike OR evacuation"
CACHE_TTL = 90
TIMEOUT = 12
STOPWORDS = {"the","and","for","from","with","after","amid","over","into","this","that","says","report","reports","live","new"}
CATEGORY_WORDS = {
    "conflict": {"war","conflict","strike","missile","drone","shelling","attack","military","explosion","ceasefire"},
    "civil_unrest": {"protest","riot","demonstration","unrest","coup","election","crackdown"},
    "disaster": {"earthquake","flood","wildfire","cyclone","hurricane","typhoon","volcano","tsunami","landslide","storm"},
    "cyber": {"cyber","ransomware","malware","breach","ddos","vulnerability"},
    "infrastructure": {"blackout","outage","pipeline","port","airport","rail","cable","telecom","shipping"},
    "health": {"outbreak","epidemic","pandemic","virus","disease"},
}
SOURCE_TIERS = {"usgs.gov":1,"nasa.gov":1,"gdacs.org":1,"cisa.gov":1,"un.org":1,"reliefweb.int":1,"reuters.com":2,"apnews.com":2,"bbc.com":2,"bbc.co.uk":2,"aljazeera.com":2,"afp.com":2}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        value = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value or "").strip()
    for fmt in ("%Y%m%dT%H%M%SZ","%Y%m%d%H%M%S","%a, %d %b %Y %H:%M:%S %z","%Y-%m-%dT%H:%M:%SZ","%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def clean(value: Any, limit: int = 600) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:18]


def host_family(value: str) -> str:
    raw = value.lower().strip()
    if "://" not in raw:
        raw = "https://" + raw
    host = urllib.parse.urlparse(raw).hostname or "unknown"
    host = host.removeprefix("www.").removeprefix("m.")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in {"co.uk","com.au","co.jp","com.br"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def source_tier(family: str, official: bool = False) -> int:
    if official:
        return 1
    return next((tier for domain, tier in SOURCE_TIERS.items() if family.endswith(domain)), 3)


def tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]{3,}", text.lower()) if x not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def haversine(a: tuple[float,float] | None, b: tuple[float,float] | None) -> float:
    if not a or not b:
        return math.inf
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742 * math.asin(math.sqrt(h))


def category(text: str) -> str:
    low = text.lower()
    scores = {k: sum(word in low for word in words) for k, words in CATEGORY_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "world"


def severity(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("mass casualty","tsunami warning","invasion","nuclear","catastrophic")): return "critical"
    if any(x in low for x in ("killed","dead","evacuation","missile","airstrike","explosion","major flood","wildfire","coup","blackout")): return "high"
    if any(x in low for x in ("protest","warning","outage","storm","clashes","disruption","advisory")): return "medium"
    return "low"


@dataclass
class Evidence:
    id: str
    title: str
    url: str
    source: str
    source_family: str
    source_tier: int
    published_at: str
    evidence_type: str
    official: bool = False
    snippet: str = ""
    latitude: float | None = None
    longitude: float | None = None
    raw_source: str = ""


@dataclass
class Claim:
    id: str
    title: str
    category: str
    severity: str
    published_at: str
    updated_at: str
    latitude: float | None
    longitude: float | None
    location_name: str
    evidence: list[Evidence] = field(default_factory=list)
    k_align_status: str = "NOT_PROVEN"
    confidence_grade: str = "G1"
    confidence_score: int = 25
    independent_origins: int = 1
    action_state: str = "MONITOR"
    what_changed: str = "Initial signal detected."
    why_it_matters: str = "Material impact is still being assessed."
    strongest_counterargument: str = "The signal may be duplicated, stale, incomplete, or mislocated."
    falsifier: str = "An authoritative correction or incompatible physical evidence would weaken this claim."
    score_components: dict[str,int] = field(default_factory=dict)


def fetch_json(url: str) -> dict[str,Any]:
    req = urllib.request.Request(url, headers={"User-Agent":USER_AGENT,"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent":USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", "replace")


def fetch_gdelt(query: str = DEFAULT_QUERY) -> list[Evidence]:
    params = urllib.parse.urlencode({"query":f"({query})","mode":"artlist","maxrecords":100,"timespan":"12h","sort":"datedesc","format":"json"})
    data = fetch_json("https://api.gdeltproject.org/api/v2/doc/doc?" + params)
    out = []
    for item in data.get("articles", []):
        title, url = clean(item.get("title"), 300), str(item.get("url") or "")
        if not title or not url: continue
        family = host_family(item.get("domain") or url)
        out.append(Evidence(stable_id("gdelt",url,title), title, url, family, family, source_tier(family), parse_date(item.get("seendate")).isoformat(), "news_report", raw_source="GDELT DOC 2.0"))
    return out


def fetch_usgs() -> list[Evidence]:
    data = fetch_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")
    out = []
    for feature in data.get("features", []):
        props, coords = feature.get("properties", {}), feature.get("geometry", {}).get("coordinates", [])
        title = clean(props.get("title"), 300)
        if not title: continue
        out.append(Evidence(stable_id("usgs",feature.get("id")), title, props.get("url") or "https://earthquake.usgs.gov/", "USGS", "usgs.gov", 1, parse_date(props.get("time")).isoformat(), "sensor_observation", True, f"Magnitude {props.get('mag')}; status {props.get('status')}", coords[1] if len(coords)>1 else None, coords[0] if coords else None, "USGS GeoJSON"))
    return out


def fetch_eonet() -> list[Evidence]:
    data = fetch_json("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=100")
    out = []
    for item in data.get("events", []):
        geometry = (item.get("geometry") or [{}])[-1]
        coords = geometry.get("coordinates") or []
        cats = ", ".join(c.get("title","") for c in item.get("categories", []))
        title = clean(item.get("title"), 300)
        out.append(Evidence(stable_id("eonet",item.get("id")), title, item.get("link") or "https://eonet.gsfc.nasa.gov/", "NASA EONET", "nasa.gov", 1, parse_date(geometry.get("date")).isoformat(), "official_event_catalog", True, f"Open EONET event; categories: {cats}", coords[1] if len(coords)>1 else None, coords[0] if coords else None, "NASA EONET v3"))
    return out


def fetch_gdacs() -> list[Evidence]:
    root = ET.fromstring(fetch_text("https://www.gdacs.org/xml/rss.xml"))
    out = []
    for item in root.findall(".//item"):
        title = clean(item.findtext("title"), 300)
        link = clean(item.findtext("link"), 500)
        desc = clean(item.findtext("description"), 400)
        if title:
            out.append(Evidence(stable_id("gdacs",link,title), title, link or "https://www.gdacs.org/", "GDACS", "gdacs.org", 1, parse_date(item.findtext("pubDate")).isoformat(), "official_disaster_alert", True, desc, raw_source="GDACS RSS"))
    return out


def load_fixtures() -> list[Evidence]:
    return [Evidence(**item) for item in json.loads(FIXTURES.read_text())]


def cluster_evidence(records: list[Evidence]) -> list[list[Evidence]]:
    clusters: list[list[Evidence]] = []
    for record in sorted(records, key=lambda x: parse_date(x.published_at), reverse=True):
        placed = False
        for cluster in clusters:
            anchor = cluster[0]
            close_time = abs((parse_date(record.published_at)-parse_date(anchor.published_at)).total_seconds()) <= 6*3600
            geo = haversine((record.latitude,record.longitude) if record.latitude is not None else None, (anchor.latitude,anchor.longitude) if anchor.latitude is not None else None)
            if close_time and (jaccard(tokens(record.title), tokens(anchor.title)) >= 0.30 or geo <= 75):
                cluster.append(record); placed = True; break
        if not placed: clusters.append([record])
    return clusters


def assess_cluster(cluster: list[Evidence]) -> Claim:
    ordered = sorted(cluster, key=lambda x: parse_date(x.published_at))
    oldest, newest = ordered[0], ordered[-1]
    families = {e.source_family for e in cluster}
    official = any(e.official for e in cluster)
    quality = min(e.source_tier for e in cluster)
    if official: status, grade = "SUPPORTED", "G3"
    elif len(families) >= 3 and quality <= 2: status, grade = "SUPPORTED", "G2"
    elif len(families) >= 2: status, grade = "PLAUSIBLE", "G2"
    else: status, grade = "NOT_PROVEN", "G1"
    direct, corroboration, source_points = (40 if official else 0), min(25, max(0,len(families)-1)*12), {1:15,2:10,3:4}[quality]
    freshness = max(0, 10-int((datetime.now(timezone.utc)-parse_date(newest.published_at)).total_seconds()/3600))
    score = min(100, 20+direct+corroboration+source_points+freshness)
    text = " ".join(e.title+" "+e.snippet for e in cluster)
    sev, cat = severity(text), category(text)
    if sev == "critical" and status != "NOT_PROVEN": action = "ESCALATE"
    elif sev in {"critical","high"} and status == "NOT_PROVEN": action = "INVESTIGATE"
    elif sev == "high": action = "PREPARE"
    elif status == "NOT_PROVEN": action = "INVESTIGATE"
    else: action = "MONITOR"
    lat = [e.latitude for e in cluster if e.latitude is not None]
    lon = [e.longitude for e in cluster if e.longitude is not None]
    title = max(cluster, key=lambda e: (e.official, -e.source_tier, len(e.title))).title
    why = {
        "conflict":"Could alter civilian risk, military posture, transport, energy flows, or diplomacy.",
        "civil_unrest":"Could affect political stability, public safety, transport, or government continuity.",
        "disaster":"Could create life-safety, infrastructure, displacement, logistics, and humanitarian impacts.",
        "cyber":"Could impair services, expose data, disrupt infrastructure, or propagate through dependencies.",
        "infrastructure":"Could disrupt mobility, communications, energy, logistics, or downstream economic activity.",
        "health":"Could affect health operations, travel, supply chains, and emergency response.",
        "world":"May have operational, political, humanitarian, or market consequences.",
    }[cat]
    return Claim(stable_id("claim",*(sorted(e.id for e in cluster))), title, cat, sev, oldest.published_at, newest.published_at, sum(lat)/len(lat) if lat else None, sum(lon)/len(lon) if lon else None, "Geolocation pending" if not lat else f"{sum(lat)/len(lat):.2f}, {sum(lon)/len(lon):.2f}", sorted(cluster,key=lambda e:parse_date(e.published_at),reverse=True), status, grade, score, len(families), action, f"{len(cluster)} evidence records from {len(families)} independent source families." if len(cluster)>1 else "Initial signal detected; independent corroboration has not accumulated.", why, "The official feed verifies only its underlying observation; cause, attribution and damage may remain open." if official else "Multiple reports may derive from one original account or syndication chain; location and scale may be wrong.", "A retraction, authoritative correction, incompatible sensor data, or evidence of duplication or mislocation.", {"base":20,"direct_evidence":direct,"independent_corroboration":corroboration,"source_quality":source_points,"freshness":freshness})


class Aggregator:
    def __init__(self):
        self.lock, self.cached_at, self.cache = threading.Lock(), 0.0, None

    def collect(self, query: str = DEFAULT_QUERY, force: bool = False) -> dict[str,Any]:
        with self.lock:
            if not force and self.cache and time.time()-self.cached_at < CACHE_TTL: return self.cache
        adapters: list[tuple[str,Callable[[],list[Evidence]]]] = [("GDELT",lambda:fetch_gdelt(query)),("USGS",fetch_usgs),("NASA EONET",fetch_eonet),("GDACS",fetch_gdacs)]
        records, health = [], []
        if os.getenv("AURORA_OFFLINE","").lower() not in {"1","true","yes"}:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(fn):name for name,fn in adapters}
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    try:
                        items = future.result(); records.extend(items); health.append({"source":name,"status":"online","records":len(items),"error":None})
                    except Exception as exc:
                        health.append({"source":name,"status":"degraded","records":0,"error":clean(exc,160)})
        if not records:
            records = load_fixtures(); health.append({"source":"Bundled fixtures","status":"offline_fallback","records":len(records),"error":None})
        claims = [assess_cluster(c) for c in cluster_evidence(records)]
        rank = {"critical":4,"high":3,"medium":2,"low":1}
        claims.sort(key=lambda c:(rank[c.severity],c.confidence_score,parse_date(c.updated_at)),reverse=True)
        payload = {"generated_at":now_iso(),"mode":"offline_fallback" if any(x["status"]=="offline_fallback" for x in health) else "live","query":query,"event_count":len(claims),"evidence_count":len(records),"sources":health,"events":[asdict(c) for c in claims],"methodology":{"status":"SUPPORTED requires an official record or 3 independent source families including Tier 1/2; PLAUSIBLE requires 2; otherwise NOT_PROVEN.","grade":"G1 directional, G2 high-confidence assessment, G3 directly verified observation/outcome.","warning":"Scores are triage aids. Verification applies only to the claim actually evidenced."}}
        with self.lock: self.cache, self.cached_at = payload, time.time()
        return payload


AGGREGATOR = Aggregator()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(STATIC),**kwargs)
    def _json(self,payload,status=200):
        body=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        parsed=urllib.parse.urlparse(self.path)
        if parsed.path=="/api/events":
            params=urllib.parse.parse_qs(parsed.query); query=clean(params.get("query",[DEFAULT_QUERY])[0],500) or DEFAULT_QUERY
            try: self._json(AGGREGATOR.collect(query,params.get("refresh",["0"])[0]=="1"))
            except Exception as exc: self._json({"error":clean(exc),"generated_at":now_iso()},500)
            return
        if parsed.path=="/api/health": self._json({"status":"ok","time":now_iso(),"cache_ttl_seconds":CACHE_TTL}); return
        if parsed.path=="/": self.path="/index.html"
        super().do_GET()


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8080); parser.add_argument("--offline",action="store_true"); args=parser.parse_args()
    if args.offline: os.environ["AURORA_OFFLINE"]="1"
    server=ThreadingHTTPServer((args.host,args.port),Handler); print(f"AURORA LIVE running at http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=="__main__": main()
