#!/usr/bin/env python3
"""it2agent SessionStart autobrief hook — discovery on session start (#113).

When a Claude Code session starts inside a project where this hook is installed
AND ``agent.autobrief`` is ON, the hook injects a short it2agent capabilities
brief into the model's context via the documented SessionStart
``additionalContext`` channel, so a fresh agent is born knowing the agentic
tooling exists and how to reach it — always current, because the brief is
rendered from the live flag schema + MCP tool registry (see it2agent_guide.py).

Wiring: registered as ONE Claude Code ``SessionStart`` hook whose command is this
tool with the ``session-start`` verb. On the event path it reads the hook JSON on
stdin and, when the gate is open, prints exactly:

    {"hookSpecificOutput": {"hookEventName": "SessionStart",
                            "additionalContext": "<brief>"}}

CRITICAL observer contract (Claude Code hooks): the event path **ALWAYS exits 0**
and writes stdout ONLY the additionalContext JSON when the gate is open. Under
every other condition — flag OFF, not installed, malformed/empty stdin, render
failure, any exception — it writes NOTHING to stdout and still exits 0, so it can
never block or steer Claude Code (only exit code 2 blocks). Diagnostics go to
stderr.

Install/uninstall reuse the shared project-local settings mechanism
(``it2agent/hookkit/claude_settings.py``): ``--scope project`` deep-merges a single
SessionStart entry into ``<git-root>/.claude/settings.local.json`` (gitignored,
never committed) and ``--scope user`` targets ``~/.claude/settings.json``.
``IT2AGENT_CLAUDE_SETTINGS`` overrides the path for tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

PROG = "it2agent-autobrief-hook"

# The shared settings-file mechanism (#113) + the guide generator (#56/#113).
_HOOKKIT_DIR = Path(__file__).resolve().parent.parent / "hookkit"
_GUIDE_DIR = Path(__file__).resolve().parent.parent / "guide"
for _d in (_HOOKKIT_DIR, _GUIDE_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import claude_settings as cs  # type: ignore  # noqa: E402

# The single Claude Code event we register, mapped to our CLI verb.
HOOK_EVENTS = {"SessionStart": "session-start"}

# Accepted spellings of the event on the CLI (verb or documented event name).
_EVENT_ALIASES = {"session-start", "sessionstart", "sessionstarted"}


# --------------------------------------------------------------------------- #
# Diagnostics (stderr only — stdout is reserved for the additionalContext JSON)
# --------------------------------------------------------------------------- #


def _log(message: str) -> None:
    try:
        print(f"{PROG}: {message}", file=sys.stderr)
    except Exception:  # noqa: BLE001 - logging must never raise
        pass


def _read_stdin() -> str:
    try:
        return sys.stdin.read()
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# Pure: build the SessionStart additionalContext payload from a brief string.
# --------------------------------------------------------------------------- #


def build_output(brief: str) -> dict:
    """Wrap ``brief`` in the documented SessionStart additionalContext envelope."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": brief,
        }
    }


def _is_session_start(event: str) -> bool:
    return (event or "").strip().lower() in _EVENT_ALIASES


# --------------------------------------------------------------------------- #
# Event dispatch — ALWAYS exit 0; stdout ONLY the JSON, ONLY when gate is open.
# --------------------------------------------------------------------------- #


def run_event(event: str, raw_stdin: str, no_gate: bool = False) -> int:
    """Handle one SessionStart hook event. Returns an exit code that is ALWAYS 0.

    Order: gate first (flag OFF ⇒ silent no-op, nothing rendered, nothing
    printed); then confirm this really is a SessionStart event; then render the
    brief and print the additionalContext JSON. Every branch returns 0. stdout is
    written ONLY on the success path (a single JSON object).
    """
    import gate  # local import so a broken sibling never stops import of this module

    try:
        if not gate.gate_open(no_gate=no_gate):
            return 0
    except Exception as exc:  # noqa: BLE001 - gate errors ⇒ treat as OFF (silent)
        _log(f"gate check failed, treating as OFF: {exc}")
        return 0

    if not _is_session_start(event):
        _log(f"not a SessionStart event: {event!r} (ignoring)")
        return 0

    # stdin is not required (we do not use its fields), but parse defensively so a
    # future need is easy and malformed input never raises. We simply ignore it.
    if raw_stdin.strip():
        try:
            json.loads(raw_stdin)
        except (ValueError, TypeError) as exc:
            _log(f"non-JSON stdin (continuing anyway): {exc}")

    try:
        import it2agent_guide  # type: ignore

        brief = it2agent_guide.render_brief()
    except Exception as exc:  # noqa: BLE001 - render failure ⇒ silent, exit 0
        _log(f"brief render failed (ignoring): {exc}")
        return 0

    if not isinstance(brief, str) or not brief.strip():
        _log("empty brief (ignoring)")
        return 0

    try:
        sys.stdout.write(json.dumps(build_output(brief)))
    except Exception as exc:  # noqa: BLE001 - a write failure must not raise
        _log(f"stdout write failed (ignoring): {exc}")
    return 0


