# it2agent team bridge (`it2agent-team-hook`, `agent.team_bridge`) — #92

A durable **observer** that mirrors Claude Code *agent-teams* task and
coordination state into the it2agent broker (sqlite-WAL), so it **survives
lead-session death** — the documented gap where `~/.claude/teams/{team}/` is
removed at session end and "coordination state is lost". We do **not** replace
the team's mailbox or task list; we shadow them durably and expose the mirror
back through the existing MCP surface. This is COOPERATION PATH 1 in
`it2agent/docs/cooperation-strategy.md` (the moat).

Additive. Gating is **per-project** (#96): installing the hook into a project
is the opt-in, so once installed the bridge **runs by default**. The global
`agent.team_bridge` flag is only an optional **kill-switch** — an EXPLICIT
`false` forces it OFF; unset/absent/true all run.

## Files

- `it2agent-team-hook` — thin shell launcher (twin-tool convention, like
  `it2agent-inbox`). No shell parity twin: the bridge speaks the broker socket
  through the shared Python `BrokerClient`.
- `it2agent_team_hook.py` — the whole CLI (stdlib only). Pure event→op mapping +
  best-effort broker I/O + `install`/`uninstall`/`status` (`--scope user|project`).
- `gate.py` — `agent.team_bridge` kill-switch gate (fail-safe: runs unless the
  flag is an EXPLICIT `false`), reusing the `it2agent_flag` helper; honors
  `--no-gate` / `IT2AGENT_FORCE=1`.
- `tests/test_team_hook.py` — headless unit tests (fake broker + temp settings).

## Observer contract (critical)

Claude Code hooks that **exit 2 BLOCK the team** (roll back task creation,
prevent completion, keep a teammate working). This tool is a passive observer,
so the event path **ALWAYS exits 0 and NEVER writes to stdout**, under every
condition: flag OFF, broker down, empty/malformed stdin, unknown event, any
exception. Diagnostics go to stderr only.

## Team key

`team_name` is DEPRECATED in the hook payload, so the durable team key is
derived deterministically from `session_id`:

```
team:session-<first 8 chars of session_id>
```

It is a stable join key the broker mirror (and any external dashboard) can use
without Claude Code running.

## Event → broker op

| Hook event (or short verb) | Broker op |
| --- | --- |
| `TeammateIdle` / `idle` | `register` `{session_id:<agent_id or team key>, role:<agent_type>, alive:true, capabilities:["claude-code-teammate","team:session-<sid8>"]}` |
| `TaskCreated` / `created` | `handoff_put` `{agent_id:"team:session-<sid8>", goal:"task:<id>", context_ptr:<transcript_path>, verification_status:"pending", owned_files:[<title>]}` |
| `TaskCompleted` / `completed` | `handoff_put` (same key, `verification_status:"completed"`) + `send` `{to:"lead", from:"team:session-<sid8>", body:"task:<id> completed"}` |

The task object's field names are **not** documented, so id/title/description
are extracted defensively (`task.id` / `task_id` / `id`; `task.title` / `title`;
`task.description` / `description`) with safe fallbacks (`id` → `"unknown"`,
title/description omitted when absent). Because handoffs are append-only,
`handoff_history(agent_id="team:session-<sid8>")` is the task's full lifecycle
log, queryable after lead death.

## Install (operator opt-in — never automatic)

`install` appends the three hooks to a Claude Code settings file, **deep-merging**
into any existing `hooks` and never overwriting other keys; `uninstall` removes
**only** the entries we added (matched by our command path) and is idempotent;
`status` reports install state via exit code (0 installed, 1 absent). Two scopes:

- `--scope user` (default) → `~/.claude/settings.json` (global).
- `--scope project` → `<git-root-of-cwd>/.claude/settings.local.json` — a
  machine-local, **gitignored** file (install ensures the `.gitignore` entry).
  This is the scope the iTerm2 GUI checkbox uses. A project-committed
  `settings.json` would run hooks **ungated** for anyone who checks it out
  (CVE-2025-59536), so we deliberately target the gitignored `.local` file.
  Errors non-zero when the cwd is not inside a git repo (never falls back to
  global).

It does **not** set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` — you enable the
experimental feature yourself.

```
# Per-project (what the GUI does), run from inside the project:
it2agent-team-hook install --scope project
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# Optional global kill-switch to force the bridge OFF everywhere:
it2agent-flag disable agent.team_bridge
```

The settings path is overridable via `IT2AGENT_CLAUDE_SETTINGS` (tests point it
at a temp file so the real settings file is never touched).

## Tests

```
python3 -m unittest discover -s it2agent/team/tests
```
