#!/usr/bin/env python3
"""it2agent team bridge — durable OBSERVER of Claude Code agent-teams (#92).

Claude Code's experimental *agent teams* let a lead spawn teammates that share a
task list and message each other. Their coordination state lives under
``~/.claude/teams/{team}/`` and is **removed at session end** — documented as
"coordination state is lost" on lead death. This tool mirrors that state into
the it2agent durable broker (sqlite-WAL) so it survives, and exposes it back
through the existing MCP surface. We do **not** replace the team's mailbox or
task list; we *shadow* them durably. This is COOPERATION PATH 1 in
``it2agent/docs/cooperation-strategy.md``.

Wiring: registered as three Claude Code hooks (``TaskCreated`` / ``TaskCompleted``
/ ``TeammateIdle``) that each deliver JSON on **stdin**. The hook is a thin CLI:

    it2agent-team-hook <event>                    # reads stdin JSON, mirrors to broker
    it2agent-team-hook install --scope project    # append 3 hooks to the project's
                                                  #   gitignored settings.local.json
    it2agent-team-hook uninstall --scope project  # remove ONLY the entries we added
    it2agent-team-hook status --scope project     # report install state via exit code

CRITICAL observer contract (Claude Code hooks): **exit code 2 BLOCKS the team**
(rolls back task creation / prevents completion / keeps a teammate working). Our
mirror is a passive observer, so the event path **ALWAYS ``exit 0`` and NEVER
writes to stdout**, under every condition: flag OFF, broker down, malformed or
empty stdin, unknown event, any exception. Diagnostics go to stderr only.

Gating (per-project model, #96): installing the hook into a project IS the
opt-in, so once installed the event path RUNS by default. The global
``agent.team_bridge`` flag is only an OPTIONAL kill-switch — an EXPLICIT
``false`` in config.toml forces the bridge OFF; unset/absent/true all RUN.
Bypass the gate entirely for local testing with ``--no-gate`` or
``IT2AGENT_FORCE=1``.

Team key derivation: ``team_name`` is DEPRECATED in the hook payload, so the team
key is derived deterministically from ``session_id``:
``team:session-<first 8 chars of session_id>``. This is a stable join key the
broker mirror (and any external dashboard) can use without Claude Code running.

Task-object fields are NOT documented, so extraction is DEFENSIVE: id/title/
description are each tried under several key spellings with safe fallbacks.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

PROG = "it2agent-team-hook"

# The broker client + paths live in the sibling package (#34).
_BROKER_DIR = Path(__file__).resolve().parent.parent / "broker"

# Canonical event aliases. The hook is registered with the short verb (created/
# completed/idle); we also accept the documented hook_event_name for robustness.
_EVENT_ALIASES = {
    "taskcreated": "created",
    "created": "created",
    "taskcompleted": "completed",
    "completed": "completed",
    "teammateidle": "idle",
    "idle": "idle",
}

# The three Claude Code hook events we register, mapped to our short verb. Used
# by install/uninstall to build the settings.json entries.
HOOK_EVENTS = {
    "TaskCreated": "created",
    "TaskCompleted": "completed",
    "TeammateIdle": "idle",
}


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O) — the whole event→op mapping is unit-testable here.
# --------------------------------------------------------------------------- #


def normalize_event(event: str) -> Optional[str]:
    """Map a CLI arg / hook_event_name to our short verb, or None if unknown."""
    return _EVENT_ALIASES.get((event or "").strip().lower())


def team_key(session_id: Any) -> str:
    """Derive the durable team key from ``session_id``: ``team:session-<sid8>``.

    ``team_name`` is DEPRECATED in the payload, so we never use it. Non-string
    or empty session ids degrade to ``team:session-`` (still a valid, stable
    key) rather than raising — the observer must never fail.
    """
    sid = session_id if isinstance(session_id, str) else ""
    return f"team:session-{sid[:8]}"


def _first_str(*values: Any) -> Optional[str]:
    """Return the first value that is a non-empty string, else None."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def extract_task(payload: dict) -> dict[str, Optional[str]]:
    """Defensively pull id/title/description out of a task payload.

    The task object's field names are NOT documented, so try several spellings:
    nested ``task.{id,title,description}`` and flat ``task_id`` / ``title`` /
    ``description``. Missing fields fall back to ``id="unknown"`` and
    ``title``/``description`` = None. Never raises.
    """
    task = payload.get("task")
    if not isinstance(task, dict):
        task = {}
    task_id = _first_str(task.get("id"), payload.get("task_id"), payload.get("id"))
    title = _first_str(task.get("title"), payload.get("title"))
    description = _first_str(task.get("description"), payload.get("description"))
    return {
        "id": task_id or "unknown",
        "title": title,
        "description": description,
    }


