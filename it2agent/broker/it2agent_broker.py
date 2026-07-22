#!/usr/bin/env python3
"""it2agent-broker — Tier 2.1 durable-state broker (#34).

The external broker owns what iTerm2 cannot: a durable message queue, a
queryable agent registry, persistent shared state, and delivery ack (see
``it2agent/docs/design.md`` — "What iTerm2 CANNOT do → external broker").
This entry point is the thin CLI/gate/wiring shell around the pure core.

``scope:external-tooling`` — the broker is fully independent of iTerm2 and never
imports or modifies iTerm2 source. The Tier 1 daemon bridges the two in #37.

Feature-flag gate: like every it2agent capability, the broker is off by
default. The **server** starts only when ``agent.broker`` is ON (checked via
the #11 flag helper). If the flag is OFF/absent it prints a clear message and
exits 0. Bypass for local testing with ``--no-gate`` or ``IT2AGENT_FORCE=1`` —
mirrors the daemon gate exactly. Client subcommands (``ping``/``health``) talk
to an already-running server and are **not** gated.

Architecture (testability): the real logic lives in the pure modules
``schema`` (sqlite), ``protocol`` (wire), ``dispatch`` (op registry), and
``paths`` — none import a socket or iTerm2 and all are unit-tested in ``tests/``.
``server`` (asyncio) and this file are the thin I/O shell.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import paths

PROG = "it2agent-broker"
FLAG_KEY = "agent.broker"

# Sibling flags helper (#11): it2agent/flags/{it2agent_flag.py,it2agent-flag}.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def flag_enabled() -> bool:
    """Return True iff ``agent.broker`` is ON.

    Prefers importing the #11 Python helper (``it2agent_flag.is_enabled``);
    falls back to shelling out to the ``it2agent-flag`` script. Fail-safe: if
    neither is reachable, treat the flag as OFF (capabilities default off).
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import it2agent_flag  # type: ignore

        return it2agent_flag.is_enabled(FLAG_KEY)
    except Exception:  # noqa: BLE001 - fall back to the CLI helper
        pass

    helper = _FLAGS_DIR / "it2agent-flag"
    candidate = str(helper) if helper.exists() else "it2agent-flag"
    try:
        result = subprocess.run(
            [candidate, FLAG_KEY],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def gate_open(no_gate: bool) -> bool:
    if no_gate or os.environ.get("IT2AGENT_FORCE") == "1":
        return True
    return flag_enabled()


def _build_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("agent.broker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s agent.broker: %(message)s")
    )
    logger.handlers = [handler]
    logger.propagate = False
    return logger


def _resolve_db(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else paths.broker_db_path()


def _resolve_sock(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else paths.broker_sock_path()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="it2agent Tier 2 broker: durable sqlite state + unix-socket "
        "request/response server (mailbox/registry/handoff ops land in #35–#37).",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the broker server (default).")
    serve.add_argument("--sock", default=None, help="unix socket path override.")
    serve.add_argument("--db", default=None, help="sqlite db path override.")
    serve.add_argument(
        "--no-gate",
        action="store_true",
        help="bypass the agent.broker feature-flag gate (local testing).",
    )
    serve.add_argument("-v", "--verbose", action="store_true", help="debug logging.")

    for name, helptext in (("ping", "ping a running broker"), ("health", "query a running broker")):
        c = sub.add_parser(name, help=helptext)
        c.add_argument("--sock", default=None, help="unix socket path override.")
        c.add_argument("-v", "--verbose", action="store_true", help="debug logging.")

    path = sub.add_parser("paths", help="print the resolved db + socket paths.")
    path.add_argument("--sock", default=None)
    path.add_argument("--db", default=None)

    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    logger = _build_logger(args.verbose)
    if not gate_open(args.no_gate):
        print(
            f"{PROG}: feature flag '{FLAG_KEY}' is OFF; refusing to start.\n"
            f"Enable it with:  it2agent-flag enable {FLAG_KEY}\n"
            f"(or run with --no-gate / IT2AGENT_FORCE=1 for local testing).",
            file=sys.stderr,
        )
        return 0
    import server

    server.run(_resolve_sock(args.sock), _resolve_db(args.db), logger)
    return 0


def _cmd_client(op: str, args: argparse.Namespace) -> int:
    from client import BrokerClient

    client = BrokerClient(sock_path=_resolve_sock(args.sock))
    try:
        response = client.request({"op": op})
    except OSError as exc:
        print(
            f"{PROG} {op}: cannot reach broker at {client.sock_path} ({exc}). "
            f"Is it running?  ({PROG} serve)",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(response, sort_keys=True))
    return 0 if response.get("ok") else 1


def _cmd_paths(args: argparse.Namespace) -> int:
    print(f"db:   {_resolve_db(args.db)}")
    print(f"sock: {_resolve_sock(args.sock)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    command = args.command or "serve"
    if command == "serve":
        # Re-parse for the default (no subcommand) case where serve flags are absent.
        if args.command is None:
            args = parser.parse_args(["serve"])
        return _cmd_serve(args)
    if command in ("ping", "health"):
        return _cmd_client(command, args)
    if command == "paths":
        return _cmd_paths(args)
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
