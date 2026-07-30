from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from phase38_transport import DOMAINS, TransportStore


def _database_target(value: str = "") -> str:
    target = str(value or "").strip()
    if target:
        return target
    return str(
        os.getenv("AURORA_TRANSPORT_DB")
        or os.getenv("AURORA_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()


def _provider_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": row.get("provider", ""),
        "domain": row.get("domain", ""),
        "state": row.get("state", "UNKNOWN"),
        "operational": bool(row.get("operational")),
        "fresh": bool(row.get("fresh")),
        "seconds_since_success": row.get("seconds_since_success"),
        "freshness_seconds": max(0, int(row.get("freshness_seconds") or 0)),
        "consecutive_failures": max(0, int(row.get("consecutive_failures") or 0)),
        "observations": max(0, int(row.get("observations") or 0)),
        "last_error": str(row.get("last_error") or ""),
    }


def evaluate(
    store: TransportStore,
    *,
    required_domains: tuple[str, ...] = DOMAINS,
    max_age_seconds: int = 900,
) -> tuple[dict[str, Any], bool]:
    unknown = sorted(set(required_domains) - set(DOMAINS))
    if unknown:
        raise ValueError(f"invalid required transport domains: {', '.join(unknown)}")

    health = store.health(max_age_seconds=max_age_seconds)
    providers = [_provider_summary(row) for row in health["providers"]]
    domains: list[dict[str, Any]] = []
    for domain in required_domains:
        rows = [row for row in providers if row["domain"] == domain]
        operational = any(row["operational"] for row in rows)
        domains.append(
            {
                "domain": domain,
                "operational": operational,
                "providers": len(rows),
                "operational_providers": sum(1 for row in rows if row["operational"]),
            }
        )

    passed = bool(domains) and all(row["operational"] for row in domains)
    result = {
        "qualified": passed,
        "required_domains": list(required_domains),
        "operational_domains": sum(1 for row in domains if row["operational"]),
        "max_age_seconds": max(30, int(max_age_seconds)),
        "database_backend": "postgresql" if store.postgres else "sqlite",
        "domains": domains,
        "providers": providers,
        "workers": health.get("workers", []),
        "generated_at": health.get("generated_at", ""),
    }
    return result, passed


def _append_summary(path: str, result: dict[str, Any]) -> None:
    target = str(path or "").strip()
    if not target:
        return
    lines = [
        "## AURORA transport production gate",
        "",
        f"- Qualified: **{str(bool(result['qualified'])).lower()}**",
        f"- Database backend: `{result['database_backend']}`",
        f"- Freshness window: `{result['max_age_seconds']} seconds`",
        f"- Operational domains: `{result['operational_domains']}/{len(result['required_domains'])}`",
        "",
        "### Domains",
    ]
    for domain in result["domains"]:
        lines.append(
            f"- `{domain['domain']}`: operational={str(bool(domain['operational'])).lower()}, "
            f"providers={domain['operational_providers']}/{domain['providers']}"
        )
    lines.extend(["", "### Providers"])
    for provider in result["providers"]:
        age = provider["seconds_since_success"]
        age_text = "unknown" if age is None else f"{age}s"
        lines.append(
            f"- `{provider['provider']}` ({provider['domain']}): state={provider['state']}, "
            f"operational={str(bool(provider['operational'])).lower()}, age={age_text}, "
            f"observations={provider['observations']}"
        )
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate AURORA transport production health")
    parser.add_argument("--database", default="")
    parser.add_argument("--max-age", type=int, default=int(os.getenv("AURORA_TRANSPORT_STALE_SECONDS", "900")))
    parser.add_argument("--require", nargs="+", choices=DOMAINS, default=list(DOMAINS))
    parser.add_argument("--summary-file", default=os.getenv("GITHUB_STEP_SUMMARY", ""))
    args = parser.parse_args()

    target = _database_target(args.database)
    if not target:
        print(json.dumps({"qualified": False, "error": "persistent database is not configured"}, sort_keys=True))
        return 2

    store = TransportStore(target)
    result, passed = evaluate(
        store,
        required_domains=tuple(args.require),
        max_age_seconds=max(30, int(args.max_age)),
    )
    print(json.dumps(result, sort_keys=True))
    _append_summary(args.summary_file, result)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
