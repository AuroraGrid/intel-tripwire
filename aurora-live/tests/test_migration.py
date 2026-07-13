import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations import Operations
from scripts.migrate_sqlite_to_postgres import migrate
from storage import Store
from test_platform import E, TABLES


@unittest.skipUnless(os.getenv("AURORA_TEST_DATABASE_URL"), "PostgreSQL integration URL not set")
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.target_url = os.environ["AURORA_TEST_DATABASE_URL"]
        target = Store(database_url=self.target_url)
        Operations(target)
        with target.db() as connection:
            for table in TABLES:
                connection.execute(f"DELETE FROM {table}")

    def test_sqlite_to_postgres_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = str(Path(directory) / "source.db")
            source = Store(source_path)
            operations = Operations(source)
            user, token = source.create_user("migration@example.com", "admin")
            source.add_watchlist(user["id"], {"name": "Ports", "query": "port"})
            operations.add_webhook(user["id"], {"name": "Ops", "url": "https://hooks.example.com/aurora"})
            source.ingest({"events": [E]})
            alert = source.alerts(user["id"])[0]
            operations.queue_deliveries(user["id"], alert["id"])
            case = operations.create_case(user["id"], {"title": "Migration case"})
            operations.add_case_incident(user["id"], case["id"], "i1")
            source.add_note("i1", user["id"], "Preserve this note")

            copied = migrate(source_path, self.target_url, truncate_target=True)
            self.assertEqual(copied["users"], 1)
            self.assertEqual(copied["incidents"], 1)
            self.assertEqual(copied["deliveries"], 1)

            target = Store(database_url=self.target_url)
            target_operations = Operations(target)
            self.assertEqual(target.auth(token)["email"], "migration@example.com")
            self.assertEqual(target.incident("i1")["title"], E["title"])
            self.assertEqual(len(target_operations.cases(user["id"])), 1)
            self.assertEqual(len(target_operations.pending_deliveries(user["id"])), 1)


if __name__ == "__main__":
    unittest.main()
