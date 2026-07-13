from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

from database import DatabaseIntegrityError

ROLES = {
    "viewer": {"read"},
    "analyst": {"read", "write"},
    "admin": {"read", "write", "ingest", "admin", "workers"},
    "owner": {"read", "write", "ingest", "admin",