#!/usr/bin/env python3
"""Feature-flag gate for the team bridge: ``agent.team_bridge`` (#92, revised #96).

Per-project model (#96): installing the hook into a project's settings file IS
the opt-in, so the team-hook RUNS by default once installed. The global
``agent.team_bridge`` flag is now only an OPTIONAL kill-switch — an EXPLICIT
``false`` in ``config.toml`` forces the bridge OFF; unset/absent/true all RUN.
This inverts the old default-OFF gate (before #96 the flag had to be turned ON).

Fail-safe direction also inverts: if the config cannot be read we treat the
flag as NOT-kill-switched (i.e. the installed hook still runs), because the
operator's explicit install is the stronger opt-in signal.

Reads the raw flag value via the #11 ``it2agent_flag`` helper (imported from the
sibling ``it2agent/flags`` dir) so we can distinguish an EXPLICIT ``false`` from
an absent key — ``is_enabled`` collapses both to False and cannot tell them
apart. Bypass the gate for local testing with ``--no-gate`` (wired by the CLI)
or the ``IT2AGENT_FORCE=1`` environment override.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

FLAG_KEY = "agent.team_bridge"

# Sibling flags helper: it2agent/flags/it2agent_flag.py.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def team_bridge_kill_switched() -> bool:
    """Return True iff ``agent.team_bridge`` is EXPLICITLY ``false`` in config.toml.

    Absent / unset / ``true`` all return False (do NOT kill). Fail-safe: any
    error (unreadable config, missing helper) returns False so the installed
    hook still runs.
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import it2agent_flag  # type: ignore

        path = it2agent_flag.config_path()
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        features = data.get("features")
        if not isinstance(features, dict):
            return False
        return features.get(FLAG_KEY) is False
    except Exception:  # noqa: BLE001 - unreadable ⇒ not kill-switched (runs)
        return False


def gate_open(no_gate: bool = False) -> bool:
    """Whether the bridge should act. Bypassed by ``--no-gate`` / ``IT2AGENT_FORCE=1``.

    Runs unless the flag is an explicit kill-switch (see module docstring).
    """
    if os.environ.get("IT2AGENT_FORCE") == "1":
        return True
    if no_gate:
        return True
    return not team_bridge_kill_switched()
