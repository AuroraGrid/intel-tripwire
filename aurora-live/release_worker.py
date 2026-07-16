from __future__ import annotations

import app
from phase8_runtime import OperationalAggregator

app.AGGREGATOR = OperationalAggregator()

from worker import main

if __name__ == "__main__":
    main()
