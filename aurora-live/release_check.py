from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path

PLACEHOLDERS = ("replace-with", "changeme", "example.com")


def read_env(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def validate(values, public=True):
    errors = []
    for key in ("POSTGRES_PASSWORD", "AURORA_BOOTSTRAP_SECRET", "AURORA_WEBHOOK_SECRET"):
        value = values.get(key, "")
        if len(value) < 32:
            errors.append(f"{key} must contain at least 32 characters")
        if any(marker in value.lower() for marker in PLACEHOLDERS):
            errors.append(f"{key} still contains a placeholder")
    origin = values.get("AURORA_CORS_ORIGIN", "")
    if public and not origin.startswith("https://"):
        errors.append("AURORA_CORS_ORIGIN must use https")
    hosts = [item.strip() for item in values.get("AURORA_ALLOWED_HOSTS", "").split(",") if item.strip()]
    if not hosts or "*" in hosts:
        errors.append("AURORA_ALLOWED_HOSTS must be explicit and non-wildcard")
    if public and not any(host not in {"localhost", "127.0.0.1"} and "example.com" not in host for host in hosts):
        errors.append("AURORA_ALLOWED_HOSTS needs a real public hostname")
    for proxy in [item.strip() for item in values.get("AURORA_TRUSTED_PROXIES", "").split(",") if item.strip()]:
        try:
            ipaddress.ip_network(proxy, strict=False)
        except ValueError:
            errors.append(f"invalid trusted proxy network: {proxy}")
    if values.get("AURORA_REQUIRE_WORKER", "1").lower() not in {"1", "true", "yes"}:
        errors.append("AURORA_REQUIRE_WORKER must be enabled")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--allow-local", action="store_true")
    args = parser.parse_args()
    errors = validate(read_env(args.env), public=not args.allow_local)
    print(json.dumps({"passed": not errors, "errors": errors}, indent=2, sort_keys=True))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
