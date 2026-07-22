#!/usr/bin/env python3
"""In-memory best-effort message router for the it2agent daemon (Tier 1.3, #28).

This module is **pure**: no top-level ``iterm2`` import and no iTerm2 I/O, so it
imports and unit-tests without a running iTerm2. It is the decision half of the
custom-sequence relay: given a parsed :class:`envelope.Envelope` and the current
:class:`registry.Registry`, it resolves the destination session(s) and returns a
structured :class:`RoutingDecision`. The thin adapter (``adapter.py``) is what
actually calls ``Session.async_send_text`` on the target — this module never
touches iTerm2.

Resolution precedence (``to`` field, matched against live sessions):
  1. **agent_id** — exact match wins. If one or more sessions carry
     ``agent_id == to`` they are the targets and nothing else is considered.
  2. **agent_role** — fallback only when *zero* sessions matched by id. **All**
     sessions whose ``agent_role == to`` receive the message (fan-out); this is
     deliberate so a message addressed to a role reaches every worker in it.
  3. no session matched either → undeliverable (``no match``).

Undeliverable outcomes (never raises; a bad envelope is logged and dropped):
  * ``messaging disabled`` — the ``agent.messaging`` gate is OFF.
  * ``no destination`` — the envelope has no ``to``.
  * ``empty body`` — nothing to inject.
  * ``no match`` — no session carries that id or role.
  * ``self`` — the only match(es) belong to the sender itself.

**Best-effort only (explicit non-goal).** This is an in-memory relay with no
durability: if the target session is absent when the message arrives it is
simply undeliverable, and if it is present but busy the text is injected and may
be lost. There is **no** queue, replay, ack, retry, or ordering guarantee — that
durable path is Tier 2 (#4) and is intentionally out of scope here. The injected
text carries a ``[it2agent]`` marker and the sender id so the receiving agent
can see it is a it2agent relay rather than local input.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Gate: like every it2agent capability this relay is off by default. We reuse
# the #11 flag helper (the same mechanism it2agent_daemon.flag_enabled uses),
# never a second TOML reader. Kept iterm2-free so this module stays pure.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"
MESSAGING_FLAG = "agent.messaging"

# How a destination was resolved (surfaced on the decision for logging/tests).
MATCH_BY_ID = "agent_id"
MATCH_BY_ROLE = "agent_role"

# Prefix on every injected line so the receiving agent can tell a relayed
# it2agent message apart from ordinary local input.
DELIVERY_PREFIX = "[it2agent]"


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of routing one envelope.

    ``deliverable`` implies ``target_session_ids`` and ``text`` are set;
    otherwise ``reason`` explains why the message was dropped.
    """

    deliverable: bool
    target_session_ids: tuple[str, ...] = ()
    text: str = ""
    matched_by: str = ""  # MATCH_BY_ID | MATCH_BY_ROLE | ""
    reason: str = ""

    @classmethod
    def undeliverable(cls, reason: str) -> "RoutingDecision":
        return cls(deliverable=False, reason=reason)

    @classmethod
    def deliver(
        cls, session_ids: tuple[str, ...], text: str, matched_by: str
    ) -> "RoutingDecision":
        return cls(
            deliverable=True,
            target_session_ids=tuple(session_ids),
            text=text,
            matched_by=matched_by,
        )


def messaging_enabled() -> bool:
    """Return True iff the ``agent.messaging`` flag is ON.

    Mirrors ``it2agent_daemon.flag_enabled``: import the #11 helper and ask it.
    Fail-safe — if the helper is unreachable the flag reads OFF (capabilities
    default off). No ``iterm2`` involvement, so this module stays pure.
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import it2agent_flag  # type: ignore

        return it2agent_flag.is_enabled(MESSAGING_FLAG)
    except Exception:  # noqa: BLE001 - unreachable helper => flag OFF
        return False


def _body_text(body: Any) -> str:
    """Coerce an envelope body to the string to inject. Objects become compact
    JSON; anything unserializable falls back to ``str``."""
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(body)


def format_delivery(sender: str | None, body_text: str) -> str:
    """Build the exact text injected into the target session. Carries the
    ``[it2agent]`` marker and the sender so the receiver knows it is a relay."""
    who = (sender or "").strip() or "unknown"
    return f"{DELIVERY_PREFIX} message from {who}: {body_text}\n"


def route(envelope, registry) -> RoutingDecision:
    """Resolve ``envelope`` against ``registry`` into a :class:`RoutingDecision`.

    Pure and total: never raises. Applies the id-before-role precedence
    documented in the module docstring, guards self-sends, and returns a
    structured undeliverable reason for every non-delivery.

    This function does **not** consult the feature flag — gating is handled by
    :func:`route_if_enabled` so the resolution logic stays deterministic and the
    gate stays independently testable.
    """
    try:
        if envelope is None or registry is None:
            return RoutingDecision.undeliverable("no envelope")

        to = (getattr(envelope, "to", None) or "").strip()
        if not to:
            return RoutingDecision.undeliverable("no destination")

        body_text = _body_text(getattr(envelope, "body", None))
        if not body_text.strip():
            return RoutingDecision.undeliverable("empty body")

        sender = (getattr(envelope, "sender", None) or "").strip()
        sessions = registry.all()

        id_matches = [s for s in sessions if s.agent_id and s.agent_id == to]
        if id_matches:
            candidates, matched_by = id_matches, MATCH_BY_ID
        else:
            role_matches = [s for s in sessions if s.agent_role and s.agent_role == to]
            if role_matches:
                candidates, matched_by = role_matches, MATCH_BY_ROLE
            else:
                return RoutingDecision.undeliverable("no match")

        # Self-send guard: never relay a message back into the sender's own
        # session(s). If that empties the target set, the send was to self.
        targets = [s for s in candidates if not (sender and s.agent_id == sender)]
        if not targets:
            return RoutingDecision.undeliverable("self")

        text = format_delivery(sender, body_text)
        session_ids = tuple(s.session_id for s in targets)
        return RoutingDecision.deliver(session_ids, text, matched_by)
    except Exception as exc:  # noqa: BLE001 - a router bug must never crash ingest
        return RoutingDecision.undeliverable(f"router error: {exc}")


def route_if_enabled(envelope, registry, *, enabled: bool) -> RoutingDecision:
    """Gate then route. When ``enabled`` is False (the ``agent.messaging``
    flag is OFF) no routing decision is produced — the caller still parsed and
    logged the envelope upstream, it just does not relay. Otherwise defer to
    :func:`route`."""
    if not enabled:
        return RoutingDecision.undeliverable("messaging disabled")
    return route(envelope, registry)
