#!/usr/bin/env python3
"""spawnterm feature-flag helper (Python implementation).

Every spawnTerm capability is an individually toggleable, per-user feature
flag that defaults OFF. This module is the Python half of the shell/Python
parity pair (see the sibling ``spawnterm-flag`` shell script). It exposes an
importable ``is_enabled(key) -> bool`` for the daemon and a CLI identical to
the shell tool when run as ``python3 spawnterm_flag.py`` or ``python3 -m``.

Config lives at ``$XDG_CONFIG_HOME/spawnterm/config.toml`` (falling back to
``~/.config/spawnterm/config.toml``). Flags are quoted keys under a
``[features]`` table, e.g. ``"spawnterm.messaging" = true``. A missing file,
a missing table, or a missing key all read as OFF. Reads never write a file.

The config file may also carry a ``[settings]`` table (owned by spawnterm-lang,
e.g. ``language``). The writer here does a read-modify-write that PRESERVES an
existing ``[settings]`` table, mirroring how spawnterm-lang preserves
``[features]``. Both tools share one canonical serialization — ``[features]``
first, then ``[settings]`` — so repeated writes by either are stable and the
shell/Python twins stay byte-identical.

Docs: spawnterm/docs/feature-flags.md
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

PREFIX = "spawnterm."

# Seeded schema: capability name -> one-line description. Order is canonical
# and drives both `list` output and the deterministic file serializer. This
# MUST stay in sync with KNOWN_FLAGS in the sibling `spawnterm-flag` shell tool.
KNOWN_FLAGS: dict[str, str] = {
    "status_board": "Tier 0 escape-code status board (agents emit state; iTerm2 paints it).",
    "worktree_isolation": "Per-agent git-worktree + $PORT isolation.",
    "messaging": "Cross-tab agent-to-agent messaging via the broker.",
    "agent_inbox": "Durable per-agent inbox surface.",
    "cost_dashboard": "Token/cost dashboard.",
    "janitor": "Background cleanup of stale worktrees/sessions.",
    "mcp": "MCP surface exposing spawnterm to agents.",
    "daemon": "Tier 1 iTerm2 Python API orchestration daemon (registry + ingest/idle).",
    "broker": "Tier 2 external broker (durable sqlite mailbox/registry/state/ack over a unix socket).",
    "review": "Per-agent diff/review surface (show worktree diff vs base; approve->merge / request-changes).",
    "tmux": "Tier 3 tmux -CC persistence: spawn agents inside a native tmux -CC session so windows/agents survive quit/crash and can be reattached.",
    "claude_statusbar": "Claude Code session status aggregator status-bar component (Waiting/Working/Idle across all windows).",
    "agent_menubar": "Menu bar status item showing a live count badge of busy AI agents (imported from gnachman/iTerm2#670).",
    "codex_status": "Show Codex CLI working/idle activity in the tab status by decoding the braille-spinner title prefix (imported from gnachman/iTerm2#673).",
}


def _env(name: str) -> str | None:
    """Return a non-empty environment variable value, or None."""
    value = os.environ.get(name)
    return value if value else None


def config_path() -> Path:
    """Resolve the config file path.

    Precedence: ``$SPAWNTERM_CONFIG`` (full path override, mainly for tests) >
    ``$XDG_CONFIG_HOME/spawnterm/config.toml`` > ``~/.config/spawnterm/config.toml``.
    """
    override = _env("SPAWNTERM_CONFIG")
    if override:
        return Path(override).expanduser()
    base = _env("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "spawnterm" / "config.toml"


def normalize(key: str) -> str:
    """Strip the optional ``spawnterm.`` prefix, returning the capability name."""
    key = key.strip()
    if key.startswith(PREFIX):
        return key[len(PREFIX):]
    return key


def _load_features() -> dict:
    """Return the ``[features]`` table, or an empty dict if unreadable/absent."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    features = data.get("features")
    return features if isinstance(features, dict) else {}


def is_enabled(key: str) -> bool:
    """Return True iff the flag for ``key`` is present and truthy in the config.

    ``key`` may be given with or without the ``spawnterm.`` prefix. A missing
    file, table, or key all read as OFF (False). Unknown capabilities read OFF.
    """
    cap = normalize(key)
    features = _load_features()
    value = features.get(PREFIX + cap)
    return value is True


