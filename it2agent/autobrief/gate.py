#!/usr/bin/env python3
"""Feature-flag gate for the SessionStart autobrief hook: ``agent.autobrief`` (#113).

Unlike the team bridge (whose install-into-a-project IS the opt-in, so its flag is
only a kill-switch), the autobrief flag is a **positive gate, default OFF**: the
hook injects the capabilities brief into a fresh Claude's context ONLY when
``agent.autobrief`` is explicitly ON. Installing the hook wires it into the
project's ``settings.local.json`; turning the flag on is the separate, deliberate
"actually inject" switch. This keeps a freshly-cloned dev environment quiet by
default and matches the issue contract (flag OFF ⇒ no additionalContext).

Reads flag state via the #11 ``it2agent_flag`` helper. Bypass for local testing
with ``--no-gate`` (wired by the CLI) or ``IT2AGENT_FORCE=1``. Any error reading
the flag is treated as OFF (fail-safe: an observer that cannot confirm it is
enabled stays silent).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FLAG_KEY = "agent.autobrief"

# Sibling flags helper: it2agent/flags/it2agent_flag.py.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def autobrief_enabled() -> bool:
    """Return True iff ``agent.autobrief`` is explicitly ON in config.toml.

    Absent / unset / ``false`` all return False. Fail-safe: any error (unreadable
    config, missing helper) returns False so the hook stays silent.
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import it2agent_flag  # type: ignore

        return it2agent_flag.is_enabled(FLAG_KEY)
    except Exception:  # noqa: BLE001 - unreadable ⇒ OFF (silent)
        return False


def gate_open(no_gate: bool = False) -> bool:
    """Whether the hook should emit. Bypassed by ``--no-gate`` / ``IT2AGENT_FORCE=1``.

    Emits only when the flag is explicitly ON (positive gate, default OFF).
    """
    if os.environ.get("IT2AGENT_FORCE") == "1":
        return True
    if no_gate:
        return True
    return autobrief_enabled()
