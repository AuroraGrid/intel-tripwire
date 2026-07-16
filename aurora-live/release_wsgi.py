from __future__ import annotations

import app
from phase8_runtime import OperationalAggregator

app.AGGREGATOR = OperationalAggregator()

from phase12_complete import application  # noqa: E402,F401
