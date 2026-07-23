from __future__ import annotations

import os

from phase18_worker import Phase18Worker
from phase19_verification import MultimodalVerification


class Phase19Worker(Phase18Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.media_interval = max(
            1, int(os.getenv("AURORA_MEDIA_INTERVAL_SECONDS", "10"))
        )
        self.media_limit = max(
            1, min(1000, int(os.getenv("AURORA_MEDIA_BATCH_SIZE", "100")))
        )
        self.state.ensure_job("media_verification", self.media_interval)

    def process_media(self):
        totals = {"workspaces": 0, "processed": 0, "ready_for_review": 0}
        for workspace_id in self.workspace_ids():
            actor = self._actor(workspace_id)
            if not actor:
                continue
            result = MultimodalVerification(self.store).process_pending(
                actor, self.media_limit
            )
            totals["workspaces"] += 1
            totals["processed"] += int(result.get("processed", 0))
            totals["ready_for_review"] += int(result.get("ready_for_review", 0))
        return totals

    def tick(self):
        result = super().tick()
        result["media"] = self.run_job(
            "media_verification", self.media_interval, self.process_media
        )
        return result


def main():
    Phase19Worker().run()


if __name__ == "__main__":
    main()
