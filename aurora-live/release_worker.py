from __future__ import annotations

import app
from release_engine import ReleaseAggregator

app.AGGREGATOR = ReleaseAggregator()

from worker import main

if __name__ == "__main__":
    main()
