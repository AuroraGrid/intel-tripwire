from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from delivery import deliver_pending
from feeds import json_feed, rss_feed
from operations import Operations
from storage import Store, now

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
REQUEST_ID_RE