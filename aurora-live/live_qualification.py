from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import app
from phase8_runtime import OperationalAggregator, operational_status
from release_engine import adapters


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def qualify(output: str, retries: int = 2, minimum_online: int = 4) -> dict:
    os.environ.pop("AURORA_OFFLINE", None)
    target = Path(output)
    last_error = None
    payload = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            aggregate = OperationalAggregator(runtime_path=target)
            payload = aggregate.collect(force=True)
            if payload.get("