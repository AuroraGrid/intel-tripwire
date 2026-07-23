from __future__ import annotations

import os

from identity import ROLES
from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase17_fabric import RealtimeFabric
from worker import AuroraWorker


class Phase17Worker(AuroraWorker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fabric_interval = max(1, int(os.getenv("AURORA_FABRIC_INTERVAL_SECONDS", "5")))
        self.fabric_limit = max(1, min(1000, int(os.getenv("AURORA_FABRIC_BATCH_SIZE", "200"))))
        self.state.ensure_job("realtime_fabric", self.fabric_interval)

    def _actor(self, workspace_id):
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT user_id,role FROM memberships WHERE workspace_id=? ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,created_at LIMIT 1",
                (workspace_id,),
            ).fetchone()
        if not row:
            return None
        role = row["role"]
        return {"id": row["user_id"], "workspace_id": workspace_id, "workspace_role": role, "role": "admin" if role == "owner" else role, "permissions": sorted(ROLES.get(role, set()))}

    def process_fabric(self):
        totals = {"workspaces": 0, "processed": 0, "created": 0, "linked": 0, "events_published": 0}
        for workspace_id in self.workspace_ids():
            actor = self._actor(workspace_id)
            if not actor:
                continue
            mesh = SensorMesh(self.store)
            integrity = EvidenceIntegrity(self.store)
            detection = DetectionEngine(self.store, mesh, integrity)
            fabric = RealtimeFabric(self.store, detection)
            result = fabric.process_pending(actor, self.fabric_limit)
            totals["workspaces"] += 1
            for key in ("processed", "created", "linked", "events_published"):
                totals[key] += int(result.get(key, 0))
        return totals

    def tick(self):
        result = super().tick()
        result["fabric"] = self.run_job("realtime_fabric", self.fabric_interval, self.process_fabric)
        return result


def main():
    Phase17Worker().run()


if __name__ == "__main__":
    main()
