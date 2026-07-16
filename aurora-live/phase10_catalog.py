from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Callable

import app


NATURAL_EARTH_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
WORLD_BANK_URL = "https://api.worldbank.org/v2/country?format=json&per_page=400"
CABLES_URL = "https://www.submarinecablemap.com/api/v3/cable/all.json"
LANDINGS_URL = "https://www.submarinecablemap.com/api/v3/landing-point/all.json"
OVERPASS_ENDPOINTS = (
