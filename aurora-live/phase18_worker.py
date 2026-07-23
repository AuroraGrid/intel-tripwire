from __future__ import annotations

import os

from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_detection import DetectionEngine
from phase17_fabric import RealtimeFabric
from phase17_worker import Phase17Worker
from phase18_graph import EntityGraph


class Phase18Worker(Phase17Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.graph_interval = max(1, int(os.getenv("AURORA_GRAPH_INTERVAL_SECONDS", "5")))
        self.graph_limit = max(1, min(1000, int(os.getenv("AURORA_GRAPH_BATCH_SIZE", "200"))))
        self.state.ensure_job("entity_graph", self.graph_interval)

    def process_graph(self):
        totals = {"workspaces": 0, "processed": 0, "materialized": 0}
        for workspace_id in self.workspace_ids():
            actor = self._actor(workspace_id)
            if not actor:
                continue
            mesh = SensorMesh(self.store)
            integrity = EvidenceIntegrity(self.store)
            detection = DetectionEngine(self.store, mesh, integrity)
            fabric = RealtimeFabric(self.store, detection)
            graph = EntityGraph(self.store, detection, fabric)
            result = graph.process_fabric(actor, self.graph_limit)
            totals["workspaces"] += 1
            totals["processed"] += int(result.get("processed", 0))
            totals["materialized"] += int(result.get("materialized", 0))
        return totals

    def tick(self):
        result = super().tick()
        result["graph"] = self.run_job("entity_graph", self.graph_interval, self.process_graph)
        return result


def main():
    Phase18Worker().run()


if __name__ == "__main__":
    main()
