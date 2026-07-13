from __future__ import annotations

from datetime import datetime, timedelta, timezone

from storage import now


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))).isoformat().replace("+00:00", "Z")


class WorkerState:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with