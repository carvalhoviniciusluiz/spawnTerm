#!/usr/bin/env python3
"""it2agent-mcp — MCP orchestration surface for it2agent (#18).

Exposes it2agent's orchestration (spawn / assign / handoff / send_message /
status / list_agents) as **MCP tools** over stdio (JSON-RPC 2.0), so any
MCP-capable agent — Claude Code, Codex, etc. — can self-orchestrate. It is
backed by the durable Tier 2 broker (sqlite mailbox/registry/handoff over a unix
socket) and the Tier 1 daemon spawn path; it does **not** reimplement transport
or state (see ``it2agent/docs/design.md``).

``scope:external-tooling`` — never imports or modifies iTerm2 source. The spawn
tool reaches iTerm2 only indirectly, by shelling out to the Tier 1 daemon spawn
CLI, so this process itself has no ``iterm2`` dependency.

This file is the **thin I/O shell** only:

  * the feature-flag gate (``agent.mcp``, default OFF — refuse to start with
    a message + exit 0, bypassable with ``--no-gate`` / ``IT2AGENT_FORCE=1``,
    mirroring the daemon/broker gates), and
  * the stdio read/dispatch/write loop.

All protocol and tool logic is pure and unit-tested in ``rpc`` / ``tools``.
No pip dependencies: the JSON-RPC loop is a handful of stdlib lines.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROG = "it2agent-mcp"
FLAG_KEY = "agent.mcp"

# Sibling helpers, resolved relative to this file (mirrors broker/daemon).
_HERE = Path(__file__).resolve().parent
_FLAGS_DIR = _HERE.parent / "flags"
_BROKER_DIR = _HERE.parent / "broker"
_DAEMON_DIR = _HERE.parent / "daemon"


# --------------------------------------------------------------------------- #
# Feature-flag gate (identical convention to it2agent-broker / it2agent-daemon)
# --------------------------------------------------------------------------- #


def flag_enabled() -> bool:
    """Return True iff ``agent.mcp`` is ON.

    Prefers the #11 Python helper (``it2agent_flag.is_enabled``); falls back to
    shelling out to the ``it2agent-flag`` script. Fail-safe: if neither is
    reachable, treat the flag as OFF (capabilities default off).
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
    logger = logging.getLogger("agent.mcp")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s agent.mcp: %(message)s")
    )
    logger.handlers = [handler]
    logger.propagate = False
    return logger


# --------------------------------------------------------------------------- #
# Injected impure dependencies (broker client + spawn launcher)
# --------------------------------------------------------------------------- #


def _make_broker_client() -> Any:
    """Construct a real :class:`client.BrokerClient` (lazy import).

    The client is stateless (a short-lived connection per request), so a down
    broker surfaces as an ``OSError`` at call time — which the server catches
    and reports as an MCP tool error. Never pings here; the tools tolerate a
    dead broker per-call.
    """
    if str(_BROKER_DIR) not in sys.path:
        sys.path.insert(0, str(_BROKER_DIR))
    from client import BrokerClient  # type: ignore

    return BrokerClient()


def _make_spawn_launcher(logger: logging.Logger):
    """Return a spawn launcher that shells out to the Tier 1 daemon spawn CLI.

    Keeping the launch behind a subprocess boundary means this MCP process never
    imports ``iterm2``. The daemon spawn CLI (``it2agent_daemon.py spawn``)
    rebuilds and executes the same pure plan and opens the tab via the iTerm2
    Python API. A launch failure (e.g. iterm2 not installed) is returned as a
    structured ``{launched: False, ...}`` result, never raised.
    """
    daemon_cli = _DAEMON_DIR / "it2agent_daemon.py"

    def launch(arguments: dict, command: str, plan) -> dict:
        argv = [sys.executable, str(daemon_cli), "spawn"]
        cwd = arguments.get("cwd")
        if isinstance(cwd, str) and cwd:
            argv += ["--dir", cwd]
        if arguments.get("home") is True:
            argv += ["--home"]
        for flag, key in (("--id", "id"), ("--role", "role"), ("--task", "task"), ("--status", "status")):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                argv += [flag, value]
        argv += ["--", command]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("spawn launch failed: %s", exc)
            return {"launched": False, "error": str(exc)}
        launched = result.returncode == 0
        return {
            "launched": launched,
            "returncode": result.returncode,
            "stderr": (result.stderr or "").strip()[:2000],
        }

    return launch


def _build_deps(logger: logging.Logger):
    from tools import Deps

    return Deps(broker=_make_broker_client(), spawn=_make_spawn_launcher(logger))


# --------------------------------------------------------------------------- #
# stdio JSON-RPC loop (the only I/O)
# --------------------------------------------------------------------------- #


def serve_stdio(deps, logger: logging.Logger, stdin=None, stdout=None) -> int:
    """Read newline-framed JSON-RPC requests from stdin; write responses to stdout.

    One request per line, one response line per non-notification, flushed
    immediately. EOF ends the loop cleanly (exit 0). A per-line failure is
    funneled through :func:`rpc.dispatch_line`, which never raises.
    """
    import rpc

    rx = stdin if stdin is not None else sys.stdin
    tx = stdout if stdout is not None else sys.stdout
    logger.info("serving MCP over stdio (%d tools)", len(_tool_count()))
    for line in rx:
        response = rpc.dispatch_line(line, deps)
        if response is None:
            continue
        tx.write(response + "\n")
        tx.flush()
    logger.info("stdin closed; shutting down")
    return 0


def _tool_count():
    import tools

    return tools.TOOLS


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="it2agent MCP surface: orchestration tools (spawn/assign/"
        "handoff/send_message/status/list_agents) over stdio JSON-RPC, backed by "
        "the Tier 1 daemon + Tier 2 broker.",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="bypass the agent.mcp feature-flag gate (local testing).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    logger = _build_logger(args.verbose)

    if not gate_open(args.no_gate):
        print(
            f"{PROG}: feature flag '{FLAG_KEY}' is OFF; refusing to start.\n"
            f"Enable it with:  it2agent-flag enable {FLAG_KEY}\n"
            f"(or run with --no-gate / IT2AGENT_FORCE=1 for local testing).",
            file=sys.stderr,
        )
        return 0

    deps = _build_deps(logger)
    try:
        return serve_stdio(deps, logger)
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
        return 0


if __name__ == "__main__":
    sys.exit(main())
