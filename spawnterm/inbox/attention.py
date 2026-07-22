#!/usr/bin/env python3
"""Attention routing for the inbox (#17).

Split cleanly into two halves so the *decision* is unit-testable without any
process spawning:

* :func:`route_attention` — **pure**. Given a request and its policy result,
  decide *whether* to raise attention, *which* session (iTerm2 pane) to raise it
  to, and *what* message to show. Only ``NEEDS_HUMAN`` requests route; auto and
  blocked ones never page a human.
* :class:`AttentionEmitter` — the injectable side-effect. The default
  :class:`EmitAttentionEmitter` shells out to ``spawnterm-emit attention <msg>``
  (which writes ``RequestAttention=yes`` + an ``OSC 9`` notification). Tests
  inject a :class:`RecordingEmitter`; the daemon can inject a session-aware one.

Keeping ``iterm2`` out entirely (the module imports only the stdlib): attention
reaches the pane through the ``spawnterm-emit`` subprocess, exactly as the design
note requires. The pure route carries ``session`` so a session-aware emitter
(e.g. the Tier 1 daemon) can target the exact pane; the default emitter runs
``spawnterm-emit`` in the current process, which lands on the pane that hosts it
(the common case: an agent self-reporting from its own pane).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any, Optional

from model import Decision

EMIT_BIN = "spawnterm-emit"


@dataclass(frozen=True)
class AttentionRoute:
    """Where to raise attention (``session``, may be ``None``) and what to say."""

    session: Optional[str]
    message: str


def build_message(request: Any) -> str:
    """A concise one-line page for the human, derived from the descriptor."""
    ident = f"req {request.id}" if getattr(request, "id", None) is not None else "req ?"
    detail = request.summary or request.action
    return (
        f"spawnterm inbox: approval needed - {detail} "
        f"(scope {request.scope}, cost {request.cost}) [{ident}]"
    )


def route_attention(request: Any, result: Any) -> Optional[AttentionRoute]:
    """Return the route to raise, or ``None`` when no human page is warranted.

    Pure: only ``NEEDS_HUMAN`` warrants a page. The target pane is the request's
    ``session`` (``None`` when the agent did not tag one — the default emitter
    still pages the current pane).
    """
    if result.decision is not Decision.NEEDS_HUMAN:
        return None
    return AttentionRoute(session=request.session, message=build_message(request))


class AttentionEmitter:
    """Interface: raise attention for a route. Return True on success."""

    def raise_attention(self, route: AttentionRoute) -> bool:  # pragma: no cover
        raise NotImplementedError


class EmitAttentionEmitter(AttentionEmitter):
    """Default emitter: shell out to ``spawnterm-emit attention <message>``.

    The subprocess writes the iTerm2 escape codes to *its* stdout, i.e. the pane
    that hosts it. ``spawnterm-emit`` self-gates on ``spawnterm.status_board`` and
    emits nothing when that flag is off, so this is safe to call unconditionally.
    Never raises: a missing binary or non-zero exit is reported as ``False``.
    """

    def __init__(self, emit_bin: str = EMIT_BIN) -> None:
        self.emit_bin = emit_bin

    def raise_attention(self, route: AttentionRoute) -> bool:
        try:
            completed = subprocess.run(
                [self.emit_bin, "attention", route.message],
                check=False,
            )
        except OSError:
            return False
        return completed.returncode == 0


class RecordingEmitter(AttentionEmitter):
    """Test/degraded emitter: record routes instead of touching a terminal."""

    def __init__(self) -> None:
        self.routes: list[AttentionRoute] = []

    def raise_attention(self, route: AttentionRoute) -> bool:
        self.routes.append(route)
        return True
