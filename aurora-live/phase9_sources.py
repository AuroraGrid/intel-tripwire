from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import app
from release_engine import Adapter, adapters as base_adapters


@dataclass(frozen=True)
class SourceSpec:
    name: str
    provider: str
    family: str
    tier: int
    official: bool
    capability: str
    observation_type: str
    coverage