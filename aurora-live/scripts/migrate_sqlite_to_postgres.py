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
    "notes",
    "cases",
    "case_incidents",
    "case_notes",
    "webhooks",
    "deliveries",
    "worker_jobs",
    "worker_heartbeats",
    "audit_events",
]


def count(database: Database, table: str) -> int:
    if not database.table_exists(table):
        return 0
    with database