"""Core release worker (Phase 22) plus optional Phase 38–40 layer ingestion.

The platform UI for transport, infrastructure, and markets only stays fresh when
the dedicated Phase 38/39/40 workers run. By default this process starts them as
child processes so a single `python release_worker.py` is enough for local beta
and friend testing.

Disable embedded layer workers with AURORA_START_LAYER_WORKERS=0 (Compose already
runs dedicated aurora-*-worker services).
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
from pathlib import Path

import app
from phase8_runtime import OperationalAggregator

app.AGGREGATOR = OperationalAggregator()

ROOT = Path(__file__).resolve().parent
_CHILDREN: list[subprocess.Popen] = []


def _enabled(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _stop_children(*_args) -> None:
    for proc in list(_CHILDREN):
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    for proc in list(_CHILDREN):
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _CHILDREN.clear()


def _start_layer_workers() -> None:
    if not _enabled("AURORA_START_LAYER_WORKERS", "1"):
        print("layer workers disabled (AURORA_START_LAYER_WORKERS=0)", flush=True)
        return

    py = sys.executable
    commands: list[list[str]] = []
    if _enabled("AURORA_START_TRANSPORT_WORKER", "1"):
        commands.append(
            [
                py,
                str(ROOT / "phase38_worker.py"),
                "--loop",
                "--provider",
                os.getenv("AURORA_TRANSPORT_PROVIDERS", "all"),
            ]
        )
    if _enabled("AURORA_START_INFRASTRUCTURE_WORKER", "1"):
        commands.append(
            [
                py,
                str(ROOT / "phase39_worker.py"),
                "--loop",
                "--interval",
                os.getenv("AURORA_INFRASTRUCTURE_INTERVAL_SECONDS", "300"),
            ]
        )
    if _enabled("AURORA_START_MARKETS_WORKER", "1"):
        commands.append(
            [
                py,
                str(ROOT / "phase40_worker.py"),
                "--loop",
                "--interval",
                os.getenv("AURORA_MARKETS_INTERVAL_SECONDS", "300"),
            ]
        )

    if not commands:
        return

    # Prefer the dedicated supervisor when present so PID files stay consistent.
    supervisor = ROOT / "scripts" / "start_layer_workers.py"
    if supervisor.is_file():
        print("starting Phase 38-40 layer workers via start_layer_workers.py", flush=True)
        proc = subprocess.Popen([py, str(supervisor)], cwd=str(ROOT))
        _CHILDREN.append(proc)
        pid_path = ROOT / "var" / "layer-workers-supervisor.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{proc.pid}\n", encoding="ascii")
        return

    for cmd in commands:
        print("starting", " ".join(cmd), flush=True)
        _CHILDREN.append(subprocess.Popen(cmd, cwd=str(ROOT)))


def main() -> None:
    from phase22_worker import main as phase22_main

    _start_layer_workers()
    atexit.register(_stop_children)
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_stop_children(), sys.exit(0)))
        signal.signal(signal.SIGINT, lambda *_: (_stop_children(), sys.exit(0)))
    except Exception:
        pass
    try:
        phase22_main()
    finally:
        _stop_children()


if __name__ == "__main__":
    main()
