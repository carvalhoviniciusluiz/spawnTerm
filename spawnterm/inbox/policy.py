#!/usr/bin/env python3
"""The inbox policy engine (#17) — pure, unit-tested, no I/O.

This is the heart of the human-attention router: given an action descriptor
(``action`` + the three axes ``reversible`` / ``scope`` / ``cost``) and a
:class:`PolicyConfig`, decide whether to **auto-approve**, **queue for a human**,
or **block** outright. It is the LangChain Agent-Inbox policy model: reversible +
scoped + cheap + explicitly-allow-listed actions run themselves; everything else
interrupts a human; a hard-forbidden few are refused.

Purity is deliberate — no config file reading, no broker, no iTerm2, no clock.
:func:`classify` is a total function of ``(request, config)``. Config loading
(the allow-list file) lives in :mod:`config`; the workflow glue lives in
:mod:`inbox`.

Decision rules, evaluated top to bottom (first match wins):

1. **BLOCK** — the action is in ``block_list``; OR ``block_cost`` is set and the
   cost exceeds it; OR the action is irreversible and its scope is in
   ``block_scopes``. These are refused; they never reach a human.
2. **AUTO_APPROVE** — the action is in ``allow_list`` **and** every guard holds:
   its scope is in ``auto_scopes``, its cost is within ``max_auto_cost``, and
   (unless ``require_reversible_for_auto`` is off) it is reversible. Deny by
   default: an action absent from ``allow_list`` is *never* auto-approved.
3. **NEEDS_HUMAN** — everything else, including an allow-listed action that
   tripped a guard (too costly / out of its safe scope / irreversible). The
   ``reason`` says which guard failed so the human sees why they were paged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from model import Decision


@dataclass(frozen=True)
class PolicyConfig:
    """The tunable policy. Frozen so a config is safe to share and cannot drift.

    * ``allow_list`` — actions eligible for auto-approve (the *only* gate into
      AUTO_APPROVE; deny-by-default for everything else).
    * ``block_list`` — actions refused outright, whatever their axes.
    * ``auto_scopes`` — scopes an allow-listed action may auto-approve within
      (default just ``read``: read-only is the safe scope).
    * ``block_scopes`` — scopes so dangerous that an *irreversible* action there
      is blocked (default ``system``).
    * ``max_auto_cost`` — the cost ceiling for auto-approve (default ``0`` — only
      free actions auto-run; conservative).
    * ``block_cost`` — a hard cost ceiling; above it, block (default ``None`` —
      no hard block, expensive-but-not-forbidden actions just page a human).
    * ``require_reversible_for_auto`` — when True (default), an irreversible
      action is never auto-approved even if allow-listed and in scope.
    """

    allow_list: frozenset[str] = field(default_factory=frozenset)
    block_list: frozenset[str] = field(default_factory=frozenset)
    auto_scopes: frozenset[str] = field(default_factory=lambda: frozenset({"read"}))
    block_scopes: frozenset[str] = field(default_factory=lambda: frozenset({"system"}))
    max_auto_cost: float = 0.0
    block_cost: Optional[float] = None
    require_reversible_for_auto: bool = True


@dataclass(frozen=True)
class PolicyResult:
    """The verdict plus *why* — ``rule`` is a stable id, ``reason`` is prose."""

    decision: Decision
    rule: str
    reason: str


def classify(request: Any, config: PolicyConfig) -> PolicyResult:
    """Classify ``request`` under ``config``. Total, pure, side-effect free.

    ``request`` is any object exposing ``action`` / ``reversible`` / ``scope`` /
    ``cost`` (an :class:`~model.InboxRequest`, or a duck-typed stand-in).
    """
    action = request.action
    reversible = bool(request.reversible)
    scope = request.scope
    cost = float(request.cost)

    # 1) BLOCK ---------------------------------------------------------------
    if action in config.block_list:
        return PolicyResult(
            Decision.BLOCK,
            "block_list",
            f"action {action!r} is on the block list",
        )
    if config.block_cost is not None and cost > config.block_cost:
        return PolicyResult(
            Decision.BLOCK,
            "block_cost",
            f"cost {cost} exceeds the hard ceiling {config.block_cost}",
        )
    if not reversible and scope in config.block_scopes:
        return PolicyResult(
            Decision.BLOCK,
            "block_scope",
            f"irreversible action in blocked scope {scope!r}",
        )

    # 2) AUTO_APPROVE (deny-by-default: allow-list membership is mandatory) ---
    if action in config.allow_list:
        if scope not in config.auto_scopes:
            return PolicyResult(
                Decision.NEEDS_HUMAN,
                "scope_guard",
                f"allow-listed but scope {scope!r} is not an auto scope",
            )
        if cost > config.max_auto_cost:
            return PolicyResult(
                Decision.NEEDS_HUMAN,
                "cost_guard",
                f"allow-listed but cost {cost} exceeds max_auto_cost {config.max_auto_cost}",
            )
        if config.require_reversible_for_auto and not reversible:
            return PolicyResult(
                Decision.NEEDS_HUMAN,
                "reversible_guard",
                "allow-listed but irreversible and reversibility is required to auto-approve",
            )
        return PolicyResult(
            Decision.AUTO_APPROVE,
            "allow_list",
            f"action {action!r} is allow-listed, in scope, cheap, and safe",
        )

    # 3) NEEDS_HUMAN (default) ----------------------------------------------
    return PolicyResult(
        Decision.NEEDS_HUMAN,
        "default_deny",
        f"action {action!r} is not on the allow list (deny by default)",
    )
