#!/usr/bin/env python3
"""Shared data types for the it2agent human-attention inbox (#17).

Pure — imports only the stdlib; no socket, no broker, no iTerm2. These are the
value objects that flow through the whole inbox: the policy engine classifies an
:class:`InboxRequest`, the store persists it (durably, via the broker), and a
human records a :class:`DecisionRecord` against it.

An **action descriptor** is the shape the LangChain Agent-Inbox policy model
reasons over: *what* the agent wants to do (``action``), plus the three axes that
gate it — is it ``reversible``, what is its ``scope``, and what does it ``cost``.
Everything else is context (which pane raised it, which agent, a human-readable
summary, and an opaque ``payload`` the agent may attach).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class Decision(str, Enum):
    """The policy verdict over an action descriptor.

    * ``AUTO_APPROVE`` — safe/read-only allow-listed action; no human needed.
    * ``NEEDS_HUMAN``  — must be queued for a human to approve/edit/reject.
    * ``BLOCK``        — refused outright; never reaches a human.
    """

    AUTO_APPROVE = "auto_approve"
    NEEDS_HUMAN = "needs_human"
    BLOCK = "block"


class Verdict(str, Enum):
    """The recorded outcome of a request (by policy or by a human)."""

    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"
    BLOCKED = "blocked"


# Conservative defaults: assume an action is irreversible until proven otherwise,
# and belongs to the (non-read) workspace scope. An agent that knows better
# overrides these when it submits.
DEFAULT_SCOPE = "workspace"


@dataclass
class InboxRequest:
    """An agent's request to take an action, carrying its action descriptor.

    ``id`` is assigned by the store on enqueue (it is the broker message id — a
    monotonic, unique handle). ``reversible``/``scope``/``cost`` are the policy
    axes; ``session`` is the iTerm2 pane to raise attention to; ``agent`` is the
    requester to notify back with the decision.
    """

    action: str
    reversible: bool = False
    scope: str = DEFAULT_SCOPE
    cost: float = 0.0
    session: Optional[str] = None
    agent: Optional[str] = None
    summary: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict. ``id`` is omitted (the store owns it)."""
        data = asdict(self)
        data.pop("id", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], id: Optional[int] = None) -> "InboxRequest":
        """Rebuild a request from a stored dict, attaching a broker ``id``."""
        return cls(
            action=data.get("action", ""),
            reversible=bool(data.get("reversible", False)),
            scope=data.get("scope", DEFAULT_SCOPE),
            cost=float(data.get("cost", 0.0)),
            session=data.get("session"),
            agent=data.get("agent"),
            summary=data.get("summary"),
            payload=data.get("payload") or {},
            id=id,
        )


@dataclass
class DecisionRecord:
    """A decision recorded against a request, by policy or a human.

    ``edited_request`` carries the human's modified action descriptor when the
    verdict is :attr:`Verdict.EDITED` (approve-with-changes); otherwise ``None``.
    """

    request_id: int
    verdict: str
    note: Optional[str] = None
    edited_request: Optional[dict[str, Any]] = None
    decided_by: str = "human"
    decided_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionRecord":
        return cls(
            request_id=int(data.get("request_id")),
            verdict=data.get("verdict", ""),
            note=data.get("note"),
            edited_request=data.get("edited_request"),
            decided_by=data.get("decided_by", "human"),
            decided_at=float(data.get("decided_at", 0.0)),
        )