def build_register_op(payload: dict) -> dict[str, Any]:
    """``TeammateIdle`` → broker ``register`` (registry upsert).

    The teammate's ``agent_id`` is the durable session key; ``agent_type``
    becomes the role. Capabilities tag the row as a Claude Code teammate and
    carry the derived team key so a resumed view of "who was on the team"
    survives the team dir being deleted.
    """
    sid = payload.get("session_id")
    key = team_key(sid)
    agent_id = _first_str(payload.get("agent_id")) or key
    role = _first_str(payload.get("agent_type"))
    op: dict[str, Any] = {
        "op": "register",
        "session_id": agent_id,
        "alive": True,
        "capabilities": ["claude-code-teammate", key],
    }
    if role is not None:
        op["role"] = role
    return op


def build_handoff_op(payload: dict, verification_status: str) -> dict[str, Any]:
    """``TaskCreated`` / ``TaskCompleted`` → broker ``handoff_put`` (append-only).

    Keyed on the *team* (``agent_id``) and the *task id* (``goal``), so the
    append-only history IS the task's lifecycle log, queryable after lead death.
    ``context_ptr`` points at the transcript so a fresh agent can re-read the
    origin. The title (when present) rides in ``owned_files[0]`` pragmatically
    in v1 (a v2 broker op can add first-class title/description columns).
    """
    key = team_key(payload.get("session_id"))
    task = extract_task(payload)
    op: dict[str, Any] = {
        "op": "handoff_put",
        "agent_id": key,
        "goal": f"task:{task['id']}",
        "verification_status": verification_status,
    }
    context_ptr = _first_str(payload.get("transcript_path"))
    if context_ptr is not None:
        op["context_ptr"] = context_ptr
    if task["title"] is not None:
        op["owned_files"] = [task["title"]]
    return op


def build_completed_send_op(payload: dict) -> dict[str, Any]:
    """Optional ``send`` on completion so a lead that died/resumed still sees it.

    Durable + replayed-until-acked via the broker mailbox. Carries an idempotency
    ``key`` of ``task:<id>:completed`` (#95) so a retried TaskCompleted — the hook
    re-firing after a partial failure — dedups on ``(lead, key)`` at the broker
    instead of enqueuing a duplicate "completed" notification. Harmless against an
    older broker that predates the key (it is simply ignored server-side).
    """
    tkey = team_key(payload.get("session_id"))
    task = extract_task(payload)
    return {
        "op": "send",
        "to": "lead",
        "from": tkey,
        "body": f"task:{task['id']} completed",
        "key": f"task:{task['id']}:completed",
    }


def build_ops(event: str, payload: dict) -> list[dict[str, Any]]:
    """Map a normalized event + payload to the ordered list of broker ops.

    Returns ``[]`` for an unknown event (the caller then no-ops, exit 0). Pure:
    no socket, no flag read — this is the function the unit tests pin.
    """
    verb = normalize_event(event)
    if verb == "idle":
        return [build_register_op(payload)]
    if verb == "created":
        return [build_handoff_op(payload, "pending")]
    if verb == "completed":
        return [
            build_handoff_op(payload, "completed"),
            build_completed_send_op(payload),
        ]
    return []


