from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import gc
from datetime import datetime, timezone
from pathlib import Path

from observability import log_event


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def manifest(path: Path, backend: str, source: str):
    data = {"created_at": now(), "backend": backend, "source": source, "file": path.name, "size": path.stat().st_size, "sha256": digest(path)}
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data, manifest_path


def backup_sqlite(source: str, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    # If the source is a regular file path and not an active connection/URI, a copy is sufficient
    # and avoids platform-specific locking issues on Windows.
    try:
        src_path = Path(source)
    except Exception:
        src_path = None
    if src_path and src_path.exists():
        # Try opening the source read-only via URI to avoid locking issues on Windows.
        try:
            src = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
            try:
                dst = sqlite3.connect(output)
                try:
                    src.backup(dst)
                    dst.commit()
                finally:
                    dst.close()
            finally:
                src.close()
        except Exception:
            # Fall back to copying the file if backup via readonly URI fails.
            shutil.copy2(src_path, output)
    else:
        # Fallback to the SQLite online backup API when source is a URI or not a simple file.
        src = sqlite3.connect(source)
        try:
            dst = sqlite3.connect(output)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
    # Force garbage collection to help release any lingering file handles on Windows
    gc.collect()
    data, manifest_path = manifest(output, "sqlite", source)
    log_event("backup_created", backend="sqlite", output=str(output), sha256=data["sha256"])
    return data, manifest_path


def backup_postgres(url: str, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pg_dump", "--format=custom", "--file", str(output), url], check=True)
    data, manifest_path = manifest(output, "postgres", url.split("@")[-1])
    log_event("backup_created", backend="postgres", output=str(output), sha256=data["sha256"])
    return data, manifest_path


def verify_manifest(path: Path, manifest_path: Path | None = None):
    manifest_path = manifest_path or path.with_suffix(path.suffix + ".manifest.json")
    data = json.loads(manifest_path.read_text())
    if path.stat().st_size != int(data["size"]): raise ValueError("backup size mismatch")
    if digest(path) != data["sha256"]: raise ValueError("backup checksum mismatch")
    return data


def verify_sqlite_restore(path: Path):
    verify_manifest(path)
    with tempfile.TemporaryDirectory() as directory:
        restored = Path(directory) / "restored.db"
        shutil.copy2(path, restored)
        # Use explicit connect/close and force GC to avoid lingering file handles on Windows
        conn = sqlite3.connect(f"file:{restored.as_posix()}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok": raise ValueError(f"SQLite integrity check failed: {result}")
            tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            conn.close()
        gc.collect()
    log_event("restore_verified", backend="sqlite", backup=str(path), tables=tables)
    return {"verified": True, "backend": "sqlite", "tables": tables}


def verify_postgres_restore(path: Path, target_url: str):
    verify_manifest(path)
    subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--dbname", target_url, str(path)], check=True)
    command = ["psql", target_url, "-Atqc", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"]
    tables = int(subprocess.check_output(command, text=True).strip())
    if tables < 1: raise ValueError("restored PostgreSQL database has no public tables")
    log_event("restore_verified", backend="postgres", backup=str(path), tables=tables)
    return {"verified": True, "backend": "postgres", "tables": tables}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--database", default=os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", "data/aurora-live.db"))
    create.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--target-database")
    args = parser.parse_args()
    if args.command == "create":
        result = backup_postgres(args.database, Path(args.output)) if args.database.startswith(("postgres://", "postgresql://")) else backup_sqlite(args.database, Path(args.output))
        print(json.dumps(result[0], sort_keys=True))
    else:
        path = Path(args.backup)
        data = verify_manifest(path)
        result = verify_postgres_restore(path, args.target_database) if data["backend"] == "postgres" else verify_sqlite_restore(path)
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__": main()