def _load_settings_language() -> str | None:
    """Return the ``[settings] language`` string to preserve, or None if absent.

    The flag writer never fabricates a ``[settings]`` table; it only re-emits one
    that already exists so a language chosen via spawnterm-lang is not clobbered.
    """
    path = config_path()
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    settings = data.get("settings")
    if isinstance(settings, dict):
        language = settings.get("language")
        if isinstance(language, str):
            return language
    return None


def _canonical_body(values: dict[str, bool]) -> str:
    """Serialize [features] (full seeded schema) then a preserved [settings].

    Canonical table order shared with spawnterm-lang: [features] first, then
    [settings]. A table is emitted only when it has content — [settings] appears
    only when the config already carries a language.
    """
    lines = [
        "# spawnterm config",
        "# Managed by spawnterm-flag (features) and spawnterm-lang (settings).",
        "# Docs: spawnterm/docs/feature-flags.md",
        "[features]",
    ]
    for cap in KNOWN_FLAGS:
        state = "true" if values.get(cap, False) else "false"
        lines.append(f'"{PREFIX}{cap}" = {state}')
    language = _load_settings_language()
    if language is not None:
        lines.append("[settings]")
        lines.append(f'language = "{language}"')
    return "\n".join(lines) + "\n"


def _current_values() -> dict[str, bool]:
    """Read the effective on/off state of every known flag (default False)."""
    features = _load_features()
    return {cap: features.get(PREFIX + cap) is True for cap in KNOWN_FLAGS}


def _write(values: dict[str, bool]) -> Path:
    """Write the canonical config file, creating the directory if needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_body(values), encoding="utf-8")
    return path


def _set(cap: str, enabled: bool) -> Path:
    values = _current_values()
    values[cap] = enabled
    return _write(values)


USAGE = """usage: spawnterm-flag <command|key>

Query a flag (default command):
  spawnterm-flag <key>          print 1 and exit 0 if ON; print 0 and exit 1 otherwise
                                (<key> may be given with or without the spawnterm. prefix)

Commands:
  list                          print every known flag and its effective on/off state
  enable  <key>                 turn a flag ON  (creates the config + full schema if absent)
  disable <key>                 turn a flag OFF (creates the config + full schema if absent)
  path                          print the resolved config file path
  -h, --help                    show this help

Config: $XDG_CONFIG_HOME/spawnterm/config.toml (falls back to ~/.config/...).
All flags default OFF. Reads never create a file."""


def _err(msg: str) -> None:
    print(f"spawnterm-flag: {msg}", file=sys.stderr)


def _cmd_query(key: str) -> int:
    cap = normalize(key)
    if cap not in KNOWN_FLAGS:
        _err(f"unknown flag key: {key} (treating as OFF)")
    if is_enabled(key):
        print("1")
        return 0
    print("0")
    return 1


def _cmd_list() -> int:
    values = _current_values()
    for cap in KNOWN_FLAGS:
        state = "on" if values[cap] else "off"
        print(f"{PREFIX + cap:<30} {state}")
    return 0


def _cmd_set(enabled: bool, args: list[str]) -> int:
    verb = "enable" if enabled else "disable"
    if len(args) != 1:
        _err(f"{verb} requires exactly one <key>")
        return 2
    cap = normalize(args[0])
    if cap not in KNOWN_FLAGS:
        _err(f"unknown flag key: {args[0]}")
        return 2
    _set(cap, enabled)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _err("missing command or key")
        print(USAGE, file=sys.stderr)
        return 2

    command = argv[0]
    rest = argv[1:]

    if command in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if command == "list":
        return _cmd_list()
    if command == "path":
        print(config_path())
        return 0
    if command == "enable":
        return _cmd_set(True, rest)
    if command == "disable":
        return _cmd_set(False, rest)

    # Default: treat the single argument as a flag key to query.
    if rest:
        _err(f"unexpected extra arguments after '{command}'")
        return 2
    return _cmd_query(command)


if __name__ == "__main__":
    sys.exit(main())
