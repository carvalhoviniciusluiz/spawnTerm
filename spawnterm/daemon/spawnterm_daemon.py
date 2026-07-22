#!/usr/bin/env python3
"""spawnterm-daemon — Tier 1 iTerm2 Python API orchestration daemon (#26).

Connects to the iTerm2 Python API websocket and **survives** the session via
``iterm2.run_forever``. It maintains an in-memory registry of sessions and
ingests agent messages / idle state. Auth is the standard iTerm2 mechanism: the
``iterm2`` library reads the API cookie / ``ITERM2_COOKIE`` — we never hand-roll
auth.

``scope:external-tooling`` — runs *on* iTerm2's Python API; never modifies
iTerm2 source.

Feature-flag gate: like every spawnTerm capability, the daemon is off by
default. It starts only when ``spawnterm.daemon`` is ON (checked via the #11
flag helper). If the flag is OFF/absent it prints a clear message and exits 0.
Bypass for local testing with ``--no-gate`` or ``SPAWNTERM_FORCE=1``.

Architecture (testability): the real logic lives in the pure modules
``registry`` and ``envelope`` (no ``iterm2`` import — unit-tested in
``tests/``). This file and ``adapter`` are the thin iTerm2 I/O layer and import
``iterm2`` lazily inside ``run``/the adapter, so the pure path imports without
the package installed.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

PROG = "spawnterm-daemon"
FLAG_KEY = "spawnterm.daemon"

# Sibling flags helper (#11): spawnterm/flags/{spawnterm_flag.py,spawnterm-flag}.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def flag_enabled() -> bool:
    """Return True iff ``spawnterm.daemon`` is ON.

    Prefers importing the #11 Python helper (``spawnterm_flag.is_enabled``);
    falls back to shelling out to the ``spawnterm-flag`` script. Fail-safe: if
    neither is reachable, treat the flag as OFF (capabilities default off).
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import spawnterm_flag  # type: ignore

        return spawnterm_flag.is_enabled(FLAG_KEY)
    except Exception:  # noqa: BLE001 - fall back to the CLI helper
        pass

    helper = _FLAGS_DIR / "spawnterm-flag"
    candidate = str(helper) if helper.exists() else "spawnterm-flag"
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
    if no_gate or os.environ.get("SPAWNTERM_FORCE") == "1":
        return True
    return flag_enabled()


def _build_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("spawnterm.daemon")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s spawnterm.daemon: %(message)s")
    )
    logger.handlers = [handler]
    logger.propagate = False
    return logger


