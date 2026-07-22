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
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
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


if __name__ == "__main__":
    sys.exit(main())
