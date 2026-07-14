import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from operations import Operations
from scripts.migrate_sqlite_to_postgres import migrate
from storage import Store
from test_platform import E, TABLES


@unittest.skipUnless(os.getenv("AURORA_TEST_DATABASE_URL"), "PostgreSQL integration URL not set")
class MigrationTests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.target_url = os.environ["AURORA_TEST_DATABASE_URL"]
        target = Store(database_url=self.target_url)
        Operations(target)
        with target.db() as connection:
            for table in TABLES:
                if target.database.table_exists(table):
                    connection.execute(f"DELETE FROM {table}")

    def test_sqlite_to_postgres_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = str(Path(directory) / "source.db")
            source = Store(source_path)
            operations = Operations(source)
            user, token = source.create_user("migration@example.com", "admin")
            context = source.auth(token)
            workspace_id = context["workspace_id"]
            source.add_watchlist(user["id"], {"name": "Ports", "query": "port"}, workspace_id)
            operations.add_webhook(user["id"], {"name": "Ops", "url": "https://hooks.example.com/aurora"}, workspace_id)
            result = source.ingest({"events": [E]}, workspace_id=workspace_id, actor_user_id=user["id"])
            incident_id = result["incident_ids"][0]
            alert = source.alerts(user["id"], workspace_id)[0]
            operations.queue_deliveries(user["id"], alert["id"], workspace_id)
            case = operations.create_case(user["id"], {"title": "Migration case"}, workspace_id)
            operations.add_case_incident(user["id"], case["id"], incident_id, workspace_id)
            source.add_note(incident_id, user["id"], "Preserve this note", workspace_id)

            copied = migrate(source_path, self.target_url, truncate_target=True)
            self.assertEqual(copied["users"], 1)
            self.assertEqual(copied["memberships"], 1)
            self.assertGreaterEqual(copied["api_tokens"], 1)
            self.assertEqual(copied["incidents"], 1)
            self.assertEqual(copied["deliveries"], 1)
            self.assertGreaterEqual(copied["audit_events"], 1)

            CURRENT_WORKSPACE.set(None)
            target = Store(database_url=self.target_url)
            target_operations = Operations(target)
            authenticated = target.auth(token)
            self.assertEqual(authenticated["email"], "migration@example.com")
            self.assertEqual(authenticated["workspace_id"], workspace_id)
            self.assertEqual(target.incident(incident_id, workspace_id=workspace_id)["title"], E["title"])
            self.assertEqual(len(target_operations.cases(user["id"], workspace_id)), 1)
            self.assertEqual(len(target_operations.pending_deliveries(user["id"], workspace_id=workspace_id)), 1)


if __name__ == "__main__":
    unittest.main()
