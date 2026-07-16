from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from storage import sid

MODULES={"AURORA_GRID","K_ALIGN","CRF","IPR","BLACKGLASS","COMMAND","AAIK"}
REVIEW_KINDS={"adjudication","red_team"}

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def dumps(v): return json.dumps(v,ensure_ascii=False,se