# --------------------------------------------------------------------------- #
# install / uninstall / status — operator opt-in via the shared mechanism.
# --------------------------------------------------------------------------- #


def _err(message: str) -> None:
    try:
        print(f"{PROG}: {message}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _wrapper_command_path() -> str:
    """Absolute path to the it2agent-autobrief-hook shell wrapper (sibling)."""
    return str(Path(__file__).resolve().parent / PROG)


def _events_for_install() -> dict[str, str]:
    base = _wrapper_command_path()
    return {event: f"{base} {verb}" for event, verb in HOOK_EVENTS.items()}


def cmd_install(scope: str = "project", start_dir: Optional[Path] = None) -> int:
    import os

    try:
        path = cs.settings_path(scope, start_dir)
    except cs.NotAGitRepoError as exc:
        _err(str(exc))
        return 2
    settings = cs.load_settings(path)
    cs.install_event_hooks(settings, _events_for_install(), PROG)
    cs.write_settings(path, settings)
    print(f"Installed it2agent autobrief hook into {path}")
    print("Registered: SessionStart")
    if scope == "project" and not os.environ.get("IT2AGENT_CLAUDE_SETTINGS"):
        root = path.parent.parent  # <root>/.claude/settings.local.json -> <root>
        if cs.ensure_gitignored(root):
            print(f"Added {cs.GITIGNORE_ENTRY} to {root / '.gitignore'}")
    print(
        "NOTE: the brief is injected on the next Claude Code session in this "
        "project ONLY when agent.autobrief is ON. Enable it with "
        "`it2agent-flag enable agent.autobrief` (default OFF)."
    )
    return 0


def cmd_uninstall(scope: str = "project", start_dir: Optional[Path] = None) -> int:
    try:
        path = cs.settings_path(scope, start_dir)
    except cs.NotAGitRepoError as exc:
        _err(str(exc))
        return 2
    settings = cs.load_settings(path)
    cs.uninstall_event_hooks(settings, PROG)
    cs.write_settings(path, settings)
    print(f"Removed it2agent autobrief hook from {path}")
    return 0


def cmd_status(scope: str = "project", start_dir: Optional[Path] = None) -> int:
    """Report install state read-only: print the resolved path, signal via exit.

    Exit 0 = our hook is present; 1 = absent; 2 = ``--scope project`` from outside
    a git repo (path unresolvable). Never writes to a file.
    """
    try:
        path = cs.settings_path(scope, start_dir)
    except cs.NotAGitRepoError as exc:
        _err(str(exc))
        return 2
    print(str(path))
    settings = cs.load_settings(path)
    return 0 if cs.is_installed(settings, PROG) else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

USAGE = f"""usage: {PROG} <event|command> [--scope project|user] [--no-gate]

Observer event (reads hook JSON on stdin; ALWAYS exit 0):
  session-start | SessionStart   emit the it2agent capabilities brief as the
                                 SessionStart additionalContext (only when the
                                 gate is open; otherwise silent)

Operator commands (opt-in; edit a Claude Code settings file):
  install                        add the SessionStart hook (deep-merge; never overwrites)
  uninstall                      remove ONLY the entry this tool added
  status                         print the resolved path; exit 0=installed, 1=absent
  -h, --help                     show this help

  --scope project (default)      <git-root>/.claude/settings.local.json (machine-local,
                                 gitignored; errors if cwd is not in a git repo)
  --scope user                   ~/.claude/settings.json (global)

Gating: the flag agent.autobrief is a positive gate (default OFF) — the brief is
injected ONLY when it is ON. Bypass with --no-gate or IT2AGENT_FORCE=1 (testing).
Settings file is overridable via IT2AGENT_CLAUDE_SETTINGS (used by tests)."""


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    no_gate = False
    scope = "project"
    positional: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--no-gate":
            no_gate = True
        elif arg in ("-h", "--help"):
            print(USAGE)
            return 0
        elif arg == "--scope":
            index += 1
            if index >= len(argv):
                _err("--scope requires a value (project|user)")
                return 2
            scope = argv[index]
        elif arg.startswith("--scope="):
            scope = arg.split("=", 1)[1]
        else:
            positional.append(arg)
        index += 1

    if not positional:
        # No event given. Should never happen in a real hook invocation; be safe
        # and exit 0 with usage on stderr (never block, never write stdout).
        _log("missing <event|command>")
        print(USAGE, file=sys.stderr)
        return 0

    command = positional[0]
    if command in ("install", "uninstall", "status"):
        if scope not in ("project", "user"):
            _err(f"unknown scope: {scope!r} (expected project|user)")
            return 2
        if command == "install":
            return cmd_install(scope)
        if command == "uninstall":
            return cmd_uninstall(scope)
        return cmd_status(scope)

    # Otherwise it is an event: read stdin and (maybe) emit. ALWAYS exit 0.
    return run_event(command, _read_stdin(), no_gate=no_gate)


if __name__ == "__main__":
    sys.exit(main())
