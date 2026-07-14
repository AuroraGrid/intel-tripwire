from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
import urllib.request


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def run_load(base_url, requests=100, concurrency=10, timeout=5.0, opener=None):
    opener = opener or urllib.request.urlopen
    target = base_url.rstrip("/") + "/api/events"

    def one(_):
        started = time.monotonic()
        try:
            with opener(target, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                ok = response.status == 200 and isinstance(payload.get("events"), list)
                return ok, time.monotonic() - started, response.status, None
        except Exception as exc:
            return False, time.monotonic() - started, 0, type(exc).__name__

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(one, range(max(1, requests))))
    elapsed = time.monotonic() - started
    durations = [item[1] for item in results]
    successes = sum(1 for item in results if item[0])
    errors = {}
    for _, _, status, error in results:
        key = error or (str(status) if status != 200 else "")
        if key:
            errors[key] = errors.get(key, 0) + 1
    return {
        "requests": len(results),
        "successes": successes,
        "success_rate": round(successes / len(results), 4),
        "requests_per_second": round(len(results) / elapsed, 2) if elapsed else 0,
        "latency_p50_seconds": round(percentile(durations, 0.50), 4),
        "latency_p95_seconds": round(percentile(durations, 0.95), 4),
        "latency_max_seconds": round(max(durations), 4),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-p95", type=float, default=2.0)
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    args = parser.parse_args()
    result = run_load(args.base_url, args.requests, args.concurrency, args.timeout)
    result["gate_passed"] = result["success_rate"] >= args.min_success_rate and result["latency_p95_seconds"] <= args.max_p95
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["gate_passed"] else 1)


if __name__ == "__main__":
    main()
