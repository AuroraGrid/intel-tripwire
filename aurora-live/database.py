from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


class DatabaseIntegrityError(Exception):
    """Backend-neutral uniqueness or foreign-key violation."""


class HybridRow(dict):
    """Mapping row that also supports SQLite-style numeric indexing."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class CursorResult:
    def __init__(self, cursor, backend: str):
        self.cursor = cursor
        self.backend = backend

    @property
    def rowcount(self) -> int:
        if self.cursor is None:
            return 0
        return self.cursor.rowcount

    def _row(self, row):
        if row is None or self.backend == "sqlite":
            return row
        if isinstance(row, dict):
            return HybridRow(row)
        return row

    def fetchone(self):
        if self.cursor is None:
            return None
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        if self.cursor is None:
            return []
        return [self._row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        if self.cursor is None:
            return
        for row in self.cursor:
            yield self._row(row)


class Connection:
    def __init__(self, connection, backend: str):
        self.connection = connection
        self.backend = backend

    def _sql(self, sql: str) -> str:
        if self.backend != "postgres":
            return sql
        translated = re.sub(
            r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
            "BIGSERIAL PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )
        return translated.replace("?", "%s")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> CursorResult:
        try:
            cursor = self.connection.execute(self._sql(sql), tuple(params))
            return CursorResult(cursor, self.backend)
        except sqlite3.IntegrityError as exc:
            raise DatabaseIntegrityError(str(exc)) from exc
        except Exception as exc:
            if self.backend == "postgres":
                import psycopg
                # Concurrent CREATE TABLE IF NOT EXISTS races on pg_type; treat as success.
                msg = str(exc).lower()
                is_create = sql.lstrip().upper().startswith("CREATE")
                if is_create and (
                    "already exists" in msg
                    or "pg_type_typname_nsp_index" in msg
                    or "duplicate key value violates unique constraint" in msg
                ):
                    return CursorResult(None, self.backend)
                if isinstance(exc, psycopg.IntegrityError):
                    if is_create and ("already exists" in msg or "duplicate" in msg):
                        return CursorResult(None, self.backend)
                    raise DatabaseIntegrityError(str(exc)) from exc
            raise

    def executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self.connection.executescript(script)
            return
        for statement in re.split(r";\s*(?:\n|$)", script.strip()):
            statement = statement.strip()
            if statement:
                self.execute(statement)


class Database:
    def __init__(self, target: str | Path):
        self.target = str(target)
        self.backend = "postgres" if self.target.startswith(("postgres://", "postgresql://")) else "sqlite"
        if self.backend == "sqlite":
            self.path = Path(self.target)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.path = None

    def _connect(self):
        if self.backend == "sqlite":
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL requires psycopg; install aurora-live/requirements.txt") from exc
        return psycopg.connect(self.target, row_factory=dict_row)

    @contextmanager
    def connection(self):
        raw = self._connect()
        wrapped = Connection(raw, self.backend)
        try:
            yield wrapped
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def table_exists(self, table: str) -> bool:
        with self.connection() as connection:
            if self.backend == "sqlite":
                row = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
                    (table,),
                ).fetchone()
        return bool(row)

    def column_names(self, table: str) -> set[str]:
        with self.connection() as connection:
            if self.backend == "sqlite":
                return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            rows = connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
                (table,),
            ).fetchall()
        return {row["column_name"] for row in rows}
