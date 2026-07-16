from __future__ import annotations

import json
import secrets
from statistics import mean

from phase10_assets import all_static_assets
from phase10_complete import route_exposure
from storage import now, sid

OUTCOMES = {"true_positive", "false_positive", "false_negative"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class DecisionOperations