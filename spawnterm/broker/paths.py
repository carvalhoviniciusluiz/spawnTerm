#!/usr/bin/env python3
"""Per-user path resolution for the spawnTerm broker (#34).

Pure — no sqlite, no socket, no iTerm2. Just resolves where the broker's
durable state (sqlite db) and its unix domain socket live for the current user,
honoring the XDG base-directory spec with test-friendly overrides.

Precedence for the database:
  ``$SPAWNTERM_BROKER_DB`` (full path, mainly for tests) >
  ``$XDG_STATE_HOME/spawnterm/broker.db`` >
  ``~/.local/state/spawnterm/broker.db``

Precedence for the socket:
  ``$SPAWNTERM_BROKER_SOCK`` (full path, mainly for tests) >
  ``$XDG_RUNTIME_DIR/spawnterm/broker.sock`` >
  ``~/.local/state/spawnterm/broker.sock``

``$XDG_RUNTIME_DIR`` is the right home for a socket (ephemeral, per-session,
tmpfs) but it is not always set on macOS, hence the state-dir fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

_APP = "spawnterm"
_DB_NAME = "broker.db"
_SOCK_NAME = "broker.sock"


def _env(name: str) -> str | None:
    """Return a non-empty environment variable value, or None."""
    value = os.environ.get(name)
    return value if value else None


def _state_home() -> Path:
    base = _env("XDG_STATE_HOME")
    return Path(base).expanduser() if base else Path.home() / ".local" / "state"


def broker_db_path() -> Path:
    """Resolve the sqlite database path for this user."""
    override = _env("SPAWNTERM_BROKER_DB")
    if override:
        return Path(override).expanduser()
    return _state_home() / _APP / _DB_NAME


def broker_sock_path() -> Path:
    """Resolve the unix domain socket path for this user."""
    override = _env("SPAWNTERM_BROKER_SOCK")
    if override:
        return Path(override).expanduser()
    runtime = _env("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime).expanduser() / _APP / _SOCK_NAME
    return _state_home() / _APP / _SOCK_NAME
