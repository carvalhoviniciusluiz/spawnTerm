#!/usr/bin/env python3
"""Registry + handoff/state store for the it2agent broker (#36).

The broker's two **persistent** stores, the durable state iTerm2 deliberately
lacks (see ``it2agent/docs/design.md`` — "What iTerm2 CANNOT do"):

* **agents** — a queryable registry keyed by ``session_id`` (``role``, ``task``,
  ``capabilities``, ``last_seen``, ``alive``). Unlike the daemon's *ephemeral*
  registry (#26), this one lives in sqlite and **survives a broker restart**.
* **handoffs** — an **append-only history** of state handoffs per agent/goal.
  Each ``handoff_put`` appends a new row with a monotonic ``id``; a read returns
  the **latest** version, and the full ordered history on request.

Pure — imports :mod:`schema` (stdlib sqlite) and :mod:`dispatch` (the op
registry); no socket, no asyncio, no iTerm2. All real logic (SQL, query filters,
latest-vs-history selection) lives in module-level functions that take a live
sqlite connection, so they are unit-tested without a server. The ``@register``
handlers at the bottom are thin wrappers: validate the request, call a pure
function, wrap the result in :func:`dispatch.ok` / :func:`dispatch.error`.

Wiring: importing this module runs the ``@register`` decorators, so the server
imports it once (a single additive import) and every op below becomes routable.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import sqlite3

from dispatch import BrokerContext, error, ok, register


# --------------------------------------------------------------------------- #
# Row <-> dict helpers
# --------------------------------------------------------------------------- #


def _load_json_list(text: Optional[str]) -> list:
    """Decode a stored JSON-array column back to a list ([] for null/blank)."""
    if not text:
        return []
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _dump_json_list(value: Any) -> str:
    """Normalize a list-ish value to a stable JSON-array string ([] if absent)."""
    items = list(value) if isinstance(value, (list, tuple)) else []
    return json.dumps(items, separators=(",", ":"), sort_keys=False)


def _agent_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Materialize an ``agents`` row as a JSON-friendly dict."""
    return {
        "session_id": row["session_id"],
        "role": row["role"],
        "task": row["task"],
        "capabilities": _load_json_list(row["capabilities"]),
        "last_seen": row["last_seen"],
        "alive": bool(row["alive"]),
    }


def _handoff_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Materialize a ``handoffs`` row as a JSON-friendly dict."""
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "goal": row["goal"],
        "context_ptr": row["context_ptr"],
        "owned_files": _load_json_list(row["owned_files"]),
        "verification_status": row["verification_status"],
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------- #
# Registry: pure logic
# --------------------------------------------------------------------------- #


def upsert_agent(
    conn: sqlite3.Connection,
    session_id: str,
    role: Optional[str] = None,
    task: Optional[str] = None,
    capabilities: Any = None,
    alive: bool = True,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Insert or replace the registry entry for ``session_id``; return it.

    Idempotent per ``session_id`` (the primary key): re-registering the same
    session overwrites role/task/capabilities/alive and refreshes ``last_seen``.
    """
    ts = time.time() if now is None else now
    conn.execute(
        "INSERT INTO agents(session_id, role, task, capabilities, last_seen, alive) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "  role=excluded.role, task=excluded.task, "
        "  capabilities=excluded.capabilities, last_seen=excluded.last_seen, "
        "  alive=excluded.alive",
        (session_id, role, task, _dump_json_list(capabilities), ts, 1 if alive else 0),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM agents WHERE session_id = ?", (session_id,)
    ).fetchone()
    return _agent_to_dict(row)


