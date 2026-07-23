#!/usr/bin/env python3
"""Shared Claude Code settings-file mechanism for it2agent hooks (#113).

This module is the reusable **mechanism** the project convention mandates: every
it2agent integration that needs Claude Code to pick up a hook writes it to the
active project's gitignored ``<git-root>/.claude/settings.local.json`` with a
symmetric, safe install/uninstall. See ``it2agent/docs/claude-config-convention.md``.

It generalizes the settings-editing logic first proven in the team bridge
(``it2agent/team/it2agent_team_hook.py``, #92/#96) so new Claude-config flags
plug into ONE implementation instead of re-rolling git-root resolution, deep
merge, gitignore management, and marker-based uninstall. The team hook predates
this kit and keeps its own copy for now; new hooks (starting with the SessionStart
autobrief, #113) build on this.

Everything here is pure filesystem + JSON: no broker, no iterm2, no network, so
it is fully unit-testable against a temp repo. The two scopes are:

  * ``user``    → ``~/.claude/settings.json`` (global, distributed with the user)
  * ``project`` → ``<git-root-of-cwd>/.claude/settings.local.json`` — machine-local,
                  gitignored, per-project but NOT committed. This is the scope the
                  convention uses: a project-*committed* settings.json would run
                  hooks UNGATED for anyone who checks it out (CVE-2025-59536), so
                  we deliberately target the gitignored .local file instead.

``IT2AGENT_CLAUDE_SETTINGS`` (a full path) always wins so tests and operators can
redirect writes away from any real ``~/.claude`` or project file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

GITIGNORE_ENTRY = ".claude/settings.local.json"


class NotAGitRepoError(Exception):
    """Raised when ``--scope project`` is requested from outside a git repo."""


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #


def find_git_root(start: Path) -> Optional[Path]:
    """Walk up from ``start`` for a ``.git`` entry (dir or file); repo root or None.

    A ``.git`` file (submodules / linked worktrees) counts as well as a dir, so
    this works from inside a worktree. Never raises.
    """
    try:
        current = start.resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def settings_path(scope: str = "user", start_dir: Optional[Path] = None) -> Path:
    """Resolve the Claude Code settings file for ``scope``.

    ``IT2AGENT_CLAUDE_SETTINGS`` (a full path) always wins. Otherwise:

      * ``user``    → ``~/.claude/settings.json``
      * ``project`` → ``<git-root-of(start_dir or cwd)>/.claude/settings.local.json``

    Project scope raises :class:`NotAGitRepoError` when ``start_dir`` (default:
    the current working directory) is not inside a git repository — we never
    silently fall back to the global file.
    """
    override = os.environ.get("IT2AGENT_CLAUDE_SETTINGS")
    if override:
        return Path(override).expanduser()
    if scope == "project":
        root = find_git_root(start_dir or Path.cwd())
        if root is None:
            raise NotAGitRepoError(
                "not inside a git repository; --scope project needs a project root"
            )
        return root / ".claude" / "settings.local.json"
    return Path.home() / ".claude" / "settings.json"


# --------------------------------------------------------------------------- #
# Settings file I/O
# --------------------------------------------------------------------------- #


def load_settings(path: Path) -> dict:
    """Read a settings JSON file into a dict; ``{}`` if absent or unparseable."""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(path: Path, settings: dict) -> None:
    """Write ``settings`` as pretty JSON, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Marker-based hook install / uninstall (deep-merge, idempotent, surgical)
# --------------------------------------------------------------------------- #


def command_is_ours(command: Any, marker: str) -> bool:
    """True iff a hook command string contains our ``marker`` (a tool basename)."""
    return isinstance(command, str) and marker in command


def install_event_hooks(settings: dict, events: dict[str, str], marker: str) -> dict:
    """Deep-merge ``events`` (event → command) into ``settings['hooks']``.

    NEVER overwrites unrelated keys or other tools' hooks. Idempotent: an existing
    entry of ours (matched by ``marker``) for a given event is left as-is rather
    than duplicated. Returns the same ``settings`` dict (mutated) for chaining.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    for event, command in events.items():
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        already = any(
            isinstance(group, dict)
            and isinstance(group.get("hooks"), list)
            and any(
                isinstance(h, dict) and command_is_ours(h.get("command"), marker)
                for h in group["hooks"]
            )
            for group in groups
        )
        if already:
            continue
        groups.append({"hooks": [{"type": "command", "command": command}]})
    return settings


def uninstall_event_hooks(settings: dict, marker: str) -> dict:
    """Remove ONLY hook entries whose command matches ``marker``.

    Prunes empty hook groups, then empty event lists, then an empty ``hooks``
    table — but never touches any other key. Idempotent. Returns ``settings``.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            inner = group.get("hooks")
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept_inner = [
                h
                for h in inner
                if not (isinstance(h, dict) and command_is_ours(h.get("command"), marker))
            ]
            if not kept_inner:
                # The whole group was ours ⇒ drop it.
                continue
            if len(kept_inner) != len(inner):
                group = dict(group)
                group["hooks"] = kept_inner
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def is_installed(settings: dict, marker: str) -> bool:
    """True iff any hook entry matching ``marker`` is present in ``settings``."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            inner = group.get("hooks")
            if not isinstance(inner, list):
                continue
            for entry in inner:
                if isinstance(entry, dict) and command_is_ours(entry.get("command"), marker):
                    return True
    return False


# --------------------------------------------------------------------------- #
# .gitignore management (belt-and-suspenders over Claude Code's own convention)
# --------------------------------------------------------------------------- #


def gitignore_covers(lines: list[str], entry: str) -> bool:
    """True iff a ``.gitignore`` line already ignores ``entry``.

    Heuristic (no git invocation, so this stays hermetic and unit-testable): an
    uncommented line equals the exact path or a broader pattern that clearly
    covers it (the ``.claude`` dir, or the bare filename anywhere).
    """
    patterns = {
        entry,
        "/" + entry,
        ".claude/",
        "/.claude/",
        ".claude",
        "/.claude",
        "settings.local.json",
        "**/settings.local.json",
    }
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in patterns:
            return True
    return False


def ensure_gitignored(root: Path, entry: str = GITIGNORE_ENTRY) -> bool:
    """Ensure ``<root>/.gitignore`` ignores ``entry``.

    Creates ``.gitignore`` if missing; appends our entry (with a comment) only
    when nothing already covers it. Returns True iff it wrote an addition.
    Idempotent and best-effort (a write failure returns False, never raises).
    """
    gitignore = root / ".gitignore"
    text = ""
    if gitignore.is_file():
        try:
            text = gitignore.read_text(encoding="utf-8")
        except OSError:
            text = ""
    if gitignore_covers(text.splitlines(), entry):
        return False
    prefix = text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    addition = ""
    if prefix.strip():
        addition += "\n"  # visual separation from existing content
    addition += "# it2agent: machine-local Claude Code settings (do not commit)\n"
    addition += entry + "\n"
    try:
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        gitignore.write_text(prefix + addition, encoding="utf-8")
    except OSError:
        return False
    return True
