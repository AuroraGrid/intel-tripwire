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