# --------------------------------------------------------------------------- #
# Broker I/O (only reached on the success path, after the gate).
# --------------------------------------------------------------------------- #


def _send_to_broker(ops: list[dict[str, Any]]) -> None:
    """Send each op to the broker via the sibling stdlib BrokerClient.

    Best-effort: a per-op failure is logged to stderr and swallowed so one bad
    op never blocks the rest and the process still exits 0.
    """
    if str(_BROKER_DIR) not in sys.path:
        sys.path.insert(0, str(_BROKER_DIR))
    from client import BrokerClient  # type: ignore

    client = BrokerClient()
    for op in ops:
        try:
            client.request(op)
        except Exception as exc:  # noqa: BLE001 - observer must never fail
            _log(f"broker request failed for op={op.get('op')!r}: {exc}")


def _log(message: str) -> None:
    """Diagnostics go to stderr only — never stdout (the harness parses stdout)."""
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
# Event dispatch — ALWAYS exit 0, NEVER write stdout.
# --------------------------------------------------------------------------- #


def run_event(event: str, raw_stdin: str, no_gate: bool = False) -> int:
    """Handle one hook event. Returns an exit code that is ALWAYS 0.

    Order: gate first (flag OFF ⇒ silent no-op, no parse, no broker); then parse
    stdin defensively; then map to ops; then best-effort broker send. Every
    branch returns 0 and writes nothing to stdout.
    """
    import gate  # local import so a broken sibling never stops import of this module

    try:
        if not gate.gate_open(no_gate=no_gate):
            return 0
    except Exception as exc:  # noqa: BLE001 - gate errors ⇒ treat as OFF
        _log(f"gate check failed, treating as OFF: {exc}")
        return 0

    verb = normalize_event(event)
    if verb is None:
        _log(f"unknown event: {event!r} (ignoring)")
        return 0

    if not raw_stdin.strip():
        _log("empty stdin (ignoring)")
        return 0
    try:
        payload = json.loads(raw_stdin)
    except (ValueError, TypeError) as exc:
        _log(f"non-JSON stdin (ignoring): {exc}")
        return 0
    if not isinstance(payload, dict):
        _log("stdin JSON was not an object (ignoring)")
        return 0

    try:
        ops = build_ops(event, payload)
    except Exception as exc:  # noqa: BLE001 - mapping must never fail the team
        _log(f"op mapping failed (ignoring): {exc}")
        return 0
    if not ops:
        return 0

    try:
        _send_to_broker(ops)
    except Exception as exc:  # noqa: BLE001 - broker unreachable/any error ⇒ exit 0
        _log(f"broker unreachable (ignoring): {exc}")
    return 0


# --------------------------------------------------------------------------- #
# install / uninstall / status — operator opt-in, edits a Claude Code settings
# file. Two scopes:
#   user     -> ~/.claude/settings.json (global, distributed with the user)
#   project  -> <git-root-of-cwd>/.claude/settings.local.json — machine-local,
#              gitignored, per-project but NOT committed/distributed. This is the
#              scope the GUI uses: a project-committed settings.json would run
#              hooks UNGATED for anyone who checks it out (CVE-2025-59536), so we
#              deliberately target the gitignored .local file instead.
# --------------------------------------------------------------------------- #

_GITIGNORE_ENTRY = ".claude/settings.local.json"


class NotAGitRepoError(Exception):
    """Raised when ``--scope project`` is requested from outside a git repo."""


def _err(message: str) -> None:
    """Operator-command error → stderr (never stdout). Never raises."""
    try:
        print(f"{PROG}: {message}", file=sys.stderr)
    except Exception:  # noqa: BLE001 - logging must never raise
        pass


