from __future__ import annotations

import concurrent.futures
import os
import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Callable

import app


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"}


@dataclass(frozen=True)
class Adapter:
    name: str
    family: str
    tier: int
    official: bool
    capability: str
    fetcher: Callable[[], list[app.Evidence]]