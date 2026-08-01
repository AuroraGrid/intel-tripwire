"""Start Phase 38/39/40 layer workers for local or operator-managed stacks.

Does not replace release_worker.py (core ingest + delivery). Use alongside it.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _enabled(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    os.chdir(ROOT)
    py = sys.executable
    procs: list[subprocess.Popen] = []
    commands: list[list[str]] = []

    if _enabled("AURORA_START_TRANSPORT_WORKER", "1"):
        commands.append([py, "phase38_worker.py", "--loop", "--provider", os.getenv("AURORA_TRANSPORT_PROVIDERS", "all")])
    if _enabled("AURORA_START_INFRASTRUCTURE_WORKER", "1"):
        commands.append(
            [
                py,
                "phase39_worker.py",
                "--loop",
                "--interval",
                os.getenv("AURORA_INFRASTRUCTURE_INTERVAL_SECONDS", "300"),
            ]
        )
    if _enabled("AURORA_START_MARKETS_WORKER", "1"):
        commands.append(
            [
                py,
                "phase40_worker.py",
                "--loop",
                "--interval",
                os.getenv("AURORA_MARKETS_INTERVAL_SECONDS", "300"),
            ]
        )

    if not commands:
        print("no layer workers enabled", flush=True)
        return 0

    stopping = {"value": False}

    def _stop(*_args):
        stopping["value"] = True
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    for cmd in commands:
        print("starting", " ".join(cmd), flush=True)
        procs.append(subprocess.Popen(cmd, cwd=str(ROOT)))

    # Record PIDs for operator tooling.
    pid_path = ROOT / "var" / "layer-workers.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("\n".join(str(p.pid) for p in procs) + "\n", encoding="ascii")

    exit_code = 0
    try:
        while not stopping["value"]:
            for proc in list(procs):
                code = proc.poll()
                if code is not None:
                    print(f"worker exited pid={proc.pid} code={code}", flush=True)
                    if code != 0:
                        exit_code = code
                    procs.remove(proc)
            if not procs:
                break
            time.sleep(1)
    finally:
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
