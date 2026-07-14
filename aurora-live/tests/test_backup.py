import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backup import backup_sqlite, verify_manifest, verify_sqlite_restore


class BackupTests(unittest.TestCase):
    def test_sqlite_backup_manifest_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY,value TEXT)")
                connection.execute("INSERT INTO records(value) VALUES('aurora')")
            output = root / "backup.db"
            data, manifest_path = backup_sqlite(str(source), output)
            self.assertEqual(data["backend"], "sqlite")
            self.assertTrue(manifest_path.exists())
            self.assertEqual(verify_manifest(output)["sha256"], data["sha256"])
            result = verify_sqlite_restore(output)
            self.assertTrue(result["verified"])
            self.assertGreaterEqual(result["tables"], 1)

    def test_checksum_failure_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            with sqlite3.connect(source) as connection: connection.execute("CREATE TABLE x(id INTEGER)")
            output = root / "backup.db"
            backup_sqlite(str(source), output)
            output.write_bytes(output.read_bytes() + b"corruption")
            with self.assertRaises(ValueError): verify_manifest(output)


if __name__ == "__main__": unittest.main()
