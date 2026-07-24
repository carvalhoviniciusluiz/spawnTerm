#!/usr/bin/env python3
"""Op-dispatch for the it2agent broker (#34).

Pure — imports :mod:`schema` (stdlib sqlite) for the health probe; no socket,
no asyncio, no iTerm2. This is the extensible request→response core: the socket
server (:mod:`server`) is a thin transport that decodes a line, calls
:func:`handle`, and encodes the result.

**Extensibility.** Handlers live in a registry keyed by op name. #35/#36/#37 add
``send`` / ``poll`` / ``ack`` / ``register`` / ``query`` / ``handoff`` by
importing this module and decorating a function::

    from dispatch import register, ok, error

    @register("send")
    def _send(request, ctx):
        ...
        return ok(id=row_id)

No restructuring of the server is needed. A handler receives the decoded request
dict and a :class:`BrokerContext` (the live sqlite connection + resolved paths)
and returns a response dict. Unknown ops and handler exceptions become
structured error responses — the server never crashes on bad input.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional

import schema

# A handler: (request_object, context) -> response_object.
Handler = Callable[[dict, "BrokerContext"], dict]


@dataclass
class BrokerContext:
    """State handed to every op handler.

    ``conn`` is the live sqlite connection (``None`` only in narrow unit tests
    that exercise ops needing no db). ``db_path`` / ``sock_path`` are the
    resolved per-user locations, surfaced by ``health`` for diagnostics.
    """

    conn: Any = None  # sqlite3.Connection | None
    db_path: str = ""
    sock_path: Optional[str] = None


# Registry of op name -> handler. Insertion order is preserved so ``health``
# can report the available ops deterministically.
HANDLERS: dict[str, Handler] = {}


def register(op: str) -> Callable[[Handler], Handler]:
    """Decorator: register ``func`` as the handler for op ``op``."""

    def wrap(func: Handler) -> Handler:
        if op in HANDLERS:
            raise ValueError(f"duplicate op registration: {op}")
        HANDLERS[op] = func
        return func

    return wrap


def ok(**fields: Any) -> dict[str, Any]:
    """Build a success response: ``{"ok": true, **fields}``."""
    response: dict[str, Any] = {"ok": True}
    response.update(fields)
    return response


def error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured error response.

    ``{"ok": false, "error": {"code": code, "message": message, **extra}}``.
    """
    payload: dict[str, Any] = {"code": code, "message": message}
    payload.update(extra)
    return {"ok": False, "error": payload}


def _rollback(ctx: "BrokerContext") -> None:
    """Best-effort rollback of a half-applied write so the db stays consistent.

    On any handler failure we discard the current (possibly partial) transaction
    rather than leave the shared connection stuck mid-write. WAL guarantees the
    last committed state is intact, so rolling back an uncommitted write leaves
    the on-disk db consistent (``PRAGMA quick_check`` still ``ok``).
    """
    conn = getattr(ctx, "conn", None) if ctx is not None else None
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 - rollback itself must never crash the server
        pass


def handle(request: Any, ctx: "BrokerContext") -> dict[str, Any]:
    """Route one decoded request to its handler; never raises.

    Bad shape → ``bad_request``. Unknown op → ``unknown_op``. A storage failure
    (disk full, I/O error, locked db — any ``sqlite3.Error``) → ``storage``,
    after rolling the partial write back so the db is not corrupted and the
    process stays up. Any other handler exception → ``internal``. All of these
    are ``ok:false`` responses — the op fails cleanly, the server survives.
    """
    if not isinstance(request, dict):
        return error("bad_request", "request must be a JSON object")
    op = request.get("op")
    if not isinstance(op, str) or not op:
        return error("bad_request", "missing or non-string 'op' field")
    func = HANDLERS.get(op)
    if func is None:
        return error("unknown_op", f"unknown op: {op}", op=op)
    try:
        return func(request, ctx)
    except sqlite3.Error as exc:
        # Storage-layer failure (e.g. disk full → "database or disk is full",
        # a read-only db, an I/O error). Fail the OP cleanly, never the process,
        # and never a partial write: roll back so the db stays consistent.
        _rollback(ctx)
        return error("storage", f"{type(exc).__name__}: {exc}", op=op)
    except Exception as exc:  # noqa: BLE001 - never crash the server on a handler bug
        _rollback(ctx)
        return error("internal", f"{type(exc).__name__}: {exc}", op=op)


# --------------------------------------------------------------------------- #
# Base ops (#34). Mailbox/registry/handoff ops arrive in #35/#36/#37.
# --------------------------------------------------------------------------- #


@register("ping")
def _ping(request: dict, ctx: "BrokerContext") -> dict[str, Any]:
    """Liveness probe. Echoes an optional ``echo`` value back under ``echo``."""
    response = ok(pong=True)
    if "echo" in request:
        response["echo"] = request["echo"]
    return response


@register("health")
def _health(request: dict, ctx: "BrokerContext") -> dict[str, Any]:
    """Report schema version, db/socket paths, and the registered ops."""
    version = schema.current_version(ctx.conn) if ctx.conn is not None else None
    return ok(
        schema_version=version,
        db=ctx.db_path,
        sock=ctx.sock_path,
        ops=list(HANDLERS.keys()),
    )
