#!/usr/bin/env python3
"""AURORA LIVE: evidence-first global event browser.

Standard-library server with public OSINT adapters, source-lineage deduplication,
K-ALIGN verification states, confidence grades and AURORA action routing.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from