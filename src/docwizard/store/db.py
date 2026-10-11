"""SQLite connection management (one connection per thread, WAL) and migrations."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docwizard.store.schema import MIGRATIONS

JSON_COLS = {"meta", "params", "changes", "profile", "roles", "event", "result", "steps", "details"}
BOOL_COLS = {"internet", "compacted", "pinned", "valid", "confidential", "archived"}

_local = threading.local()
_lock = threading.Lock()
_db_path: Path | None = None
_migrated: set[str] = set()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def set_db_path(path: str | Path | None) -> None:
    """Point the store at another database file (tests). None = default from settings."""
    global _db_path, _default
    with _lock:
        _db_path = Path(path) if path else None
        _default = None


_default: tuple[str, Path] | None = None


def db_path() -> Path:
    """Explicit path, else `<data dir>/docwizard.db` (cached per DOCWIZARD_HOME value)."""
    global _default
    if _db_path is not None:
        return _db_path
    env = os.environ.get("DOCWIZARD_HOME", "")
    if _default is None or _default[0] != env:
        from docwizard.config import app_paths

        _default = (env, app_paths().db)
    return _default[1]


def conn() -> sqlite3.Connection:
    """Connection for the current thread and current db path (created on demand)."""
    path = db_path()
    key = str(path.resolve())
    cache: dict[str, sqlite3.Connection] = getattr(_local, "conns", None) or {}
    _local.conns = cache
    c = cache.get(key)
    if c is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        c.row_factory = _row_factory
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("PRAGMA synchronous=NORMAL")
        _migrate(c, key)
        cache[key] = c
    return c


def close_all() -> None:
    """Close this thread's connections."""
    for c in (getattr(_local, "conns", None) or {}).values():
        c.close()
    _local.conns = {}


class tx:
    """`with tx() as c:` — a write transaction (BEGIN IMMEDIATE) on this thread's connection."""

    def __enter__(self) -> sqlite3.Connection:
        self.c = conn()
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, exc_type, *_: Any) -> None:
        self.c.execute("ROLLBACK" if exc_type else "COMMIT")


def _row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    d = {}
    for (name, *_), v in zip(cursor.description, row, strict=True):
        if name in JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        elif name in BOOL_COLS and v is not None:
            v = bool(v)
        d[name] = v
    return d


def dumps(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _migrate(c: sqlite3.Connection, key: str) -> None:
    with _lock:
        if key in _migrated:
            return
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        for version, sql in enumerate(MIGRATIONS, start=1):
            if version <= current:
                continue
            c.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _split_sql(sql):
                    c.execute(stmt)
                c.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        _migrated.add(key)


def _split_sql(script: str) -> list[str]:
    """Split a script into statements (keeps trigger bodies intact)."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out
