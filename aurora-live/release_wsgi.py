from __future__ import annotations

import app
from release_engine import ReleaseAggregator

app.AGGREGATOR = ReleaseAggregator()

from production_wsgi import application  # noqa: E402,F401
