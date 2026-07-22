#!/usr/bin/env python3
"""Route a request-changes note to an agent via the broker mailbox (#14).

The review surface's ``request-changes`` action wants to reach the agent that
produced the diff. The natural, durable mechanism is the Tier 2 broker mailbox
(#34/#35): a ``{op:"send", to, from, body}`` request appends a durable,
at-least-once, acked message to the recipient's queue. That is strictly better
than a fire-and-forget nudge — the note survives a broker restart and is
re-delivered until the agent acks it.

This is the thin, testable seam the shell ``it2agent-review`` tool shells out
to for the broker leg. It imports **only** the sibling broker's stdlib client
(:class:`BrokerClient`) — no asyncio, no iTerm2. When the broker is unreachable
the caller (``it2agent-review``) falls back to a file-in-the-worktree note; this
helper's job is just the broker send.

Layering / testability
-----------------------
* :func:`build_request` is pure: it validates the fields and returns the exact
  ``send`` request object. Unit-testable with no socket.
* ``--dry-run`` prints that request as JSON and sends nothing, so callers (and
  tests) can assert the routing decision + payload without a running broker.
* Only the ``send`` path opens a socket. An unreachable broker exits non-zero so
  the shell caller can degrade to its file fallback.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROG = "it2agent-review-notify"

# The broker client + paths live in the sibling package (#34).
_BROKER_DIR = Path(__file__).resolve().parent.parent / "broker"


def build_request(to: str, sender: str, note: str) -> dict:
    """Build the broker ``send`` request object. Pure; raises on bad input.

    Mirrors the mailbox ``send`` op contract (#35): ``to``/``from``/``body`` are
    all required non-empty strings. The body is prefixed so the agent can tell a
    review note apart from peer chatter at a glance.
    """
    to = (to or "").strip()
    sender = (sender or "").strip()
    note = (note or "").strip()
    if not to:
        raise ValueError("a recipient agent id is required (--to)")
    if not sender:
        raise ValueError("a sender id is required (--from)")
    if not note:
        raise ValueError("a non-empty note is required (--note)")
    return {
        "op": "send",
        "to": to,
        "from": sender,
        "body": f"[review: changes requested] {note}",
    }


def _resolve_sock(arg: str | None) -> str:
    if arg:
        return str(Path(arg).expanduser())
    if str(_BROKER_DIR) not in sys.path:
        sys.path.insert(0, str(_BROKER_DIR))
    import paths  # type: ignore

    return str(paths.broker_sock_path())


def _send(request: dict, sock: str) -> dict:
    if str(_BROKER_DIR) not in sys.path:
        sys.path.insert(0, str(_BROKER_DIR))
    from client import BrokerClient  # type: ignore

    return BrokerClient(sock_path=sock).request(request)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Send a request-changes note to an agent via the broker mailbox.",
    )
    parser.add_argument("--to", required=True, help="recipient agent id (mailbox key).")
    parser.add_argument("--from", dest="sender", required=True, help="reviewer id.")
    parser.add_argument("--note", required=True, help="the review note text.")
    parser.add_argument("--sock", default=None, help="broker socket path override.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the send request as JSON and exit; send nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        request = build_request(args.to, args.sender, args.note)
    except ValueError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps(request, sort_keys=True))
        return 0

    sock = _resolve_sock(args.sock)
    try:
        response = _send(request, sock)
    except OSError as exc:
        print(f"{PROG}: cannot reach broker at {sock} ({exc})", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - surface protocol/import failures
        print(f"{PROG}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(response, sort_keys=True))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
