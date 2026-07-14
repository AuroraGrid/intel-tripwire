from __future__ import annotations

import argparse
import os
from http.server import ThreadingHTTPServer

import app
from release_engine import ReleaseAggregator

app.AGGREGATOR = ReleaseAggregator()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.offline:
        os.environ["AURORA_OFFLINE"] = "1"
    server = ThreadingHTTPServer((args.host, args.port), app.Handler)
    print(f"AURORA LIVE release candidate running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
