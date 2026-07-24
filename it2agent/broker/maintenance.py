#!/usr/bin/env python3
"""Retention + vacuum maintenance for the it2agent broker (#133).

Pure — imports the stdlib ``sqlite3`` (via a live connection) and :mod:`dispatch`
for the op registry; no socket, no asyncio, no iTerm2. Same layering as
:mod:`mailbox`/:mod:`store`: the real logic lives in module-level functions that
take a live sqlite connection and are unit-tested with a throwaway db; the
``@register`` handlers at the bottom are thin wrappers, wired in when the server
imports this module.

Why this exists
---------------
The mailbox (``messages``) and the handoff history (``handoffs``) are both
**append-only**, so an it2agent instance in sustained use grows the db without
bound. This module adds a **retention policy** and a ``VACUUM`` so growth is
bounded, without ever weakening the mailbox's exactly-once guarantee.

Guarantees (what pruning MUST NOT break)
----------------------------------------
* **Only ACKED messages are prunable.** A ``pending``/``delivered`` (un-acked)
  message is never deleted, at any age — deleting it would drop an undelivered
  message. Retention only removes messages the recipient has already acked.
* **Age floor.** Even an acked message is only pruned once it is older than the
  retention window (``created_at`` older than ``max_age_days``). The default is
  conservative (:data:`DEFAULT_RETENTION_DAYS`).
* **Cursors and exactly-once are preserved.** ``ack_cursors`` rows are never
  touched, and ``messages.id`` is ``AUTOINCREMENT`` so ids are never reused after
  a delete. Deleting acked rows below a cursor therefore cannot cause a future
  message to be mis-acked or re-delivered — the observable send/poll/ack behavior
  is unchanged (acked rows would already be filtered out of every ``poll``).
* **Handoff cap is opt-in.** Capping handoff history to the last K versions per
  ``(agent_id, goal)`` DOES drop old versions (observable via
  ``handoff_history``), so it is off by default (``keep=None``) and only runs
  when a caller asks for it.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

from dispatch import BrokerContext, error, ok, register
from mailbox import ACKED

# Conservative default: keep a week of acked history before pruning. Un-acked
# messages are kept regardless of age.
DEFAULT_RETENTION_DAYS = 7
_SECONDS_PER_DAY = 86400


# --------------------------------------------------------------------------- #
# Pure maintenance logic (takes a live sqlite connection; no socket, no iTerm2).
# --------------------------------------------------------------------------- #


def prune_acked_messages(
    conn: sqlite3.Connection,
    max_age_days: float = DEFAULT_RETENTION_DAYS,
    now: Optional[float] = None,
) -> int:
    """Delete ACKED messages older than ``max_age_days``; return the count.

    Only rows in state ``acked`` with ``created_at`` strictly older than the
    cutoff are removed. Un-acked (``pending``/``delivered``) rows are never
    touched, and neither are ``ack_cursors``, so exactly-once delivery is intact.
    """
    ts = time.time() if now is None else now
    cutoff = ts - float(max_age_days) * _SECONDS_PER_DAY
    cur = conn.execute(
        "DELETE FROM messages WHERE state = ? AND created_at < ?",
        (ACKED, cutoff),
    )
    conn.commit()
    return int(cur.rowcount)


def cap_handoff_history(conn: sqlite3.Connection, keep: int) -> int:
    """Keep only the most-recent ``keep`` handoffs per ``(agent_id, goal)``.

    Deletes the older versions (lowest ids) beyond the last ``keep`` in each
    group; returns the number deleted. ``keep <= 0`` is treated as "no cap"
    (a no-op) so a caller can never accidentally wipe all history.
    """
    if keep <= 0:
        return 0
    cur = conn.execute(
        "DELETE FROM handoffs WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, ROW_NUMBER() OVER ("
        "      PARTITION BY agent_id, goal ORDER BY id DESC"
        "    ) AS rn FROM handoffs"
        "  ) WHERE rn > ?"
        ")",
        (int(keep),),
    )
    conn.commit()
    return int(cur.rowcount)


def _db_size_pages(conn: sqlite3.Connection) -> int:
    """Return the db's total page count (a proxy for on-disk file size)."""
    row = conn.execute("PRAGMA page_count").fetchone()
    return int(row[0]) if row is not None else 0


def vacuum(conn: sqlite3.Connection) -> dict[str, int]:
    """Reclaim free space by rebuilding the db; return before/after page counts.

    Checkpoints the WAL first so freed pages from recent deletes are folded into
    the main file, then runs ``VACUUM`` (which must run outside a transaction —
    the connection is in autocommit between committed ops, so this is safe). A
    final checkpoint folds the rebuilt db back into the main file and truncates
    the WAL, so the space is actually reclaimed on disk, not just logically.
    """
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _db_size_pages(conn)
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = _db_size_pages(conn)
    return {"pages_before": before, "pages_after": after}


# --------------------------------------------------------------------------- #
# Op handlers (#133). Thin wrappers: validate the request, delegate. Registered
# on import; the server picks them up by importing this module.
# --------------------------------------------------------------------------- #


def _optional_number(request: dict, key: str) -> tuple[Optional[float], Optional[dict]]:
    """Return ``(value, None)`` for an absent/non-negative number, else ``(None, err)``."""
    if key not in request or request[key] is None:
        return None, None
    value = request[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None, error("bad_request", f"'{key}' must be a non-negative number")
    return float(value), None


def _optional_int(request: dict, key: str) -> tuple[Optional[int], Optional[dict]]:
    """Return ``(value, None)`` for an absent/non-negative int, else ``(None, err)``."""
    if key not in request or request[key] is None:
        return None, None
    value = request[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, error("bad_request", f"'{key}' must be a non-negative integer")
    return value, None


@register("prune")
def _prune(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """``{op:"prune", max_age_days?, keep_handoffs?}`` → prune acked-old messages.

    ``max_age_days`` (default :data:`DEFAULT_RETENTION_DAYS`) is the age floor;
    only acked messages older than it are removed. ``keep_handoffs`` (optional)
    caps handoff history to the last K versions per ``(agent_id, goal)``. Un-acked
    messages, ack cursors, and exactly-once delivery are always preserved.
    """
    max_age_days, err = _optional_number(request, "max_age_days")
    if err:
        return err
    if max_age_days is None:
        max_age_days = float(DEFAULT_RETENTION_DAYS)
    keep_handoffs, err = _optional_int(request, "keep_handoffs")
    if err:
        return err
    pruned_messages = prune_acked_messages(ctx.conn, max_age_days=max_age_days)
    pruned_handoffs = (
        cap_handoff_history(ctx.conn, keep_handoffs) if keep_handoffs is not None else 0
    )
    return ok(
        pruned_messages=pruned_messages,
        pruned_handoffs=pruned_handoffs,
        max_age_days=max_age_days,
    )


@register("vacuum")
def _vacuum(request: dict, ctx: BrokerContext) -> dict[str, Any]:
    """``{op:"vacuum"}`` → reclaim free space; report before/after page counts."""
    result = vacuum(ctx.conn)
    return ok(**result)
