#!/usr/bin/env python3
"""AURORA LIVE platform API: persistence, users, watchlists, alerts, timelines.

Runs with Python's standard library. SQLite is the local/default store. The API is
intentionally dependency-free so it can be tested immediately and migrated to
PostgreSQL behind the same repository layer later.
"""
from __future__ import annotations