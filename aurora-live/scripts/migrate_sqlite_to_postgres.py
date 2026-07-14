from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database
from operations import Operations
from storage import Store

TABLES = [
    "users",
    "memberships",
    "api_tokens",
    "watchlists",
    "incidents",
    "evidence",
    "timeline",
    "alerts",
