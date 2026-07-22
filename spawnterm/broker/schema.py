#!/usr/bin/env python3
"""sqlite schema + connection management for the spawnTerm broker (#34).

Pure — imports only the stdlib ``sqlite3``; no socket, no asyncio, no iTerm2.
This is the durable-state layer iTerm2 deliberately lacks (see
``spawnterm/docs/design.md`` — "What iTerm2 CANNOT do"). Keep durable state
here, never in iTerm2.

Concurrency: the connection is opened in **WAL** mode with a **busy timeout** so
multiple client processes (the daemon bridge in #37, CLI clients, the server)
can read/write the same file safely.

Migrations: schema creation is **idempotent**. A ``schema_version`` meta table
records which numbered migrations have run; :func:`apply_schema` applies only
the pending ones and is safe to call repeatedly. #35 (mailbox: messages) and
#36 (agent registry + handoff/state history) add their tables by appending new
entries to :data:`MIGRATIONS` (v2, v3, …) — no restructuring here.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# Bump when a new migration is appended below. Equals the highest migration
# version (#35 owns v2 `messages`; #36 owns v3 `agents` + `handoffs`).
SCHEMA_VERSION = 3

# Busy timeout for lock contention across processes (WAL still serializes
# writers). Seconds for the sqlite3.connect timeout; ms for the PRAGMA.
BUSY_TIMEOUT_SECONDS = 5.0
BUSY_TIMEOUT_MS = 5000

_META_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_version ("
    "  version INTEGER NOT NULL PRIMARY KEY,"
    "  applied_at REAL NOT NULL"
    ")"
)

# Ordered, numbered migrations. Each key is a schema version; each value is the
# list of DDL statements that take the schema *to* that version. v1 is the
# baseline (only the meta table, created separately). Future sub-issues append:
#   MIGRATIONS[2] = ["CREATE TABLE IF NOT EXISTS messages (...)"]   # #35 mailbox
#   MIGRATIONS[3] = ["CREATE TABLE IF NOT EXISTS agents (...)", ...] # #36 registry
# Never edit a shipped migration in place — always add the next number.
MIGRATIONS: dict[int, list[str]] = {
    1: [],
    # v3 (#36): persistent agent registry + append-only handoff/state history.
    # Independent of v2 (#35 `messages`) — creates only its own tables, so it
    # applies cleanly whether or not v2 is present (migrations are ordered).
    3: [
        # Queryable agent registry, keyed by session_id. Survives a broker
        # restart (unlike the daemon's ephemeral registry in #26).
        "CREATE TABLE IF NOT EXISTS agents ("
        "  session_id TEXT NOT NULL PRIMARY KEY,"
        "  role TEXT,"
        "  task TEXT,"
        "  capabilities TEXT,"  # JSON array of capability strings
        "  last_seen REAL NOT NULL,"
        "  alive INTEGER NOT NULL DEFAULT 1"  # 0/1
        ")",
        "CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role)",
        "CREATE INDEX IF NOT EXISTS idx_agents_alive ON agents(alive)",
        # Append-only handoff/state history. Each handoff_put inserts a new row
        # with a monotonic id; the latest version per (agent_id, goal) is the
        # highest id, and the full history is every row in id order.
        "CREATE TABLE IF NOT EXISTS handoffs ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  agent_id TEXT NOT NULL,"
        "  goal TEXT NOT NULL,"
        "  context_ptr TEXT,"
        "  owned_files TEXT,"  # JSON array of file paths
        "  verification_status TEXT,"
        "  created_at REAL NOT NULL"
        ")",
        "CREATE INDEX IF NOT EXISTS idx_handoffs_agent_goal "
        "ON handoffs(agent_id, goal, id)",
    ],
}


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) the sqlite db in WAL mode.

    Does not create the schema — call :func:`apply_schema` (or :func:`init_db`).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    # WAL for concurrent readers alongside a writer; busy timeout so a locked
    # write waits instead of raising immediately.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def journal_mode(conn: sqlite3.Connection) -> str:
    """Return the connection's current journal mode (e.g. ``"wal"``)."""
    row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).lower()


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none/absent."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    value = row[0] if row is not None else None
    return int(value) if value is not None else 0


def apply_schema(conn: sqlite3.Connection) -> int:
    """Idempotently create/migrate the schema; return the resulting version.

    Creates the meta table if absent, then runs every migration whose version
    is greater than the currently recorded one. Safe to call any number of
    times — a fully-migrated db is a no-op.
    """
    conn.execute(_META_DDL)
    have = current_version(conn)
    for version in sorted(MIGRATIONS):
        if version <= have:
            continue
        for statement in MIGRATIONS[version]:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
            (version, time.time()),
        )
    conn.commit()
    return current_version(conn)


def init_db(path: str | Path) -> sqlite3.Connection:
    """Open the db and apply the schema in one step; return the connection."""
    conn = open_db(path)
    apply_schema(conn)
    return conn
