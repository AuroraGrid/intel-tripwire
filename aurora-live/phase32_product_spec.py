from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

STATUSES = {"LIVE", "PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"}
PRIORITIES = {"P0", "P1", "P2", "P3"}


@dataclass(frozen=True)
class Capability:
    key: str
    domain: str
    name: str
    status: str
    priority: str
    evidence: tuple[str, ...] = ()
    blocker: str = ""

    def value(self) -> dict[str, Any]:
        item = asdict(self)
        item["evidence"] = list(self.evidence)
        return item


CAPABILITIES = (
    Capability("global-event-map", "operating-picture", "Live global event map", "LIVE", "P0", ("GDELT", "USGS", "NASA EONET", "GDACS")),
    Capability("incident-room", "analysis", "Incident Room", "LIVE", "P0", ("evidence chain", "confidence", "contradictions", "falsifier")),
    Capability("source-health", "operations", "Source Health panel", "LIVE", "P0", ("feed state", "fallback state", "worker health")),
    Capability("event-replay", "operating-picture", "Replayable event timelines", "PARTIAL", "P0", ("stored event history",)),
    Capability("evidence-drawers", "analysis", "Evidence drawers", "LIVE", "P0", ("records", "source tiers", "audit trail")),
    Capability("corrections", "analysis", "Corrections and revision history", "LIVE", "P0", ("record lock", "accuracy history")),
    Capability("forecast-tracking", "analysis", "Forecast and probability tracking", "LIVE", "P0", ("CRF", "triggers", "falsifiers")),
    Capability("downloadable-reports", "distribution", "Downloadable intelligence reports", "LIVE", "P1", ("verifiable packages", "SHA-256 manifests")),
    Capability("webcams", "media", "At least 10 live webcams per world region", "PLANNED", "P0", blocker="curated source registry and embed-health monitor required"),
    Capability("live-imagery", "media", "Live images and video", "PARTIAL", "P0", blocker="rights, provenance and availability controls incomplete"),
    Capability("video-verification", "verification", "Image and video verification", "PLANNED", "P1"),
    Capability("satellite-imagery", "sensors", "Satellite imagery layers", "PLANNED", "P1", blocker="provider adapters and licensing policy required"),
    Capability("aviation", "transportation", "Live aircraft tracking", "PLANNED", "P0", blocker="provider selection, rate limits and attribution required"),
    Capability("maritime", "transportation", "Live ship and maritime tracking", "PLANNED", "P0", blocker="AIS provider selection and coverage limits required"),
    Capability("earthquakes", "disasters", "Earthquake monitoring", "LIVE", "P0", ("USGS",)),
    Capability("volcanoes", "disasters", "Volcano monitoring", "PARTIAL", "P1", ("NASA EONET", "GDACS")),
    Capability("wildfires", "disasters", "Wildfire monitoring", "PARTIAL", "P0", ("NASA EONET",)),
    Capability("weather", "weather", "Severe weather monitoring", "PLANNED", "P0"),
    Capability("hurricanes", "weather", "Hurricanes and typhoons", "PARTIAL", "P0", ("GDACS",)),
    Capability("flooding", "disasters", "Flood monitoring", "PARTIAL", "P0", ("GDACS", "NASA EONET")),
    Capability("internet-outages", "infrastructure", "Internet outage monitoring", "PLANNED", "P0"),
    Capability("bgp", "infrastructure", "BGP disruption monitoring", "PLANNED", "P0"),
    Capability("power-outages", "infrastructure", "Power outage monitoring", "PLANNED", "P1"),
    Capability("cyberalerts", "cyber", "Cybersecurity alerts and incidents", "PARTIAL", "P0"),
    Capability("infrastructure", "infrastructure", "Infrastructure disruption monitoring", "PARTIAL", "P0"),
    Capability("energy", "markets", "Energy and oil/gas markets", "PLANNED", "P0"),
    Capability("commodities", "markets", "Commodity markets", "PLANNED", "P1"),
    Capability("currencies", "markets", "Currency markets", "PLANNED", "P1"),
    Capability("crypto", "markets", "Cryptocurrency markets", "PLANNED", "P1"),
    Capability("global-stocks", "markets", "Global stock markets and indexes", "PLANNED", "P0"),
    Capability("prediction-markets", "forecasting", "Prediction markets", "PLANNED", "P0"),
    Capability("elections", "forecasting", "Election forecasts", "PLANNED", "P1"),
    Capability("political-risk", "forecasting", "Political-risk indicators", "PARTIAL", "P0"),
    Capability("economic-indicators", "markets", "Economic indicators", "PLANNED", "P1"),
    Capability("sanctions", "government", "Sanctions monitoring", "PLANNED", "P0"),
    Capability("government-alerts", "government", "Government alerts", "PARTIAL", "P0"),
    Capability("social-intake", "signals", "Rapid social-media intake", "PLANNED", "P1", blocker="provenance and manipulation controls required"),
    Capability("telegram", "signals", "Telegram signal intake", "PLANNED", "P1", blocker="legal, account and provenance controls required"),
    Capability("watchlists", "workflow", "Watchlists", "LIVE", "P0"),
    Capability("geofences", "workflow", "Geofences", "PARTIAL", "P1"),
    Capability("alerts", "workflow", "Email, webhook and mobile alerts", "PARTIAL", "P0"),
    Capability("analyst-workspaces", "workflow", "Analyst workspaces", "LIVE", "P0"),
    Capability("pwa", "client", "Mobile and PWA access", "PLANNED", "P0"),
    Capability("free-public", "product", "Free public no-paywall deployment", "PLANNED", "P0", blocker="public hosting, abuse controls and operating budget required"),
)


def manifest() -> dict[str, Any]:
    items = [capability.value() for capability in CAPABILITIES]
    counts = {status: sum(item["status"] == status for item in items) for status in sorted(STATUSES)}
    priorities = {priority: sum(item["priority"] == priority for item in items) for priority in sorted(PRIORITIES)}
    return {
        "product": "AURORA LIVE",
        "phase": 32,
        "mission": "A free global evidence and decision-intelligence operating system combining live events, media, markets, transportation, disasters, infrastructure and verified analysis.",
        "workflow": ["SCOUT", "SOURCEGRID", "K-ALIGN", "BLACKGLASS", "CRF/IPR", "COMMAND", "AURORA GRID", "RECORD LOCK"],
        "regions": ["Oceania", "Africa", "Asia", "Middle East", "Europe", "North America", "South America"],
        "interface": ["Global Operating Picture", "Incident Room", "Source Health"],
        "counts": counts,
        "priority_counts": priorities,
        "capabilities": items,
    }


def gaps(priority: str = "") -> dict[str, Any]:
    normalized = str(priority or "").upper()
    if normalized and normalized not in PRIORITIES:
        raise ValueError("invalid priority")
    open_states = {"PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"}
    items = [capability.value() for capability in CAPABILITIES if capability.status in open_states and (not normalized or capability.priority == normalized)]
    items.sort(key=lambda item: (item["priority"], item["domain"], item["key"]))
    return {"total": len(items), "priority": normalized or "ALL", "gaps": items}