def touch_agent(
    conn: sqlite3.Connection,
    session_id: str,
    alive: bool = True,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Liveness update: refresh ``last_seen`` (and ``alive``) for an agent.

    Returns the updated agent, or ``None`` if no such session is registered.
    """
    ts = time.time() if now is None else now
    cur = conn.execute(
        "UPDATE agents SET last_seen = ?, alive = ? WHERE session_id = ?",
        (ts, 1 if alive else 0, session_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT * FROM agents WHERE session_id = ?", (session_id,)
    ).fetchone()
    return _agent_to_dict(row)


def query_agents(
    conn: sqlite3.Connection,
    role: Optional[str] = None,
    alive: Optional[bool] = None,
    capability: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return registered agents matching the given filters (AND-combined).

    ``role`` and ``alive`` are filtered in SQL; ``capability`` is matched in
    Python against the decoded JSON ``capabilities`` list (membership). All
    filters are optional — no filters returns every agent. Ordered by
    ``last_seen`` descending (most-recently-seen first), then ``session_id``.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if role is not None:
        clauses.append("role = ?")
        params.append(role)
    if alive is not None:
        clauses.append("alive = ?")
        params.append(1 if alive else 0)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = "SELECT * FROM agents" + where + " ORDER BY last_seen DESC, session_id ASC"
    rows = conn.execute(sql, params).fetchall()
    agents = [_agent_to_dict(row) for row in rows]
    if capability is not None:
        agents = [a for a in agents if capability in a["capabilities"]]
    return agents


# --------------------------------------------------------------------------- #
# Handoff: pure logic (append-only history)
# --------------------------------------------------------------------------- #


def put_handoff(
    conn: sqlite3.Connection,
    agent_id: str,
    goal: str,
    context_ptr: Optional[str] = None,
    owned_files: Any = None,
    verification_status: Optional[str] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Append a new handoff version for ``(agent_id, goal)``; return the row.

    Never updates in place — each call inserts a fresh row with a monotonic
    ``id``, preserving the full history.
    """
    ts = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO handoffs("
        "  agent_id, goal, context_ptr, owned_files, verification_status, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            agent_id,
            goal,
            context_ptr,
            _dump_json_list(owned_files),
            verification_status,
            ts,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM handoffs WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return _handoff_to_dict(row)


def get_handoff(
    conn: sqlite3.Connection,
    agent_id: str,
    goal: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return the **latest** handoff for an agent (optionally scoped to ``goal``).

    "Latest" == highest ``id`` (monotonic insert order). Returns ``None`` when
    the agent (or agent/goal pair) has no handoffs.
    """
    if goal is not None:
        row = conn.execute(
            "SELECT * FROM handoffs WHERE agent_id = ? AND goal = ? "
            "ORDER BY id DESC LIMIT 1",
            (agent_id, goal),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM handoffs WHERE agent_id = ? ORDER BY id DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
    return _handoff_to_dict(row) if row is not None else None


def handoff_history(
    conn: sqlite3.Connection,
    agent_id: str,
    goal: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return **all** handoff versions for an agent, oldest → newest (by ``id``).

    Optionally scoped to a single ``goal``.
    """
    if goal is not None:
        rows = conn.execute(
            "SELECT * FROM handoffs WHERE agent_id = ? AND goal = ? ORDER BY id ASC",
            (agent_id, goal),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM handoffs WHERE agent_id = ? ORDER BY id ASC",
            (agent_id,),
        ).fetchall()
    return [_handoff_to_dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Request validation helpers
# --------------------------------------------------------------------------- #


def _require_str(request: dict, field: str) -> str:
    """Extract a required non-empty string field or raise ``ValueError``."""
    value = request.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or non-string '{field}' field")
    return value


def _optional_str(request: dict, field: str) -> Optional[str]:
    """Extract an optional string field (``None`` if absent); reject wrong type."""
    if field not in request or request[field] is None:
        return None
    value = request[field]
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string")
    return value


def _optional_bool(request: dict, field: str) -> Optional[bool]:
    """Extract an optional boolean field (``None`` if absent); reject wrong type."""
    if field not in request or request[field] is None:
        return None
    value = request[field]
    if not isinstance(value, bool):
        raise ValueError(f"'{field}' must be a boolean")
    return value


# --------------------------------------------------------------------------- #
# Ops (#36). Thin wrappers over the pure functions above.
# --------------------------------------------------------------------------- #


@register("register")
def _register(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """Registry upsert: ``{op:"register", session_id, role?, task?, capabilities?, alive?}``."""
    try:
        session_id = _require_str(request, "session_id")
    except ValueError as exc:
        return error("bad_request", str(exc))
    alive = request.get("alive", True)
    if not isinstance(alive, bool):
        return error("bad_request", "'alive' must be a boolean")
    agent = upsert_agent(
        ctx.conn,
        session_id=session_id,
        role=request.get("role"),
        task=request.get("task"),
        capabilities=request.get("capabilities"),
        alive=alive,
    )
    return ok(agent=agent)


@register("query")
def _query(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """Registry query: ``{op:"query", role?, alive?, capability?}`` → matching agents."""
    try:
        role = _optional_str(request, "role")
        alive = _optional_bool(request, "alive")
        capability = _optional_str(request, "capability")
    except ValueError as exc:
        return error("bad_request", str(exc))
    agents = query_agents(ctx.conn, role=role, alive=alive, capability=capability)
    return ok(agents=agents, count=len(agents))


@register("touch")
def _touch(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """Liveness update: ``{op:"touch", session_id, alive?}`` refreshes ``last_seen``."""
    try:
        session_id = _require_str(request, "session_id")
    except ValueError as exc:
        return error("bad_request", str(exc))
    alive = request.get("alive", True)
    if not isinstance(alive, bool):
        return error("bad_request", "'alive' must be a boolean")
    agent = touch_agent(ctx.conn, session_id=session_id, alive=alive)
    if agent is None:
        return error("not_found", f"no such agent: {session_id}", session_id=session_id)
    return ok(agent=agent)


@register("handoff_put")
def _handoff_put(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """Append a handoff version: ``{op:"handoff_put", agent_id, goal, context_ptr?, owned_files?, verification_status?}``."""
    try:
        agent_id = _require_str(request, "agent_id")
        goal = _require_str(request, "goal")
        context_ptr = _optional_str(request, "context_ptr")
        verification_status = _optional_str(request, "verification_status")
    except ValueError as exc:
        return error("bad_request", str(exc))
    handoff = put_handoff(
        ctx.conn,
        agent_id=agent_id,
        goal=goal,
        context_ptr=context_ptr,
        owned_files=request.get("owned_files"),
        verification_status=verification_status,
    )
    return ok(handoff=handoff)


@register("handoff_get")
def _handoff_get(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """Latest handoff: ``{op:"handoff_get", agent_id, goal?}`` → newest version (or null)."""
    try:
        agent_id = _require_str(request, "agent_id")
        goal = _optional_str(request, "goal")
    except ValueError as exc:
        return error("bad_request", str(exc))
    handoff = get_handoff(ctx.conn, agent_id=agent_id, goal=goal)
    return ok(handoff=handoff)


@register("handoff_history")
def _handoff_history(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """Full history: ``{op:"handoff_history", agent_id, goal?}`` → all versions oldest→newest."""
    try:
        agent_id = _require_str(request, "agent_id")
        goal = _optional_str(request, "goal")
    except ValueError as exc:
        return error("bad_request", str(exc))
    history = handoff_history(ctx.conn, agent_id=agent_id, goal=goal)
    return ok(handoffs=history, count=len(history))
