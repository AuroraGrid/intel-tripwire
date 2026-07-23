from __future__ import annotations

from phase14_integrity import now
from phase16_detection import DetectionEngine as BaseDetectionEngine
from storage import sid


class DetectionEngine(BaseDetectionEngine):
    """Synchronize explicit detection contradictions into the atomic claim ledger."""

    def _link_claim(self, actor, detection_id, observation, relation, similarity):
        claim_id = super()._link_claim(actor, detection_id, observation, relation, similarity)
        if relation == "CONTRADICTS":
            self._mark_claim_disputed(actor, claim_id, detection_id, observation)
        return claim_id

    def _mark_claim_disputed(self, actor, claim_id, detection_id, observation):
        claim = self.integrity.claim(actor, claim_id, include_evidence=False)
        if claim["status"] in {"DISPUTED", "RETRACTED"}:
            return
        assessment = self.integrity.assessment(actor, claim_id, persist=False)
        stamp = now()
        confidence = max(float(claim["confidence"]), float(assessment.get("confidence") or 0.0))
        reason = "Explicit contradiction linked by the Phase 16 detection engine"
        revision_id = sid("claim-revision", actor["workspace_id"], claim_id, stamp, "DISPUTED")
        with self.store.db() as connection:
            connection.execute(
                "UPDATE atomic_claims SET status='DISPUTED',confidence=?,updated_at=? WHERE id=? AND workspace_id=?",
                (confidence, stamp, claim_id, actor["workspace_id"]),
            )
            connection.execute(
                """INSERT INTO claim_revisions(id,workspace_id,claim_id,from_status,to_status,
                from_confidence,to_confidence,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (revision_id, actor["workspace_id"], claim_id, claim["status"], "DISPUTED",
                 float(claim["confidence"]), confidence, reason, actor["id"], stamp),
            )
        self.store.identity.audit(
            actor["workspace_id"], actor["id"], "claim.disputed_by_detection", "atomic_claim", claim_id,
            metadata={"detection_id": detection_id, "observation_id": observation["id"]},
        )
