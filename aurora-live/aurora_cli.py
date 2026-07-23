from __future__ import annotations

import argparse
import json
import os
import sys

from phase24_sdk import AuroraClient, AuroraAPIError


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="aurora", description="AURORA LIVE CLI")
    value.add_argument(
        "--url", default=os.getenv("AURORA_URL", "http://127.0.0.1:8090")
    )
    value.add_argument("--token", default=os.getenv("AURORA_TOKEN", ""))
    value.add_argument("--api-key", default=os.getenv("AURORA_API_KEY", ""))
    commands = value.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=50)
    for name in ("detections", "routes", "forecasts"):
        command = commands.add_parser(name)
        command.add_argument("--limit", type=int, default=100)
    tool = commands.add_parser("mcp-call")
    tool.add_argument("tool")
    tool.add_argument("--arguments", default="{}")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        client = AuroraClient(
            args.url, token=args.token, api_key=args.api_key
        )
        if args.command == "search":
            result = client.search(args.query, args.limit)
        elif args.command == "detections":
            result = list(client.detections(limit=args.limit))
        elif args.command == "routes":
            result = list(client.routes(limit=args.limit))
        elif args.command == "forecasts":
            result = list(client.forecast_candidates(limit=args.limit))
        else:
            result = client.mcp_call(
                args.tool, json.loads(args.arguments)
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    except (AuroraAPIError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
