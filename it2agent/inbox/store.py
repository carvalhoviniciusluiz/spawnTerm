#!/usr/bin/env python3
"""Durable inbox queue, backed by the broker mailbox (#17 over #4/#35).

We do **not** reinvent transport or state — the inbox rides the broker's durable
per-agent mailbox (``send`` / ``poll`` / ``ack``), reusing its sqlite durability,
monotonic ids, and at-least-once replay. That is the *simplest correct* option:
adding a new broker table/op would duplicate machinery the mailbox already
provides, and the mailbox is already durable across a broker restart.

**Why an event log, not a status column.** A human decides requests in *any*
order (approve #5 before #3). The mailbox ``ack`` is *up-to-cursor* — acking #5
would also ack #3 — so a single stream with ack-as-resolved would be wrong. We
instead keep **two append-only streams** and reconcile client-side:

* ``agent.inbox.requests``  — one message per submitted request. The broker
  message id *is* the request id (monotonic, unique).
* ``agent.inbox.decisions`` — one message per decision, body carries the
  ``request_id`` it resolves.

The pending queue is ``requests`` whose id has no matching decision — correct
regardless of decision order. :meth:`InboxStore.compact` acks the contiguous
fully-resolved prefix of the requests stream to bound growth (safe: a prefix ack
never touches a still-pending higher id). Decisions are retained (an orphan
decision whose request was compacted away simply matches nothing).

Decisions are also pushed back to the requesting agent via its own mailbox
(``notify_agent``), closing the loop.

**Graceful degradation.** The store talks to anything exposing
``request(message) -> dict`` — the real :class:`client.BrokerClient` or the
in-process :class:`InMemoryBroker` here. When the real broker is unreachable the
client raises ``OSError``; :meth:`InboxStore` re-raises it as
:class:`BrokerUnavailable` so callers fail with a clear message, and the CLI can
fall back to an :class:`InMemoryBroker` (durable only for the life of the process
— documented as such). The module imports no ``iterm2`` and no broker code; the
broker client is injected.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from model import DecisionRecord, InboxRequest

REQUESTS_ADDR = "agent.inbox.requests"
DECISIONS_ADDR = "agent.inbox.decisions"
INBOX_SENDER = "agent.inbox"


class BrokerUnavailable(RuntimeError):
    """Raised when the durable broker cannot be reached. Degrade or report."""


class InMemoryBroker:
    """In-process twin of the broker mailbox: implements ``request(msg)``.

    A faithful re-implementation of the ``send`` / ``poll`` / ``ack`` semantics
    (monotonic ids, per-recipient FIFO, replay of un-acked rows, up-to-cursor
    ack) so it serves as both the unit-test double *and* the non-durable
    degraded fallback when the real broker is down. No sqlite, no socket.
    """

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []
        self._cursors: dict[str, int] = {}
        self._next_id = 1

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        op = message.get("op")
        if op == "ping":
            return {"ok": True, "pong": True}
        if op == "health":
            return {"ok": True, "schema_version": None, "ops": ["send", "poll", "ack"]}
        if op == "send":
            return self._send(message)
        if op in ("poll", "fetch"):
            return self._poll(message)
        if op == "ack":
            return self._ack(message)
        return {"ok": False, "error": {"code": "unknown_op", "message": f"unknown op: {op}"}}

    def _send(self, message: dict[str, Any]) -> dict[str, Any]:
        mid = self._next_id
        self._next_id += 1
        self._messages.append(
            {
                "id": mid,
                "from": message.get("from", ""),
                "to": message.get("to", ""),
                "body": message.get("body", ""),
                "created_at": time.time(),
                "state": "pending",
            }
        )
        return {"ok": True, "id": mid}

    def _poll(self, message: dict[str, Any]) -> dict[str, Any]:
        recipient = message.get("agent")
        since = message.get("since") or 0
        out: list[dict[str, Any]] = []
        for row in self._messages:
            if row["to"] != recipient or row["id"] <= since or row["state"] == "acked":
                continue
            if row["state"] == "pending":
                row["state"] = "delivered"
            out.append(dict(row))
        return {"ok": True, "messages": out, "count": len(out)}

    def _ack(self, message: dict[str, Any]) -> dict[str, Any]:
        agent = message.get("agent")
        msg_id = message.get("msg_id", 0)
        acked = 0
        for row in self._messages:
            if row["to"] == agent and row["id"] <= msg_id and row["state"] != "acked":
                row["state"] = "acked"
                acked += 1
        cursor = max(self._cursors.get(agent, 0), msg_id)
        self._cursors[agent] = cursor
        return {"ok": True, "acked": acked, "cursor": cursor}


class InboxStore:
    """The durable queue: enqueue requests, reconcile pending, record decisions.

    ``broker`` is any object with ``request(message) -> dict`` (the real
    :class:`client.BrokerClient` or :class:`InMemoryBroker`). ``durable`` is a
    hint for callers/UX — False when running on the in-memory fallback.
    """

    def __init__(self, broker: Any, durable: bool = True) -> None:
        self.broker = broker
        self.durable = durable

    # -- broker plumbing --------------------------------------------------- #

    def _request(self, message: dict[str, Any]) -> dict[str, Any]:
        """One broker round-trip; map transport failure to BrokerUnavailable."""
        try:
            reply = self.broker.request(message)
        except OSError as exc:
            raise BrokerUnavailable(f"broker unreachable: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - protocol errors, closed sockets, etc.
            raise BrokerUnavailable(f"broker error: {exc}") from exc
        if not isinstance(reply, dict) or not reply.get("ok"):
            err = reply.get("error") if isinstance(reply, dict) else reply
            raise BrokerUnavailable(f"broker rejected {message.get('op')!r}: {err}")
        return reply

    def _send(self, to: str, body: dict[str, Any]) -> int:
        reply = self._request(
            {"op": "send", "to": to, "from": INBOX_SENDER, "body": json.dumps(body)}
        )
        return int(reply["id"])

    def _poll(self, addr: str) -> list[dict[str, Any]]:
        reply = self._request({"op": "poll", "agent": addr})
        return reply.get("messages", [])

    def _ack(self, addr: str, msg_id: int) -> None:
        if msg_id > 0:
            self._request({"op": "ack", "agent": addr, "msg_id": msg_id})

    @staticmethod
    def _decode(body: str) -> dict[str, Any]:
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- intake ------------------------------------------------------------ #

    def enqueue(self, request: InboxRequest) -> InboxRequest:
        """Persist a request; the broker message id becomes ``request.id``."""
        request.id = self._send(REQUESTS_ADDR, request.to_dict())
        return request

    # -- reads ------------------------------------------------------------- #

    def _all_requests(self) -> list[InboxRequest]:
        return [
            InboxRequest.from_dict(self._decode(m["body"]), id=m["id"])
            for m in self._poll(REQUESTS_ADDR)
        ]

    def _decided_ids(self) -> set[int]:
        ids: set[int] = set()
        for m in self._poll(DECISIONS_ADDR):
            rid = self._decode(m["body"]).get("request_id")
            if isinstance(rid, int):
                ids.add(rid)
        return ids

    def list_pending(self) -> list[InboxRequest]:
        """Requests with no recorded decision yet, oldest first."""
        decided = self._decided_ids()
        pending = [r for r in self._all_requests() if r.id not in decided]
        pending.sort(key=lambda r: r.id or 0)
        return pending

    def get(self, request_id: int) -> Optional[InboxRequest]:
        """Return one request by id (pending or already decided), or ``None``."""
        for r in self._all_requests():
            if r.id == request_id:
                return r
        return None

    def decisions(self) -> list[DecisionRecord]:
        """Every recorded decision, oldest first."""
        out = [DecisionRecord.from_dict(self._decode(m["body"])) for m in self._poll(DECISIONS_ADDR)]
        return out

    # -- decisions --------------------------------------------------------- #

    def record_decision(self, record: DecisionRecord, compact: bool = True) -> DecisionRecord:
        """Append a decision to the decisions stream; optionally compact."""
        self._send(DECISIONS_ADDR, record.to_dict())
        if compact:
            self.compact()
        return record

    def notify_agent(self, agent: str, record: DecisionRecord) -> int:
        """Push a decision back to the requesting agent's own mailbox."""
        return self._send(agent, {"kind": "inbox_decision", **record.to_dict()})

    def compact(self) -> int:
        """Ack the contiguous fully-resolved prefix of the requests stream.

        Returns the watermark acked (0 if nothing). Safe by construction: only a
        prefix where *every* lower request id is decided is acked, so a
        still-pending higher id is never removed. Bounds unbounded replay growth.
        """
        decided = self._decided_ids()
        req_ids = sorted(r.id for r in self._all_requests() if r.id is not None)
        watermark = 0
        for rid in req_ids:
            if rid in decided:
                watermark = rid
            else:
                break
        if watermark:
            self._ack(REQUESTS_ADDR, watermark)
        return watermark
