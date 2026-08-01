"""Loopback-only Waitress entry for private local beta (127.0.0.1:8090)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from waitress import serve  # noqa: E402
from release_wsgi import application  # noqa: E402


def main() -> None:
    host = os.getenv("AURORA_BETA_BIND", "127.0.0.1")
    port = int(os.getenv("AURORA_BETA_PORT", "8090"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"Refusing non-loopback bind {host!r}. Private local beta must stay on 127.0.0.1."
        )
    print(f"AURORA beta listening on http://{host}:{port}", flush=True)
    serve(application, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
