#!/usr/bin/env python3
"""Durable per-agent mailbox for the it2agent broker (#35, Tier 2.2).

Pure — imports the stdlib ``sqlite3`` (indirectly, via a live connection) and
:mod:`dispatch` for the op registry; no socket, no asyncio, no iTerm2. This is
**the** it2agent differentiator: tmux ``send-keys`` fanout is ~70-80% reliable
and fire-and-forget; this is a db-backed queue with acknowledgement, so a
message is durable across a broker restart and is re-delivered until the
recipient acks it (see ``it2agent/docs/design.md`` — "What iTerm2 CANNOT do").

Layering mirrors the rest of the broker: the durable logic lives in pure
functions that take a live sqlite connection (``send_message`` / ``poll_messages``
/ ``ack_messages`` / ``ack_cursor``) and are unit-tested with a throwaway db and
no socket. The ``@register`` handlers at the bottom are thin wrappers that
validate the request shape and delegate. The server wires these ops in by simply
importing this module (its decorators run on import).

Semantics
---------
* **Ordering** — strict per-recipient FIFO by the monotonic ``messages.id``
  (``INTEGER PRIMARY KEY AUTOINCREMENT``). ``poll`` always returns a recipient's
  messages ordered by ascending id.
* **States** — a message moves ``pending`` → ``delivered`` → ``acked``. ``poll``
  promotes the ``pending`` rows it returns to ``delivered``; ``ack`` moves rows
  up to a cursor to ``acked``.
* **Replay** — ``poll`` returns every *un-acked* row (``pending`` **or**
  ``delivered``). A message that was delivered but never acked is therefore
  re-returned on the next ``poll`` — at-least-once delivery until it is acked.
* **Ack cursor** — ``ack(agent, msg_id)`` is *up-to-cursor*: it acks every one of
  ``agent``'s messages with ``id <= msg_id`` and advances the per-agent
  high-water cursor to ``max(existing, msg_id)`` (never rewinds). Acking is
  idempotent — re-acking the same id acks nothing new and leaves the cursor put.
* **Idempotent send** — we do **not** dedup by content: every ``send`` appends a
  new row with a fresh id. Exactly-once is guaranteed at the **ack layer**, not
  the send layer: an acked message (``id <= cursor``) is never returned again, so
  delivery is exactly-once *per cursor+ack*. A caller that wants to suppress
  duplicate work should ack.
* **Durability** — everything is committed sqlite state (WAL); nothing is held in
  memory, so no message is lost across a broker restart.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

from dispatch import BrokerContext, error, ok, register

# Message lifecycle states.
PENDING = "pending"
DELIVERED = "delivered"
ACKED = "acked"


# --------------------------------------------------------------------------- #
# Pure durable logic (takes a live sqlite connection; no socket, no iTerm2).
# --------------------------------------------------------------------------- #


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    """Shape a ``messages`` row as a wire message dict (mirrors the send fields)."""
    return {
        "id": row["id"],
        "from": row["sender"],
        "to": row["recipient"],
        "body": row["body"],
        "created_at": row["created_at"],
        "state": row["state"],
    }


def send_message(
    conn: sqlite3.Connection,
    sender: str,
    recipient: str,
    body: str,
    created_at: Optional[float] = None,
) -> int:
    """Append one message to ``recipient``'s queue; return its monotonic id.

    Always creates a new row (no content dedup) — see the module docstring on
    idempotency. The returned id is the FIFO ordering key.
    """
    ts = time.time() if created_at is None else created_at
    cursor = conn.execute(
        "INSERT INTO messages(sender, recipient, body, created_at, state) "
        "VALUES (?, ?, ?, ?, ?)",
        (sender, recipient, body, ts, PENDING),
    )
    conn.commit()
    return int(cursor.lastrowid)


def poll_messages(
    conn: sqlite3.Connection,
    recipient: str,
    since: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Return ``recipient``'s un-acked messages, ordered by id; mark them delivered.

    ``since`` is an optional exclusive id floor (return only ``id > since``);
    when ``None`` the floor is 0, so every un-acked message replays. Rows still
    in ``pending`` are promoted to ``delivered`` as part of the poll; already
    ``delivered`` rows are returned again (replay) until acked.
    """
    floor = 0 if since is None else int(since)
    rows = conn.execute(
        "SELECT id, sender, recipient, body, created_at, state FROM messages "
        "WHERE recipient = ? AND id > ? AND state != ? ORDER BY id",
        (recipient, floor, ACKED),
    ).fetchall()
    if rows:
        # Promote the pending ones we are about to hand out to delivered. The
        # WHERE mirrors the SELECT so we never touch rows we did not return.
        conn.execute(
            "UPDATE messages SET state = ? "
            "WHERE recipient = ? AND id > ? AND state = ?",
            (DELIVERED, recipient, floor, PENDING),
        )
        conn.commit()
    messages = []
    for row in rows:
        message = _row_to_message(row)
        # Everything returned is now (at least) delivered — reflect that.
        message["state"] = DELIVERED
        messages.append(message)
    return messages