def _find_git_root(start: Path) -> Optional[Path]:
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

    ``IT2AGENT_CLAUDE_SETTINGS`` (a full path) always wins so tests and the
    operator can redirect writes away from any real file. Otherwise:

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
        root = _find_git_root(start_dir or Path.cwd())
        if root is None:
            raise NotAGitRepoError(
                "not inside a git repository; --scope project needs a project root"
            )
        return root / ".claude" / "settings.local.json"
    return Path.home() / ".claude" / "settings.json"


def _wrapper_command_path() -> str:
    """Absolute path to the it2agent-team-hook shell wrapper (sibling of this).

    Claude Code invokes the ``command`` string via the shell, appending the
    event verb. We register the wrapper (not the .py) to match the twin-tool
    convention and keep the entry stable.
    """
    return str(Path(__file__).resolve().parent / "it2agent-team-hook")


def _is_ours(command: Any) -> bool:
    """True iff a hook command string is one we installed (match by our basename)."""
    return isinstance(command, str) and PROG in command


def _load_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _install_into(settings: dict, command_base: str) -> dict:
    """Deep-merge our three hook entries into ``settings`` (pure; returns it).

    NEVER overwrites unrelated keys. Idempotent: an existing entry of ours for a
    given event is left as-is rather than duplicated.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    for event, verb in HOOK_EVENTS.items():
        command = f"{command_base} {verb}"
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        already = any(
            isinstance(group, dict)
            and any(
                isinstance(h, dict) and _is_ours(h.get("command"))
                for h in (group.get("hooks") or [])
                if isinstance(group.get("hooks"), list)
            )
            for group in groups
        )
        if already:
            continue
        groups.append({"hooks": [{"type": "command", "command": command}]})
    return settings


def _uninstall_from(settings: dict) -> dict:
    """Remove ONLY the hook entries we added (match by our command basename).

    Prunes empty hook groups, then empty event lists, then an empty ``hooks``
    table — but never touches any other key. Idempotent.
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
                h for h in inner if not (isinstance(h, dict) and _is_ours(h.get("command")))
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


def _is_installed(settings: dict) -> bool:
    """True iff any of our hook entries is present in ``settings`` (match basename)."""
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
                if isinstance(entry, dict) and _is_ours(entry.get("command")):
                    return True
    return False


def _write_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _gitignore_covers(lines: list[str], entry: str) -> bool:
    """True iff a ``.gitignore`` line already ignores ``entry``.

    Heuristic (no git invocation, so this stays hermetic and unit-testable):
    an uncommented line equals the exact path or a broader pattern that clearly
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


def _ensure_gitignored(root: Path) -> bool:
    """Ensure ``<root>/.gitignore`` ignores ``.claude/settings.local.json``.

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
    if _gitignore_covers(text.splitlines(), _GITIGNORE_ENTRY):
        return False
    prefix = text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    addition = ""
    if prefix.strip():
        addition += "\n"  # visual separation from existing content
    addition += "# it2agent: machine-local Claude Code settings (do not commit)\n"
    addition += _GITIGNORE_ENTRY + "\n"
    try:
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        gitignore.write_text(prefix + addition, encoding="utf-8")
    except OSError:
        return False
    return True


def cmd_install(scope: str = "user", start_dir: Optional[Path] = None) -> int:
    try:
        path = settings_path(scope, start_dir)
    except NotAGitRepoError as exc:
        _err(str(exc))
        return 2
    command_base = _wrapper_command_path()
    settings = _load_settings(path)
    _install_into(settings, command_base)
    _write_settings(path, settings)
    print(f"Installed it2agent team-bridge hooks into {path}")
    print("Registered: TaskCreated, TaskCompleted, TeammateIdle")
    # Project scope writes a machine-local file that must never be committed.
    # (Skip when the IT2AGENT_CLAUDE_SETTINGS escape hatch redirected the path —
    # there is no project root to reason about then.)
    if scope == "project" and not os.environ.get("IT2AGENT_CLAUDE_SETTINGS"):
        root = path.parent.parent  # <root>/.claude/settings.local.json -> <root>
        if _ensure_gitignored(root):
            print(f"Added {_GITIGNORE_ENTRY} to {root / '.gitignore'}")
    print(
        "NOTE: agent teams are experimental — you must enable them yourself by "
        "exporting CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1. This tool does NOT set it."
    )
    print(
        "NOTE: the hook now runs on the next Claude Code session in this project. "
        "The agent.team_bridge flag is an optional kill-switch: disable it with "
        "`it2agent-flag disable agent.team_bridge` to force the bridge OFF."
    )
    return 0