def _run_forever(logger: logging.Logger) -> None:
    """Lazy-import iterm2 and hand control to its run_forever loop."""
    import iterm2  # lazy: only needed when the gate is open.

    from adapter import DaemonAdapter
    from registry import Registry

    registry = Registry()

    async def main(connection):
        adapter = DaemonAdapter(connection, registry, logger)
        logger.info("connected to iTerm2 Python API; starting monitors")
        # Register the agent-dashboard status-bar component (#29). Self-gated on
        # spawnterm.status_board; a no-op when that flag is OFF.
        from dashboard import maybe_register_dashboard

        await maybe_register_dashboard(connection, logger)
        await adapter.run()

    iterm2.run_forever(main)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="spawnTerm Tier 1 daemon: session registry + agent ingest + idle.",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="bypass the spawnterm.daemon feature-flag gate (local testing).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # spawnterm spawn subcommand (Tier 1.2, #27): route BEFORE the daemon flag
    # gate — spawning a tab is core and always allowed (only identity tagging is
    # gated, on spawnterm.status_board). See spawn_cli below.
    if raw and raw[0] == "spawn":
        return spawn_cli(raw[1:])
    args = parse_args(raw)
    logger = _build_logger(args.verbose)

    if not gate_open(args.no_gate):
        print(
            f"{PROG}: feature flag '{FLAG_KEY}' is OFF; refusing to start.\n"
            f"Enable it with:  spawnterm-flag enable {FLAG_KEY}\n"
            f"(or run with --no-gate / SPAWNTERM_FORCE=1 for local testing).",
            file=sys.stderr,
        )
        return 0

    try:
        _run_forever(logger)
    except ImportError as exc:
        print(
            f"{PROG}: the 'iterm2' Python package is required to run the daemon "
            f"({exc}). Install it with:  pip3 install iterm2",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
    return 0


# ---------------------------------------------------------------------------
# spawn subcommand (Tier 1.2, #27) — self-contained block.
#
# `spawnterm-daemon spawn [opts] -- <command>` opens a new iTerm2 tab running an
# agent, inheriting the spawner's cwd (or --dir/--home), and stamps its identity
# (dot-free user.agent_* vars) via the pure spawn.build_spawn_plan +
# adapter.spawn_agent. This is the Python-API twin of the reference shell
# wrapper spawnterm/spawn/spawnterm-spawn (#10): use the SHELL path on stock
# iTerm2 with no daemon running; use THIS daemon path when the Tier 1 daemon /
# Python API is already up (it opens the tab via async_create_tab and sets vars
# through the API rather than AppleScript + a `cd` written into the session).
#
# Gate: identity tagging gates on spawnterm.status_board (NOT spawnterm.daemon)
# — the same flag spawnterm-emit self-gates on. When it is OFF the tab still
# spawns; the plan's variable list is empty so no identity is stamped.
STATUS_BOARD_FLAG = "spawnterm.status_board"


def status_board_enabled(no_gate: bool = False) -> bool:
    """Return True iff identity tagging should happen: --no-gate/SPAWNTERM_FORCE
    force it on, otherwise the spawnterm.status_board flag decides. Reuses the
    #11 flag helper; fail-safe OFF if it is unreachable (capabilities default
    off)."""
    if no_gate or os.environ.get("SPAWNTERM_FORCE") == "1":
        return True
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import spawnterm_flag  # type: ignore

        return spawnterm_flag.is_enabled(STATUS_BOARD_FLAG)
    except Exception:  # noqa: BLE001 - fail safe to OFF
        return False


def _parse_spawn_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{PROG} spawn",
        description="Open a new iTerm2 tab running a tagged agent via the "
        "Python API (cwd inheritance + dot-free identity vars).",
    )
    parser.add_argument("--dir", default=None, help="working directory for the new tab.")
    parser.add_argument("--home", action="store_true", help="open in $HOME (excludes --dir).")
    parser.add_argument("--role", default="", help="agent_role.")
    parser.add_argument("--task", default="", help="agent_task.")
    parser.add_argument("--id", dest="agent_id", default="", help="agent_id.")
    parser.add_argument("--status", default="busy", help="agent_status (busy/blocked/done/idle).")
    parser.add_argument("--no-gate", action="store_true", help="bypass the status_board gate.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- <command> [args...]")
    return parser.parse_args(argv)


def spawn_cli(argv: list[str]) -> int:
    import shlex

    from spawn import SpawnPlanError, build_spawn_plan

    args = _parse_spawn_args(argv)
    logger = _build_logger(args.verbose)

    command_args = list(args.command)
    if command_args and command_args[0] == "--":
        command_args = command_args[1:]
    if not command_args:
        print(f"{PROG} spawn: no command given (use -- <command> [args...]).", file=sys.stderr)
        return 2
    command = shlex.join(command_args)

    tag_identity = status_board_enabled(args.no_gate)
    try:
        plan = build_spawn_plan(
            spawner_cwd=os.getcwd(),
            dir_override=args.dir,
            use_home=args.home,
            home=os.path.expanduser("~"),
            agent_id=args.agent_id,
            role=args.role,
            task=args.task,
            status=args.status,
            tag_identity=tag_identity,
        )
    except SpawnPlanError as exc:
        print(f"{PROG} spawn: {exc}", file=sys.stderr)
        return 2

    if not plan.tagged:
        logger.info(
            "spawn: '%s' is OFF; spawning untagged tab (identity vars skipped).",
            STATUS_BOARD_FLAG,
        )

    try:
        import iterm2  # lazy: only needed to actually open the tab.

        from adapter import DaemonAdapter
        from registry import Registry

        async def _main(connection):
            adapter = DaemonAdapter(connection, Registry(), logger)
            await adapter.spawn_agent(plan, command)

        iterm2.run_until_complete(_main)
    except ImportError as exc:
        print(
            f"{PROG} spawn: the 'iterm2' Python package is required "
            f"({exc}). Install it with:  pip3 install iterm2",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