def ack_cursor(conn: sqlite3.Connection, agent: str) -> int:
    """Return ``agent``'s high-water acked id (0 if it has never acked)."""
    row = conn.execute(
        "SELECT cursor FROM ack_cursors WHERE agent = ?", (agent,)
    ).fetchone()
    return int(row[0]) if row is not None else 0


def ack_messages(
    conn: sqlite3.Connection,
    agent: str,
    msg_id: int,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Ack all of ``agent``'s messages with ``id <= msg_id``; advance the cursor.

    Idempotent and monotonic: acks only rows not already acked, and moves the
    per-agent cursor to ``max(existing, msg_id)`` (never rewinds). Returns the
    number newly acked and the resulting cursor.
    """
    target = int(msg_id)
    ts = time.time() if now is None else now
    acked = conn.execute(
        "UPDATE messages SET state = ? "
        "WHERE recipient = ? AND id <= ? AND state != ?",
        (ACKED, agent, target, ACKED),
    ).rowcount
    # Upsert the cursor, never letting it move backwards.
    conn.execute(
        "INSERT INTO ack_cursors(agent, cursor, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(agent) DO UPDATE SET "
        "  cursor = MAX(cursor, excluded.cursor), updated_at = excluded.updated_at",
        (agent, target, ts),
    )
    conn.commit()
    return {"acked": int(acked), "cursor": ack_cursor(conn, agent)}


# --------------------------------------------------------------------------- #
# Op handlers (#35). Thin wrappers: validate the request, delegate to the pure
# functions above. Registered on import; the server picks them up by importing
# this module. No per-op flag gate — the server already gates on agent.broker.
# --------------------------------------------------------------------------- #


def _require_str(request: dict, key: str) -> tuple[Optional[str], Optional[dict]]:
    """Return ``(value, None)`` for a present non-empty string, else ``(None, err)``."""
    value = request.get(key)
    if not isinstance(value, str) or not value:
        return None, error("bad_request", f"missing or non-string '{key}' field")
    return value, None


@register("send")
def _send(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """``{op:"send", to, from, body}`` → append a message, return its id."""
    recipient, err = _require_str(request, "to")
    if err:
        return err
    sender, err = _require_str(request, "from")
    if err:
        return err
    body = request.get("body")
    if not isinstance(body, str):
        return error("bad_request", "missing or non-string 'body' field")
    msg_id = send_message(ctx.conn, sender=sender, recipient=recipient, body=body)
    return ok(id=msg_id)


@register("poll")
@register("fetch")
def _poll(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """``{op:"poll", agent, since?}`` → un-acked messages, ordered by id (replay)."""
    agent, err = _require_str(request, "agent")
    if err:
        return err
    since = request.get("since")
    if since is not None and (not isinstance(since, int) or isinstance(since, bool) or since < 0):
        return error("bad_request", "'since' must be a non-negative integer")
    messages = poll_messages(ctx.conn, agent, since=since)
    return ok(messages=messages, count=len(messages))


@register("ack")
def _ack(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """``{op:"ack", agent, msg_id}`` → ack up to msg_id, advance the cursor."""
    agent, err = _require_str(request, "agent")
    if err:
        return err
    msg_id = request.get("msg_id")
    if not isinstance(msg_id, int) or isinstance(msg_id, bool) or msg_id < 0:
        return error("bad_request", "'msg_id' must be a non-negative integer")
    result = ack_messages(ctx.conn, agent, msg_id)
    return ok(**result)
