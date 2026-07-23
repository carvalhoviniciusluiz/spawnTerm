#!/usr/bin/env python3
"""it2agent feature-flag helper (Python implementation).

Every it2agent capability is an individually toggleable, per-user feature
flag that defaults OFF. This module is the Python half of the shell/Python
parity pair (see the sibling ``it2agent-flag`` shell script). It exposes an
importable ``is_enabled(key) -> bool`` for the daemon and a CLI identical to
the shell tool when run as ``python3 it2agent_flag.py`` or ``python3 -m``.

Config lives at ``$XDG_CONFIG_HOME/it2agent/config.toml`` (falling back to
``~/.config/it2agent/config.toml``). Flags are quoted keys under a
``[features]`` table, e.g. ``"agent.messaging" = true``. A missing file,
a missing table, or a missing key all read as OFF. Reads never write a file.

The writer does a read-modify-write that emits a single canonical ``[features]``
table, keeping the shell/Python twins byte-identical.

Docs: it2agent/docs/feature-flags.md
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

PREFIX = "agent."

# Seeded schema: capability name -> one-line description. Order is canonical
# and drives both `list` output and the deterministic file serializer. This
# MUST stay in sync with KNOWN_FLAGS in the sibling `it2agent-flag` shell tool
# (names only there) and with descriptionForCapability: in
# sources/Settings/iTermAgentCapabilities.m (which mirrors these UI blurbs).
KNOWN_FLAGS: dict[str, str] = {
    "status_board": "Legacy: colors the tab and sets a status variable to show agent state. Prefer Native Tab Status.",
    "worktree_isolation": "Gives each agent its own git worktree and a dedicated port so they never collide.",
    "messaging": "Lets agents send messages to each other across tabs through the broker.",
    "inbox": "Keeps a durable per-agent inbox so messages survive restarts.",
    "cost_dashboard": "Shows a running dashboard of token usage and cost.",
    "janitor": "Cleans up stale worktrees and sessions in the background.",
    "mcp": "Exposes it2agent to your agents as an MCP server.",
    "daemon": "Runs the orchestration daemon that tracks agents and their idle/busy state.",
    "broker": "Runs the durable broker - mailbox, registry, and state over a local socket.",
    "review": "Adds a per-agent diff view to approve-and-merge or request changes on a worktree.",
    "tmux": "Runs agents inside a tmux -CC session so they survive a quit or crash and can reattach.",
    "claude_statusbar": "Adds a status-bar item summarizing Claude Code sessions (Waiting, Working, Idle).",
    "menubar": "Adds a menu-bar item with a live count of busy AI agents.",
    "codex_status": "Shows Codex CLI working/idle activity in the tab status.",
    "native_status": "Publishes agent state to iTerm2's native tab status and Cockpit via OSC 21337.",
    "team_bridge": "Mirrors Claude Code agent-teams state into the durable broker so it survives the lead session's death.",
    "canonical_port": "The focused agent also answers on the normal localhost port (e.g. 3000), not just its dynamic one.",
    "isolate_docker": "Sets COMPOSE_PROJECT_NAME per agent so Docker Compose stacks don't collide.",
    "isolate_db": "Exports a per-agent Postgres schema/search_path so agents don't share DB state.",
}


def _env(name: str) -> str | None:
    """Return a non-empty environment variable value, or None."""
    value = os.environ.get(name)
    return value if value else None


def config_path() -> Path:
    """Resolve the config file path.

    Precedence: ``$IT2AGENT_CONFIG`` (full path override, mainly for tests) >
    ``$XDG_CONFIG_HOME/it2agent/config.toml`` > ``~/.config/it2agent/config.toml``.
    """
    override = _env("IT2AGENT_CONFIG")
    if override:
        return Path(override).expanduser()
    base = _env("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "it2agent" / "config.toml"


def normalize(key: str) -> str:
    """Strip the optional ``agent.`` prefix, returning the capability name."""
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

    ``key`` may be given with or without the ``agent.`` prefix. A missing
    file, table, or key all read as OFF (False). Unknown capabilities read OFF.
    """
    cap = normalize(key)
    features = _load_features()
    value = features.get(PREFIX + cap)
    return value is True


def _canonical_body(values: dict[str, bool]) -> str:
    """Serialize the full seeded schema as a single [features] table."""
    lines = [
        "# it2agent config",
        "# Managed by it2agent-flag.",
        "# Docs: it2agent/docs/feature-flags.md",
        "[features]",
    ]
    for cap in KNOWN_FLAGS:
        state = "true" if values.get(cap, False) else "false"
        lines.append(f'"{PREFIX}{cap}" = {state}')
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


USAGE = """usage: it2agent-flag <command|key>

Query a flag (default command):
  it2agent-flag <key>          print 1 and exit 0 if ON; print 0 and exit 1 otherwise
                                (<key> may be given with or without the agent. prefix)

Commands:
  list                          print every known flag and its effective on/off state
  enable  <key>                 turn a flag ON  (creates the config + full schema if absent)
  disable <key>                 turn a flag OFF (creates the config + full schema if absent)
  path                          print the resolved config file path
  -h, --help                    show this help

Config: $XDG_CONFIG_HOME/it2agent/config.toml (falls back to ~/.config/...).
All flags default OFF. Reads never create a file."""


def _err(msg: str) -> None:
    print(f"it2agent-flag: {msg}", file=sys.stderr)


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
