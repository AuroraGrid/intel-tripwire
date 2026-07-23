from __future__ import annotations

import os

from phase11_store import ForecastLedger
from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase20_operating_picture import LiveOperatingPicture
from phase21_routes import RouteIntelligence
from phase21_worker import Phase21Worker
from phase22_forecasting import AutonomousForecastEngine


class Phase22Worker(Phase21Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.forecast_interval = max(
            10, int(os.getenv("AURORA_FORECAST_INTERVAL_SECONDS", "60"))
        )
        self.forecast_limit = max(
            1, min(1000, int(os.getenv("AURORA_FORECAST_BATCH_SIZE", "100")))
        )
        self.state.ensure_job("forecast_candidates", self.forecast_interval)

    def generate_forecast_candidates(self):
        totals = {
            "workspaces": 0,
            "proposed": 0,
            "approved_refreshed": 0,
            "rejected": 0,
        }
        for workspace_id in self.workspace_ids():
            actor = self._actor(workspace_id)
            if not actor:
                continue
            mesh = SensorMesh(self.store)
            integrity = EvidenceIntegrity(self.store)
            detection = DetectionEngine(self.store, mesh, integrity)
            picture = LiveOperatingPicture(self.store, mesh)
            routes = RouteIntelligence(self.store, picture)
            forecasts = ForecastLedger(self.store)
            engine = AutonomousForecastEngine(
                self.store, forecasts, detection, routes
            )
            result = engine.process(actor, self.forecast_limit)
            totals["workspaces"] += 1
            for key in ("proposed", "approved_refreshed", "rejected"):
                totals[key] += int(result.get(key, 0))
        return totals

    def tick(self):
        result = super().tick()
        result["forecasts"] = self.run_job(
            "forecast_candidates",
            self.forecast_interval,
            self.generate_forecast_candidates,
        )
        return result


def main():
    Phase22Worker().run()


if __name__ == "__main__":
    main()
