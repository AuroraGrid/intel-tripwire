#!/usr/bin/env python3
"""AURORA LIVE platform service.

Adds persistence, bearer-token users, watchlists, alert matching, incident history,
evidence graphs, webhook delivery, and a Vercel-compatible request application on
top of the evidence engine in app.py. Uses SQLite locally; DATABASE_PATH controls
where the database file is stored.
"""
from __future__ import annotations

import hashlib
