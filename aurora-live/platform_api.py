#!/usr/bin/env python3
"""AURORA LIVE platform API.

Dependency-free persistence and workflow layer for the AURORA LIVE evidence engine.
SQLite is the local/default store. The repository boundary is deliberately small so
PostgreSQL can replace it without changing the HTTP contract.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, is_dat