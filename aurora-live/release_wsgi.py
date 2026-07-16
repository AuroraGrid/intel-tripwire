from __future__ import annotations

import app
from phase8_runtime import OperationalAggregator

app.AGGREGATOR = OperationalAggregator()

from phase11_complete import application  # noqa: E402,F401
