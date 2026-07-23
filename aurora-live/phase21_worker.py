from __future__ import annotations

import os

from phase20_operating_picture import LiveOperatingPicture
from phase20_worker import Phase20Worker
from phase21_routes import RouteIntelligence
from phase15_mesh import SensorMesh


class Phase21Worker(Phase20Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.route_interval = max(
            10, int(os.getenv("AURORA_ROUTE_INTERVAL_SECONDS", "60"))
        )
        self.route_limit = max(
            1, min(1000, int(os.getenv("AURORA_ROUTE_BATCH_SIZE", "100")))
        )
        self.state.ensure_job("route_reassessment", self.route_interval)

    def reassess_routes(self):
        totals = {
            "workspaces": 0, "plans_processed": 0,
            "material_changes": 0, "infrastructure_imported": 0,
        }
        for workspace_id in self.workspace_ids():
            actor = self._actor(workspace_id)
            if not actor:
                continue
            mesh = SensorMesh(self.store)
            picture = LiveOperatingPicture(self.store, mesh)
            routes = RouteIntelligence(self.store, picture)
            imported = routes.import_infrastructure(actor, 5000)
            result = routes.recalculate_active(actor, self.route_limit)
            totals["workspaces"] += 1
            totals["plans_processed"] += int(result.get("processed", 0))
            totals["material_changes"] += int(result.get("material_changes", 0))
            totals["infrastructure_imported"] += int(imported.get("imported", 0))
        return totals

    def tick(self):
        result = super().tick()
        result["routes"] = self.run_job(
            "route_reassessment", self.route_interval, self.reassess_routes
        )
        return result


def main():
    Phase21Worker().run()


if __name__ == "__main__":
    main()
