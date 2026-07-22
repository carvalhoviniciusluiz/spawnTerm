#!/usr/bin/env python3
"""spawnterm language selector (Python implementation).

Reads and writes the active-language setting ``[settings] language`` in the
shared config (``$XDG_CONFIG_HOME/spawnterm/config.toml``). This is the Python
twin of the canonical ``spawnterm-lang`` shell script.

CRITICAL: writing ``[settings]`` performs a read-modify-write that PRESERVES an
existing ``[features]`` table (spawnterm-flag's data). The two tables are
serialized deterministically so the shell and Python twins produce byte-identical
files.

Docs: spawnterm/i18n/README.md
"""

from __future__ import annotations

import sys
import tomllib

import spawnterm_i18n as i18n

FEATURE_PREFIX = "spawnterm."
# Canonical feature order, mirrored from spawnterm-flag so a preserved
# [features] table is re-serialized in a stable, parity-friendly order.
KNOWN_FLAGS = (
    "status_board", "worktree_isolation", "messaging", "agent_inbox",
    "cost_dashboard", "janitor", "mcp", "daemon", "broker", "review",
    "tmux", "claude_statusbar", "agent_menubar", "codex_status",
)


def _read_features() -> dict[str, bool]:
    """Return the present ``[features]`` flags (known keys) as booleans."""
    path = i18n.config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    features = data.get("features")
    if not isinstance(features, dict):
        return {}
    present: dict[str, bool] = {}
    for cap in KNOWN_FLAGS:
        key = FEATURE_PREFIX + cap
        if key in features:
            present[cap] = features[key] is True
    return present


def _serialize(features: dict[str, bool], language: str) -> str:
    """Serialize [features] (if any) then [settings] deterministically."""
    lines = [
        "# spawnterm config",
        "# Managed by spawnterm-flag (features) and spawnterm-lang (settings).",
    ]
    if features:
        lines.append("[features]")
        for cap in KNOWN_FLAGS:
            if cap in features:
                state = "true" if features[cap] else "false"
                lines.append(f'"{FEATURE_PREFIX}{cap}" = {state}')
    lines.append("[settings]")
    lines.append(f'language = "{language}"')
    return "\n".join(lines) + "\n"


def _write(language: str) -> None:
    """Persist ``language`` while preserving any existing [features] table."""
    features = _read_features()
    path = i18n.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(features, language), encoding="utf-8")


USAGE = """usage: spawnterm-lang <command> [lang]

Commands:
  get                  print the resolved active language (en or pt-BR)
  set <en|pt-BR|system>  set [settings] language (preserves [features])
  list                 print the available languages (catalogs found)
  -h, --help           show this help

Config: $XDG_CONFIG_HOME/spawnterm/config.toml (falls back to ~/.config/...).
Default language is en. get/list never create a file."""


def _err(msg: str) -> None:
    print(f"spawnterm-lang: {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _err("missing command")
        print(USAGE, file=sys.stderr)
        return 2

    command = argv[0]
    rest = argv[1:]

    if command in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if command == "get":
        print(i18n.active_language())
        return 0
    if command == "list":
        for lang in i18n.available_languages():
            print(lang)
        return 0
    if command == "set":
        if len(rest) != 1:
            _err("set requires exactly one <lang>")
            return 2
        lang = rest[0]
        if lang not in i18n.VALID_LANGUAGES:
            _err(f"invalid language: {lang} (valid: {', '.join(i18n.VALID_LANGUAGES)})")
            return 2
        _write(lang)
        return 0

    _err(f"unknown command: {command}")
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
