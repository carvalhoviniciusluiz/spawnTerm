#!/usr/bin/env python3
"""Allow-list / policy config loading for the inbox (#17).

The I/O half of the policy: :mod:`policy` stays pure, so *reading* the tunable
allow-list from disk lives here. A :class:`~policy.PolicyConfig` is built from a
small TOML file merged over conservative built-in defaults.

Config path precedence (mirrors the flags helper, #11):
  ``$SPAWNTERM_INBOX_CONFIG`` (full path override, mainly for tests) >
  ``$XDG_CONFIG_HOME/spawnterm/inbox.toml`` >
  ``~/.config/spawnterm/inbox.toml``

File shape — a single ``[policy]`` table, every key optional::

    [policy]
    allow_list = ["git.status", "git.diff", "fs.read"]
    block_list = ["fs.rm_rf", "secrets.exfiltrate"]
    auto_scopes = ["read"]
    block_scopes = ["system"]
    max_auto_cost = 0.0
    block_cost = 5.0
    require_reversible_for_auto = true

A missing file, a missing ``[policy]`` table, or a missing key all fall back to
the built-in default for that key. The defaults are **conservative**: only a
handful of obviously read-only actions auto-approve, and only within the ``read``
scope at zero cost — everything else pages a human (deny-by-default).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Optional

from policy import PolicyConfig

# Conservative default allow-list: read-only actions only. Anything that mutates
# state is deliberately absent, so it defaults to NEEDS_HUMAN until an operator
# adds it to their own inbox.toml.
DEFAULT_ALLOW_LIST: tuple[str, ...] = (
    "git.status",
    "git.diff",
    "git.log",
    "git.show",
    "fs.read",
    "fs.list",
    "shell.readonly",
    "ls",
    "cat",
    "pwd",
)
DEFAULT_BLOCK_LIST: tuple[str, ...] = ()
DEFAULT_AUTO_SCOPES: tuple[str, ...] = ("read",)
DEFAULT_BLOCK_SCOPES: tuple[str, ...] = ("system",)
DEFAULT_MAX_AUTO_COST = 0.0
DEFAULT_BLOCK_COST: Optional[float] = None
DEFAULT_REQUIRE_REVERSIBLE_FOR_AUTO = True


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value if value else None


def config_path() -> Path:
    """Resolve the inbox config file path (see the module docstring)."""
    override = _env("SPAWNTERM_INBOX_CONFIG")
    if override:
        return Path(override).expanduser()
    base = _env("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "spawnterm" / "inbox.toml"


def default_config() -> PolicyConfig:
    """The built-in conservative policy (used when no file overrides a key)."""
    return PolicyConfig(
        allow_list=frozenset(DEFAULT_ALLOW_LIST),
        block_list=frozenset(DEFAULT_BLOCK_LIST),
        auto_scopes=frozenset(DEFAULT_AUTO_SCOPES),
        block_scopes=frozenset(DEFAULT_BLOCK_SCOPES),
        max_auto_cost=DEFAULT_MAX_AUTO_COST,
        block_cost=DEFAULT_BLOCK_COST,
        require_reversible_for_auto=DEFAULT_REQUIRE_REVERSIBLE_FOR_AUTO,
    )


def _load_policy_table(path: Path) -> dict[str, Any]:
    """Return the ``[policy]`` table, or ``{}`` if unreadable/absent."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    table = data.get("policy")
    return table if isinstance(table, dict) else {}


def _str_set(value: Any, fallback: frozenset[str]) -> frozenset[str]:
    """Coerce a TOML list of strings to a frozenset; fall back on wrong shape."""
    if not isinstance(value, list):
        return fallback
    return frozenset(str(item) for item in value)


def build_config(table: dict[str, Any]) -> PolicyConfig:
    """Merge a ``[policy]`` table over the defaults, pure (no file access)."""
    base = default_config()
    block_cost = table.get("block_cost", base.block_cost)
    if block_cost is not None:
        try:
            block_cost = float(block_cost)
        except (TypeError, ValueError):
            block_cost = base.block_cost
    return PolicyConfig(
        allow_list=_str_set(table.get("allow_list"), base.allow_list),
        block_list=_str_set(table.get("block_list"), base.block_list),
        auto_scopes=_str_set(table.get("auto_scopes"), base.auto_scopes),
        block_scopes=_str_set(table.get("block_scopes"), base.block_scopes),
        max_auto_cost=float(table.get("max_auto_cost", base.max_auto_cost)),
        block_cost=block_cost,
        require_reversible_for_auto=bool(
            table.get("require_reversible_for_auto", base.require_reversible_for_auto)
        ),
    )


def load_config(path: Optional[Path] = None) -> PolicyConfig:
    """Load the effective policy: defaults, with any ``inbox.toml`` merged over."""
    resolved = path if path is not None else config_path()
    return build_config(_load_policy_table(resolved))
