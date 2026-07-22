#!/usr/bin/env python3
"""Feature-flag gate for the inbox: ``agent.inbox`` (#17 over #11).

Same fail-safe convention as every other it2agent capability (the emitter, the
daemon dashboard, the broker): the flag defaults **OFF**, and the inbox is a
**no-op** when it is off. Reuses the #11 ``it2agent_flag.is_enabled`` helper
(imported from the sibling ``it2agent/flags`` dir), falling back to shelling out
to ``it2agent-flag`` on PATH, and finally to OFF if neither is reachable.

Bypass for local testing with ``--no-gate`` (wired by the CLI) or the
``IT2AGENT_FORCE=1`` environment override — identical to the emitter/daemon.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

FLAG_KEY = "agent.inbox"

# Sibling flags helper: it2agent/flags/it2agent_flag.py.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def inbox_enabled() -> bool:
    """Return True iff ``agent.inbox`` is ON. Fail-safe: OFF on error."""
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import it2agent_flag  # type: ignore

        return it2agent_flag.is_enabled(FLAG_KEY)
    except Exception:  # noqa: BLE001 - fall through to the shell helper / OFF
        pass
    # Fall back to the shell helper if the Python import was unavailable.
    if shutil.which("it2agent-flag") is None:
        return False
    try:
        result = subprocess.run(
            ["it2agent-flag", FLAG_KEY],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def gate_open(no_gate: bool = False) -> bool:
    """Whether the inbox should act. Bypassed by ``--no-gate`` / ``IT2AGENT_FORCE=1``."""
    if os.environ.get("IT2AGENT_FORCE") == "1":
        return True
    if no_gate:
        return True
    return inbox_enabled()
