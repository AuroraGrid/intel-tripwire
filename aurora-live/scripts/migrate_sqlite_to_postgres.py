from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database
from operations import Operations
from storage import Store

TABLES = [
    "users",
    "memberships",
    "api_tokens",
    "watchlists",
    "incidents",
    "evidence",
    "timeline",
    "alerts",
    "notes",
    "cases",
    "case_incidents",
    "case_notes",
    "webhooks",
    "deliveries",
    "worker_jobs",
    "worker_heartbeats",
    "audit_events",
]


def count(database: Database, table: str) -> int:
    if not database.table_exists(table):
        return 0
    with database.connection() as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def clear_target(database: Database) -> None:
    with database.connection() as connection:
        for table in reversed(TABLES):
            if database.table_exists(table) and table != "audit_events":
                connection.execute(f"DELETE FROM {table}")


def migrate(source_path: str, target_url: str, truncate_target: bool = False) -> dict[str, int]:
    source = Database(source_path)
    if source.backend != "sqlite":
        raise ValueError("source must be a SQLite database path")

    target_store = Store(database_url=target_url)
    if target_store.backend != "postgres":
        raise ValueError("target must be a PostgreSQL DATABASE_URL")

    Operations(target_store)
    from worker_state import WorkerState
    WorkerState(target_store)
    target = target_store.database

    existing = {table: count(target, table) for table in TABLES}
    mutable_existing = {table: rows for table, rows in existing.items() if table != "audit_events" and rows}
    if mutable_existing:
        if not truncate_target:
            populated = ", ".join(f"{table}={rows}" for table, rows in mutable_existing.items())
            raise RuntimeError("target is not empty; rerun with --truncate-target only after taking a backup: " + populated)
        clear_target(target)

    copied: dict[str, int] = {}
    for table in TABLES:
        if not source.table_exists(table):
            copied[table] = 0
            continue

        source_columns = source.column_names(table)
        target_columns = target.column_names(table)
        columns = [column for column in target_columns if column in source_columns]
        if not columns:
            copied[table] = 0
            continue

        column_sql = ",".join(columns)
        placeholders = ",".join("?" for _ in columns)
        with source.connection() as connection:
            rows = connection.execute(f"SELECT {column_sql} FROM {table}").fetchall()

        with target.connection() as connection:
            for row in rows:
                conflict_clause = "" if table == "audit_events" else " ON CONFLICT DO NOTHING"
                connection.execute(
                    f"INSERT INTO {table}({column_sql}) VALUES({placeholders}){conflict_clause}",
                    tuple(row[column] for column in columns),
                )
        copied[table] = len(rows)

    if copied.get("timeline"):
        with target.connection() as connection:
            connection.execute("SELECT setval(pg_get_serial_sequence('timeline','id'), COALESCE((SELECT MAX(id) FROM timeline),1), true)")

    for table, expected in copied.items():
        actual = count(target, table)
        if table == "audit_events":
            if actual < expected:
                raise RuntimeError(f"verification failed for {table}: expected at least {expected}, found {actual}")
        elif actual != expected:
            raise RuntimeError(f"verification failed for {table}: expected {expected}, found {actual}")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy an AURORA SQLite workspace database into PostgreSQL")
    parser.add_argument("--source", required=True, help="Path to the SQLite database")
    parser.add_argument("--target", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL URL; defaults to DATABASE_URL")
    parser.add_argument("--truncate-target", action="store_true", help="Delete existing mutable target data before copying; immutable audit history is preserved")
    args = parser.parse_args()
    if not args.target:
        parser.error("--target or DATABASE_URL is required")
    copied = migrate(args.source, args.target, args.truncate_target)
    for table in TABLES:
        print(f"{table}: {copied[table]}")


if __name__ == "__main__":
    main()
