#!/usr/bin/env python3
"""sqlite schema + connection management for the it2agent broker (#34).

Pure — imports only the stdlib ``sqlite3``; no socket, no asyncio, no iTerm2.
This is the durable-state layer iTerm2 deliberately lacks (see
``it2agent/docs/design.md`` — "What iTerm2 CANNOT do"). Keep durable state
here, never in iTerm2.

Concurrency: the connection is opened in **WAL** mode with a **busy timeout** so
multiple client processes (the daemon bridge in #37, CLI clients, the server)
can read/write the same file safely.

Migrations: schema creation is **idempotent**. A ``schema_version`` meta table
records which numbered migrations have run; :func:`apply_schema` applies only
the pending ones and is safe to call repeatedly. #35 (mailbox: messages) and
#36 (agent registry + handoff/state history) add their tables by appending new
entries to :data:`MIGRATIONS` (v2, v3, …) — no restructuring here.

Resilience (#133): :func:`init_db` refuses to open a corrupt/unreadable file. It
runs an integrity check on open and, on any sqlite ``DatabaseError`` (or a
non-``ok`` check), raises :class:`CorruptDatabaseError` with a clear, actionable
message instead of a raw traceback — and it never silently recreates the file
(that would be silent data loss). An operator can deliberately move a corrupt db
aside and start fresh with :func:`reset_corrupt_db` (wired to ``serve --reset``).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

# Bump when a new migration is appended below. Equals the highest migration
# version (#35 owns v2 `messages`; #36 owns v3 `agents` + `handoffs`;
# #95 owns v4 `messages.idempotency_key` + its partial-unique index;
# #133 owns v5 `messages(state, created_at)` retention index).
SCHEMA_VERSION = 5


class CorruptDatabaseError(Exception):
    """Raised when the broker's sqlite db is corrupt or unreadable on open.

    Carries a ready-to-print, actionable message (what failed, the path, and how
    to recover) so the entry point can surface a clean error and a nonzero exit
    rather than a raw ``sqlite3.DatabaseError`` traceback. The broker never
    recreates the file on its own — recovery is an explicit operator action
    (restore a backup, or ``serve --reset``) so data loss is never silent.
    """

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
    # v2 (#35 mailbox): durable per-agent message queue + ack cursor. The
    # ``messages`` table is the exactly-once-per-cursor delivery log; the
    # ``ack_cursors`` table records each recipient's high-water acked id. See
    # it2agent/broker/mailbox.py for the ordering/replay/ack semantics.
    2: [
        "CREATE TABLE IF NOT EXISTS messages ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  sender TEXT NOT NULL,"
        "  recipient TEXT NOT NULL,"
        "  body TEXT NOT NULL,"
        "  created_at REAL NOT NULL,"
        "  state TEXT NOT NULL DEFAULT 'pending'"
        ")",
        # Per-recipient ordered fetch (poll walks recipient rows by ascending id).
        "CREATE INDEX IF NOT EXISTS idx_messages_recipient_id"
        "  ON messages(recipient, id)",
        # Un-acked scan for replay (recipient + state, still ordered by id).
        "CREATE INDEX IF NOT EXISTS idx_messages_recipient_state_id"
        "  ON messages(recipient, state, id)",
        "CREATE TABLE IF NOT EXISTS ack_cursors ("
        "  agent TEXT NOT NULL PRIMARY KEY,"
        "  cursor INTEGER NOT NULL DEFAULT 0,"
        "  updated_at REAL NOT NULL"
        ")",
    ],
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
    # v4 (#95 idempotent send): add an optional idempotency key to messages so
    # an at-least-once retry of `send` (e.g. the team bridge re-firing a
    # TaskCompleted) dedups instead of appending a duplicate. Purely additive:
    #   * a nullable `idempotency_key` column — legacy rows and keyless sends
    #     leave it NULL, so existing behavior is unchanged;
    #   * a PARTIAL unique index on (recipient, idempotency_key) WHERE the key is
    #     non-null — this enforces at-most-one message per (recipient, key) at
    #     the db level while leaving NULL-key rows completely unconstrained
    #     (SQLite treats NULLs as distinct, and the partial predicate excludes
    #     them entirely). This migration ALTERs the table created in v2, so it
    #     upgrades an existing db in place — no fresh db required.
    4: [
        "ALTER TABLE messages ADD COLUMN idempotency_key TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_recipient_idempotency_key"
        "  ON messages(recipient, idempotency_key)"
        "  WHERE idempotency_key IS NOT NULL",
    ],
    # v5 (#133 retention): a supporting index for the retention/prune sweep,
    # which deletes ACKED messages older than a cutoff (see maintenance.py).
    # An index on (state, created_at) lets that DELETE locate prunable rows
    # without a full-table scan. Purely additive — no column or table change,
    # existing rows are untouched; an older db upgrades in place by just
    # creating the index.
    5: [
        "CREATE INDEX IF NOT EXISTS idx_messages_state_created_at"
        "  ON messages(state, created_at)",
    ],
}


def _corrupt_message(path: str | Path, detail: object) -> str:
    """Build the actionable message carried by :class:`CorruptDatabaseError`."""
    return (
        "broker database is corrupt or unreadable: {detail}\n"
        "  path: {path}\n"
        "The broker will NOT recreate it automatically — that would silently "
        "discard the durable queue/registry/handoff state.\n"
        "If you have a backup, restore it. Otherwise re-run with --reset to move "
        "the corrupt file aside (as <db>.corrupt-<ts>) and start fresh:\n"
        "  it2agent-broker serve --reset".format(detail=detail, path=path)
    )


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) the sqlite db in WAL mode.

    Does not create the schema — call :func:`apply_schema` (or :func:`init_db`).
    Raises :class:`CorruptDatabaseError` (not a raw ``sqlite3.DatabaseError``) if
    the file is not a valid sqlite database — the first PRAGMA reads the header.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    # WAL for concurrent readers alongside a writer; busy timeout so a locked
    # write waits instead of raising immediately. These PRAGMAs touch the file
    # header, so a non-database file blows up here — translate that into a clear
    # CorruptDatabaseError rather than leaking the raw sqlite traceback.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise CorruptDatabaseError(_corrupt_message(p, exc)) from exc
    return conn


def check_integrity(conn: sqlite3.Connection) -> str:
    """Return the result of ``PRAGMA quick_check`` (``"ok"`` when healthy).

    ``quick_check`` is the cheaper cousin of ``integrity_check`` (it skips the
    slow index cross-checks) — fast enough to run on every startup. Callers can
    also use it to assert the db is still consistent after a failed write.
    """
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row is not None else ""


def reset_corrupt_db(path: str | Path) -> Optional[Path]:
    """Move a corrupt db (and its WAL sidecars) aside so a fresh one can open.

    The main file is *renamed* to ``<db>.corrupt-<epoch>`` (never deleted, so a
    later forensic recovery is possible) and the ``-wal``/``-shm`` sidecars are
    removed. Returns the backup path (``None`` if there was no file to move).
    This is a deliberate operator action (``serve --reset``), never automatic.
    """
    p = Path(path)
    backup: Optional[Path] = None
    if p.exists():
        backup = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
        p.rename(backup)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{p}{suffix}")
        try:
            side.unlink()
        except FileNotFoundError:
            pass
    return backup


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
    """Open the db, verify integrity, and apply the schema; return the connection.

    Raises :class:`CorruptDatabaseError` (with a clear, actionable message — not
    a raw traceback) if the file is not a readable, consistent sqlite database.
    Never recreates a corrupt file: recovery is an explicit operator action (see
    :func:`reset_corrupt_db`, wired to ``serve --reset``).
    """
    p = Path(path)
    conn = open_db(p)  # raises CorruptDatabaseError on an unreadable header
    try:
        status = check_integrity(conn).lower()
        if status != "ok":
            raise CorruptDatabaseError(
                _corrupt_message(p, f"quick_check reported: {status}")
            )
        apply_schema(conn)
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise CorruptDatabaseError(_corrupt_message(p, exc)) from exc
    except CorruptDatabaseError:
        conn.close()
        raise
    return conn