def cmd_uninstall(scope: str = "user", start_dir: Optional[Path] = None) -> int:
    try:
        path = settings_path(scope, start_dir)
    except NotAGitRepoError as exc:
        _err(str(exc))
        return 2
    settings = _load_settings(path)
    _uninstall_from(settings)
    _write_settings(path, settings)
    print(f"Removed it2agent team-bridge hooks from {path}")
    return 0


def cmd_status(scope: str = "user", start_dir: Optional[Path] = None) -> int:
    """Report install state read-only: print the resolved path, signal via exit.

    Exit 0 = our hooks are present; 1 = absent; 2 = ``--scope project`` from
    outside a git repo (path unresolvable). Never writes to a file.
    """
    try:
        path = settings_path(scope, start_dir)
    except NotAGitRepoError as exc:
        _err(str(exc))
        return 2
    print(str(path))
    settings = _load_settings(path)
    return 0 if _is_installed(settings) else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

USAGE = f"""usage: {PROG} <event|command> [--scope user|project] [--no-gate]

Observer events (read hook JSON on stdin; ALWAYS exit 0, never write stdout):
  created | TaskCreated       mirror a new task as a 'pending' handoff
  completed | TaskCompleted   mirror task completion + notify the lead
  idle | TeammateIdle         register the idle teammate in the broker registry

Operator commands (opt-in; edit a Claude Code settings file):
  install                     append the three hooks (deep-merge; never overwrites)
  uninstall                   remove ONLY the entries this tool added
  status                      print the resolved path; exit 0=installed, 1=absent
  -h, --help                  show this help

  --scope user (default)      ~/.claude/settings.json (global)
  --scope project             <git-root>/.claude/settings.local.json (machine-local,
                              gitignored; errors if cwd is not in a git repo)

Gating: once installed, the event path RUNS by default; an EXPLICIT
`agent.team_bridge = false` in config.toml is an optional kill-switch. Bypass
the gate with --no-gate or IT2AGENT_FORCE=1 (local testing only).
Settings file is overridable via IT2AGENT_CLAUDE_SETTINGS (used by tests)."""


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    no_gate = False
    scope = "user"
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
                _err("--scope requires a value (user|project)")
                return 2
            scope = argv[index]
        elif arg.startswith("--scope="):
            scope = arg.split("=", 1)[1]
        else:
            positional.append(arg)
        index += 1

    if not positional:
        # No event given. This should never happen in a real hook invocation;
        # be safe and exit 0 with usage on stderr (never block, never stdout).
        _log("missing <event|command>")
        print(USAGE, file=sys.stderr)
        return 0

    command = positional[0]
    # Operator commands honor --scope and may exit non-zero (they are NOT the
    # observer event path, so the always-exit-0 contract does not apply here).
    if command in ("install", "uninstall", "status"):
        if scope not in ("user", "project"):
            _err(f"unknown scope: {scope!r} (expected user|project)")
            return 2
        if command == "install":
            return cmd_install(scope)
        if command == "uninstall":
            return cmd_uninstall(scope)
        return cmd_status(scope)

    # Otherwise it is an event: read stdin and mirror. ALWAYS exit 0.
    return run_event(command, _read_stdin(), no_gate=no_gate)


if __name__ == "__main__":
    sys.exit(main())
