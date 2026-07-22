#!/usr/bin/env python3
"""it2agent-inbox — the human-attention inbox CLI/TUI (#17).

A small operator surface over the durable inbox: agents ``submit`` requests; a
human ``list`` / ``show`` the queue and ``approve`` / ``edit`` / ``reject`` each
one. Policy classification, attention routing, and durability all live in the
importable modules (``policy`` / ``attention`` / ``store`` / ``inbox``); this is
just argument parsing plus a broker connection.

Gating: every subcommand honors ``agent.inbox`` (default OFF). When the
flag is off the inbox is a **no-op** — commands print a one-line notice and exit
0 without touching the broker. Bypass with ``--no-gate`` or ``IT2AGENT_FORCE=1``.

Durability + degradation: connects to the in-repo broker (#4) via
``BrokerClient`` and pings it. If the broker is unreachable the CLI prints a
clear error and exits 1 (the queue is durable state — it cannot be listed
without the broker). Pass ``--in-memory`` to run against a throwaway in-process
queue instead (non-durable; for a single-process demo or smoke test).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import attention as attention_mod
from config import config_path, load_config
from gate import FLAG_KEY, gate_open
from inbox import Inbox
from model import InboxRequest, Verdict
from store import BrokerUnavailable, InboxStore, InMemoryBroker

PROG = "it2agent-inbox"

# Sibling broker package: it2agent/broker/client.py.
_BROKER_DIR = Path(__file__).resolve().parent.parent / "broker"


def _connect_broker(in_memory: bool) -> tuple[Any, bool]:
    """Return ``(broker, durable)``. Raises :class:`BrokerUnavailable` on failure."""
    if in_memory:
        return InMemoryBroker(), False
    try:
        if str(_BROKER_DIR) not in sys.path:
            sys.path.insert(0, str(_BROKER_DIR))
        from client import BrokerClient  # type: ignore

        client = BrokerClient()
        reply = client.ping()
        if not (isinstance(reply, dict) and reply.get("ok")):
            raise BrokerUnavailable(f"broker ping failed: {reply}")
        return client, True
    except BrokerUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - socket down / package missing
        raise BrokerUnavailable(f"broker unreachable: {exc}") from exc


def _make_inbox(args: argparse.Namespace) -> Inbox:
    broker, durable = _connect_broker(getattr(args, "in_memory", False))
    store = InboxStore(broker, durable=durable)
    # Real terminal emit for the CLI; the pure route decides whether it fires.
    return Inbox(store, config=load_config(), emitter=attention_mod.EmitAttentionEmitter(),
                 no_gate=getattr(args, "no_gate", False))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _fmt_request_line(req: InboxRequest) -> str:
    detail = req.summary or req.action
    return (
        f"#{req.id:<4} {req.action:<22} scope={req.scope:<10} "
        f"cost={req.cost:<6} rev={'y' if req.reversible else 'n'}  {detail}"
    )


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #


def cmd_submit(args: argparse.Namespace) -> int:
    if not gate_open(args.no_gate):
        print(f"{PROG}: {FLAG_KEY} is OFF; inbox is a no-op (nothing submitted).")
        return 0
    inbox = _make_inbox(args)
    request = InboxRequest(
        action=args.action,
        reversible=args.reversible,
        scope=args.scope,
        cost=args.cost,
        session=args.session,
        agent=args.agent,
        summary=args.summary,
    )
    outcome = inbox.submit(request)
    decision = outcome.result.decision.value if outcome.result else "gated_off"
    routed = " (attention raised)" if outcome.routed else ""
    print(f"{PROG}: submitted #{request.id} -> {decision}{routed}")
    if outcome.result:
        print(f"        reason: {outcome.result.reason}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if not gate_open(args.no_gate):
        print(f"{PROG}: {FLAG_KEY} is OFF; inbox is a no-op (no pending requests).")
        return 0
    inbox = _make_inbox(args)
    pending = inbox.pending()
    if not pending:
        print(f"{PROG}: no pending requests.")
        return 0
    print(f"{PROG}: {len(pending)} pending request(s) awaiting a human:")
    for req in pending:
        print("  " + _fmt_request_line(req))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    if not gate_open(args.no_gate):
        print(f"{PROG}: {FLAG_KEY} is OFF; inbox is a no-op.")
        return 0
    inbox = _make_inbox(args)
    req = inbox.get(args.id)
    if req is None:
        print(f"{PROG}: no such request #{args.id}", file=sys.stderr)
        return 1
    result = inbox.classify(req)
    print(f"request #{req.id}")
    print(f"  action:     {req.action}")
    print(f"  reversible: {req.reversible}")
    print(f"  scope:      {req.scope}")
    print(f"  cost:       {req.cost}")
    print(f"  session:    {req.session}")
    print(f"  agent:      {req.agent}")
    print(f"  summary:    {req.summary}")
    print(f"  decision:   {result.decision.value} (rule {result.rule})")
    print(f"  reason:     {result.reason}")
    return 0


def _decide(args: argparse.Namespace, verdict: Verdict,
            edited_request: Optional[dict] = None) -> int:
    if not gate_open(args.no_gate):
        print(f"{PROG}: {FLAG_KEY} is OFF; inbox is a no-op (no decision recorded).")
        return 0
    inbox = _make_inbox(args)
    record = inbox.decide(args.id, verdict, note=args.note, edited_request=edited_request)
    print(f"{PROG}: request #{record.request_id} -> {record.verdict}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _decide(args, Verdict.APPROVED)


def cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args, Verdict.REJECTED)


def cmd_edit(args: argparse.Namespace) -> int:
    # An edit is an approve-with-changes: carry the operator's modified descriptor.
    edited: dict[str, Any] = {}
    if args.action is not None:
        edited["action"] = args.action
    if args.scope is not None:
        edited["scope"] = args.scope
    if args.cost is not None:
        edited["cost"] = args.cost
    if args.reversible is not None:
        edited["reversible"] = args.reversible
    return _decide(args, Verdict.EDITED, edited_request=edited or None)


def cmd_config_path(args: argparse.Namespace) -> int:
    print(config_path())
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="it2agent human-attention inbox.")
    parser.add_argument("--no-gate", action="store_true", help="bypass the feature-flag gate")
    parser.add_argument("--in-memory", action="store_true",
                        help="use a throwaway in-process queue (non-durable)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="submit a request needing review")
    p_submit.add_argument("--action", required=True, help="action descriptor, e.g. git.push")
    p_submit.add_argument("--scope", default="workspace", help="read|workspace|repo|system|network")
    p_submit.add_argument("--cost", type=float, default=0.0, help="estimated cost/magnitude")
    p_submit.add_argument("--reversible", action="store_true", help="the action is reversible")
    p_submit.add_argument("--session", default=None, help="target iTerm2 session/pane id")
    p_submit.add_argument("--agent", default=None, help="requesting agent id (for notify-back)")
    p_submit.add_argument("--summary", default=None, help="human-readable one-liner")
    p_submit.set_defaults(func=cmd_submit)

    p_list = sub.add_parser("list", help="list pending requests")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one request + its policy decision")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_approve = sub.add_parser("approve", help="approve a pending request")
    p_approve.add_argument("id", type=int)
    p_approve.add_argument("--note", default=None)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="reject a pending request")
    p_reject.add_argument("id", type=int)
    p_reject.add_argument("--note", default=None)
    p_reject.set_defaults(func=cmd_reject)

    p_edit = sub.add_parser("edit", help="approve with an edited action descriptor")
    p_edit.add_argument("id", type=int)
    p_edit.add_argument("--action", default=None)
    p_edit.add_argument("--scope", default=None)
    p_edit.add_argument("--cost", type=float, default=None)
    p_edit.add_argument("--reversible", type=lambda s: s.lower() in ("1", "true", "yes"),
                        default=None)
    p_edit.add_argument("--note", default=None)
    p_edit.set_defaults(func=cmd_edit)

    p_cfg = sub.add_parser("config-path", help="print the resolved inbox.toml path")
    p_cfg.set_defaults(func=cmd_config_path)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokerUnavailable as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        print(f"{PROG}: the inbox queue is durable broker state; start the broker "
              f"(it2agent_broker.py serve) or pass --in-memory for a non-durable run.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
