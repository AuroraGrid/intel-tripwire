from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from release_engine import ReleaseAggregator


RUNTIME_PATH = Path(os.getenv("AURORA_RUNTIME_PATH", "/data/aurora-runtime.json"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def read_runtime(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or RUNTIME_PATH)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return {}


def write_runtime(value: dict[str, Any], path: str | Path | None = None) -> None:
    target = Path(path or RUNTIME_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def operational_status(snapshot: dict[str, Any], stale_after_seconds: int = 900, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    generated = _parse_time(snapshot.get("generated_at") or snapshot.get("recorded_at"))
    age = max(0, int((current - generated).total_seconds())) if generated else None
    sources = list(snapshot.get("sources") or [])
    online = sum(1 for source in sources if source.get("status") == "online")
    degraded = sum(1 for source in sources if source.get("status") == "degraded")
    fallback = any(source.get("status") == "offline_fallback" for source in sources)
    stale = age is None or age > max(1, int(stale_after_seconds))
    if snapshot.get("status") == "error":
        state = "error"
    elif fallback:
        state = "fallback"
    elif stale:
        state = "stale"
    elif degraded:
        state = "degraded"
    else:
        state = "live"
    return {
        "state": state,
        "stale": stale,
        "age_seconds": age,
        "stale_after_seconds": max(1, int(stale_after_seconds)),
        "generated_at": snapshot.get("generated_at"),
        "recorded_at": snapshot.get("recorded_at"),
        "mode": snapshot.get("mode", "unknown"),
        "query": snapshot.get("query", ""),
        "event_count": int(snapshot.get("event_count") or 0),
        "evidence_count": int(snapshot.get("evidence_count") or 0),
        "raw_evidence_count": int(snapshot.get("raw_evidence_count") or 0),
        "duplicates_suppressed": int(snapshot.get("duplicates_suppressed") or 0),
        "source_count": len(sources),
        "sources_online": online,
        "sources_degraded": degraded,
        "sources": sources,
        "last_error": snapshot.get("last_error"),
    }


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def enrich_incident(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    payload = _object(output.get("payload"))
    for key, value in payload.items():
        output.setdefault(key, value)
    output.setdefault("k_align_status", output.get("status", "NOT_PROVEN"))
    output.setdefault("confidence_grade", output.get("grade", "G1"))
    output.setdefault("confidence_score", output.get("confidence", 0))
    output.setdefault("action_state", output.get("action", "MONITOR"))
    evidence = []
    for row in output.get("evidence") or []:
        source = dict(row)
        merged = _object(source.pop("payload", {}))
        merged.update({key: value for key, value in source.items() if value is not None})
        evidence.append(merged)
    output["evidence"] = evidence
    return output


class OperationalAggregator(ReleaseAggregator):
    def __init__(self, *args, runtime_path: str | Path | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtime_path = Path(runtime_path or RUNTIME_PATH)

    def collect(self, query: str = "", force: bool = False):
        effective_query = query or __import__("app").DEFAULT_QUERY
        try:
            payload = super().collect(effective_query, force)
            snapshot = {
                "status": "ok",
                "recorded_at": _now().isoformat().replace("+00:00", "Z"),
                "generated_at": payload.get("generated_at"),
                "mode": payload.get("mode"),
                "query": payload.get("query", effective_query),
                "event_count": payload.get("event_count", 0),
                "evidence_count": payload.get("evidence_count", 0),
                "raw_evidence_count": payload.get("raw_evidence_count", payload.get("evidence_count", 0)),
                "duplicates_suppressed": payload.get("duplicates_suppressed", 0),
                "sources": payload.get("sources", []),
                "methodology": payload.get("methodology", {}),
            }
            write_runtime(snapshot, self.runtime_path)
            return payload
        except Exception as exc:
            snapshot = read_runtime(self.runtime_path)
            snapshot.update({
                "status": "error",
                "recorded_at": _now().isoformat().replace("+00:00", "Z"),
                "last_error": f"{type(exc).__name__}: {exc}"[:500],
            })
            write_runtime(snapshot, self.runtime_path)
            raise
