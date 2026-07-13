import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from operations import Operations
from storage import Store


class IdentityTests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "identity.db", database_url=os.getenv("AURORA_TEST_DATABASE_URL"))
        self.operations = Operations(self.store)
        if self.store.backend == "postgres":
            with self.store.db() as connection:
                for table in ("deliveries", "webhooks", "case_notes", "case_incidents", "cases", "notes", "alerts", "timeline", "evidence", "incidents", "watchlists", "api_tokens", "memberships", "users"):
                    connection.execute(f"DELETE FROM {table}")
        self.owner, self.owner_token = self.store.create_user("owner-phase4@example.com", "admin")
        self.viewer, self.viewer_token = self.store.create_user("viewer-phase4@example.com", "viewer")
        self.owner_context = self.store.auth(self.owner_token)

    def tearDown(self):
        if self.store.backend == "postgres":
            with self.store.db() as connection:
                for table in ("deliveries", "webhooks", "case_notes", "case_incidents", "cases", "notes", "alerts", "timeline", "evidence", "incidents", "watchlists", "api_tokens", "memberships", "users"):
                    connection.execute(f"DELETE FROM {table}")
        self.temp.cleanup()

    def test_first_admin_is_workspace_owner(self):
        self.assertEqual(self.owner_context["workspace_role"], "owner")
        self.assertIn("owner", self.owner_context["permissions"])

    def test_workspace_token_isolation_and_revocation(self):
        second = self.store.identity.create_workspace(self.owner_context, "Second Workspace")
        self.store.identity.add_membership(self.owner_context, self.viewer["id"], "analyst", second["id"])
        viewer_token_record, viewer_secret = self.store.identity.issue_token(self.owner_context, self.viewer["id"], "second-workspace", workspace_id=second["id"])
        owner_token_record, owner_secret = self.store.identity.issue_token(self.owner_context, self.owner["id"], "second-owner", workspace_id=second["id"])

        second_context = self.store.auth(viewer_secret)
        self.assertEqual(second_context["workspace_id"], second["id"])
        self.store.add_watchlist(self.viewer["id"], {"name": "Second only", "query": "port"})
        self.assertEqual(len(self.store.watchlists(self.viewer["id"])), 1)

        CURRENT_WORKSPACE.set(None)
        default_viewer = self.store.auth(self.viewer_token)
        self.assertNotEqual(default_viewer["workspace_id"], second["id"])
        self.assertEqual(self.store.watchlists(self.viewer["id"]), [])

        CURRENT_WORKSPACE.set(None)
        second_owner = self.store.auth(owner_secret)
        self.assertEqual(second_owner["workspace_id"], second["id"])
        self.store.identity.revoke(second_owner, viewer_token_record["id"])
        CURRENT_WORKSPACE.set(None)
        self.assertIsNone(self.store.auth(viewer_secret))
        self.assertTrue(owner_token_record["id"])

    def test_viewer_cannot_administer_memberships(self):
        CURRENT_WORKSPACE.set(None)
        viewer_context = self.store.auth(self.viewer_token)
        with self.assertRaises(PermissionError):
            self.store.identity.memberships(viewer_context)

    def test_audit_events_are_immutable(self):
        event_id = self.store.identity.audit(self.owner_context["workspace_id"], self.owner["id"], "test.event")
        try:
            with self.store.db() as connection:
                connection.execute("UPDATE audit_events SET action='changed' WHERE id=?", (event_id,))
        except Exception:
            pass
        with self.store.db() as connection:
            row = connection.execute("SELECT action FROM audit_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(row["action"], "test.event")

    def test_expired_token_is_rejected(self):
        record, secret = self.store.identity.issue_token(self.owner_context, self.owner["id"], "expired", expires_at="2000-01-01T00:00:00Z")
        self.assertTrue(record["id"])
        CURRENT_WORKSPACE.set(None)
        self.assertIsNone(self.store.auth(secret))


if __name__ == "__main__":
    unittest.main()
