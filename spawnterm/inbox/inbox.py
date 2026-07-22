#!/usr/bin/env python3
"""The inbox workflow glue (#17): intake -> policy -> queue/attention -> decision.

This ties the pure pieces (policy, attention routing) to the durable store and
the feature-flag gate. It is deliberately thin — the interesting logic lives in
the pure modules; this orchestrates them.

Intake path
-----------
An agent submits an :class:`~model.InboxRequest` (through the CLI ``submit``, or
by constructing :class:`Inbox` in-process — e.g. the Tier 1 daemon forwarding a
"needs-human" signal on the agent's behalf). :meth:`Inbox.submit` then:

1. **Gate** — if ``spawnterm.agent_inbox`` is OFF (and not bypassed), do nothing
   and return a no-op result. The inbox never touches the broker when gated off.
2. **Enqueue** — persist the request durably (it gets its broker id) so there is
   an audit trail for *every* submission, auto or not.
3. **Classify** — run the pure policy:

   * ``AUTO_APPROVE`` — record an auto decision (``decided_by="policy:auto"``)
     and notify the agent. No human, no attention.
   * ``BLOCK`` — record a blocked decision (``decided_by="policy:block"``) and
     notify the agent. No human, no attention.
   * ``NEEDS_HUMAN`` — leave it pending and **raise attention** to the target
     pane via the emitter. The human later resolves it with :meth:`decide`.

Decision path
-------------
A human calls :meth:`decide` (from the CLI ``approve`` / ``edit`` / ``reject``).
The decision is appended to the durable decisions stream and pushed back to the
requesting agent's mailbox. Reversibility of who-decided-what is preserved by the
append-only log in the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import attention as attention_mod
from config import default_config
from gate import gate_open
from model import Decision, DecisionRecord, InboxRequest, Verdict
from policy import PolicyConfig, PolicyResult, classify
from store import InboxStore


@dataclass
class SubmitResult:
    """Outcome of :meth:`Inbox.submit`."""

    request: Optional[InboxRequest]
    result: Optional[PolicyResult]
    routed: bool = False
    gated_off: bool = False


class Inbox:
    """Orchestrates intake, policy, attention routing, and decisions."""

    def __init__(
        self,
        store: InboxStore,
        config: Optional[PolicyConfig] = None,
        emitter: Optional[attention_mod.AttentionEmitter] = None,
        no_gate: bool = False,
    ) -> None:
        self.store = store
        self.config = config if config is not None else default_config()
        self.emitter = emitter if emitter is not None else attention_mod.EmitAttentionEmitter()
        self.no_gate = no_gate

    def _gate_open(self) -> bool:
        return gate_open(self.no_gate)

    # -- intake ------------------------------------------------------------ #

    def submit(self, request: InboxRequest) -> SubmitResult:
        """Intake a request: gate -> enqueue -> classify -> route/auto-resolve."""
        if not self._gate_open():
            return SubmitResult(request=None, result=None, gated_off=True)

        self.store.enqueue(request)
        result = classify(request, self.config)

        if result.decision is Decision.AUTO_APPROVE:
            self._auto_resolve(request, Verdict.APPROVED, "policy:auto", result.reason)
            return SubmitResult(request=request, result=result, routed=False)

        if result.decision is Decision.BLOCK:
            self._auto_resolve(request, Verdict.BLOCKED, "policy:block", result.reason)
            return SubmitResult(request=request, result=result, routed=False)

        # NEEDS_HUMAN: leave pending, raise attention to the target pane.
        route = attention_mod.route_attention(request, result)
        routed = False
        if route is not None:
            routed = bool(self.emitter.raise_attention(route))
        return SubmitResult(request=request, result=result, routed=routed)

    def _auto_resolve(
        self, request: InboxRequest, verdict: Verdict, decided_by: str, note: str
    ) -> DecisionRecord:
        record = DecisionRecord(
            request_id=request.id,
            verdict=verdict.value,
            note=note,
            decided_by=decided_by,
        )
        self.store.record_decision(record)
        if request.agent:
            self.store.notify_agent(request.agent, record)
        return record

    # -- decision ---------------------------------------------------------- #

    def decide(
        self,
        request_id: int,
        verdict: Verdict,
        note: Optional[str] = None,
        edited_request: Optional[dict[str, Any]] = None,
        decided_by: str = "human",
    ) -> DecisionRecord:
        """Record a human decision and notify the requesting agent.

        ``edited_request`` carries the modified action descriptor for an
        approve-with-edits (``Verdict.EDITED``).
        """
        # Resolve the requesting agent BEFORE recording the decision: recording
        # compacts the resolved prefix, which may ack this request out of the
        # requests stream, so a post-decision lookup could miss it.
        request = self.store.get(request_id)
        record = DecisionRecord(
            request_id=request_id,
            verdict=verdict.value,
            note=note,
            edited_request=edited_request,
            decided_by=decided_by,
        )
        self.store.record_decision(record)
        if request is not None and request.agent:
            self.store.notify_agent(request.agent, record)
        return record

    # -- reads ------------------------------------------------------------- #

    def pending(self) -> list[InboxRequest]:
        return self.store.list_pending()

    def get(self, request_id: int) -> Optional[InboxRequest]:
        return self.store.get(request_id)

    def classify(self, request: InboxRequest) -> PolicyResult:
        """Expose the pure classification (for ``show`` — explain a decision)."""
        return classify(request, self.config)
