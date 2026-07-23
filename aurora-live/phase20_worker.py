from __future__ import annotations

import os

from phase15_mesh import SensorMesh
from phase19_worker import Phase19Worker
from phase20_operating_picture import LiveOperatingPicture


class Phase20Worker(Phase19Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.operating_interval = max(
            1, int(os.getenv("AURORA_OPERATING_INTERVAL_SECONDS", "10"))
        )
        self.operating_limit = max(
            1, min(5000, int(os.getenv("AURORA_OPERATING_BATCH_SIZE", "1000")))
        )
        self.state.ensure_job("live_operating_picture", self.operating_interval)

    def process_operating_picture(self):
        totals = {
            "workspaces": 0, "observations_seen": 0,
            "positions_materialized": 0,
            "infrastructure_materialized": 0, "rejected": 0,
        }
        for workspace_id in self.workspace_ids():
            actor = self._actor(workspace_id)
            if not actor:
                continue
            mesh = SensorMesh(self.store)
            result = LiveOperatingPicture(self.store, mesh).process_mesh(
                actor, self.operating_limit
            )
            totals["workspaces"] += 1
            for key in (
                "observations_seen", "positions_materialized",
                "infrastructure_materialized", "rejected",
            ):
                totals[key] += int(result.get(key, 0))
        return totals

    def tick(self):
        result = super().tick()
        result["operating_picture"] = self.run_job(
            "live_operating_picture",
            self.operating_interval,
            self.process_operating_picture,
        )
        return result


def main():
    Phase20Worker().run()


if __name__ == "__main__":
    main()
