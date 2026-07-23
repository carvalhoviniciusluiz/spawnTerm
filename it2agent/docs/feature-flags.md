# it2agent feature flags

Every it2agent capability is an individually toggleable, **per-user** feature flag.
**Every flag defaults OFF.** Nothing is forced on; users opt in. Each capability issue
(#7–#18) gates on its flag before doing any work.

Tracking issue: **#11** (Foundation). See also `it2agent/PLAN.md` and `AGENTS.md`.

## Config location

A single config source:

```
$XDG_CONFIG_HOME/it2agent/config.toml
```

If `$XDG_CONFIG_HOME` is unset/empty, it falls back to:

```
~/.config/it2agent/config.toml
```

For tests and tooling, `$IT2AGENT_CONFIG` (a full path to the config file) overrides
both. Precedence: `IT2AGENT_CONFIG` > `XDG_CONFIG_HOME` > `~/.config`.

Resolve it at runtime with `it2agent-flag path`.

## File format (strict)

Booleans only, under a single `[features]` table, with **quoted, fully-namespaced keys**:

```toml
# it2agent config
# Managed by it2agent-flag.
# Docs: it2agent/docs/feature-flags.md
[features]
"agent.status_board" = false
"agent.worktree_isolation" = false
"agent.messaging" = true
"agent.inbox" = false
"agent.cost_dashboard" = false
"agent.janitor" = false
"agent.mcp" = false
"agent.daemon" = false
"agent.broker" = false
"agent.review" = false
"agent.tmux" = false
"agent.claude_statusbar" = false
"agent.menubar" = false
"agent.codex_status" = false
"agent.native_status" = false
"agent.team_bridge" = false
```

The format is deliberately constrained so a pure-shell parser and Python's `tomllib`
agree exactly:

- One `[features]` table.
- Keys are quoted string keys of the form `"agent.<capability>"`.
- Values are the bare literals `true` or `false` (no other TOML types).

`it2agent-flag enable`/`disable` always rewrite the file with the **full seeded schema**
in canonical order, so the file is deterministic regardless of which implementation
(shell or Python) wrote it — the two produce byte-for-byte identical output.

## Flag schema

Namespaced keys are `agent.<capability>`. All default **`false`**.

| Key | Capability |
|-----|------------|
| `agent.status_board` | Tier 0 escape-code status board (agents emit state; iTerm2 paints it). |
| `agent.worktree_isolation` | Per-agent git-worktree + `$PORT` isolation. |
| `agent.messaging` | Cross-tab agent-to-agent messaging via the broker. |
| `agent.inbox` | Durable per-agent inbox surface. |
| `agent.cost_dashboard` | Token/cost dashboard. |
| `agent.janitor` | Background cleanup of stale worktrees/sessions. |
| `agent.mcp` | MCP surface exposing it2agent to agents. |
| `agent.daemon` | Tier 1 iTerm2 Python API orchestration daemon (registry + ingest/idle). |
| `agent.broker` | Tier 2 external broker (durable sqlite mailbox/registry/state/ack over a unix socket). |
| `agent.review` | Per-agent diff/review surface (show worktree diff vs base; approve→merge / request-changes). |
| `agent.tmux` | Tier 3 tmux `-CC` persistence: spawn agents inside a native tmux `-CC` session so windows/agents survive quit/crash and can be reattached. |
| `agent.claude_statusbar` | Claude Code session status aggregator status-bar component (Waiting/Working/Idle across all windows), adapted from gnachman/iTerm2#648. |
| `agent.menubar` | Menu bar status item with a live count badge of busy AI agents, adapted from gnachman/iTerm2#670. |
| `agent.codex_status` | Show Codex CLI working/idle activity in the tab status by decoding the braille-spinner title prefix, adapted from gnachman/iTerm2#673. |
| `agent.native_status` | Emit native OSC 21337 tab-status so agents show in iTerm2's native tab status + Cockpit (`it2agent-emit ccstatus`). |
| `agent.team_bridge` | Mirror Claude Code agent-teams task/coordination state into the durable broker so it survives lead-session death (`it2agent-team-hook`). |

## Default-OFF rule

A flag reads as **OFF** when any of the following is true:

- the config file does not exist,
- the `[features]` table is missing,
- the key is absent, or
- the value is not exactly `true`.

**Reads never write a file.** A query on a machine with no config succeeds as "OFF"
without creating anything. Only `enable`/`disable` create or modify the file.

## `it2agent-flag` contract

Canonical CLI: `it2agent/flags/it2agent-flag` (pure shell). Python twin:
`it2agent/flags/it2agent_flag.py`, runnable as `python3 it2agent_flag.py …` or
`python3 -m it2agent_flag …`, with identical behavior and output. The Python module
also exposes `is_enabled(key: str) -> bool` for the daemon.

Keys may be given **with or without** the `agent.` prefix; they are normalized.

### Query (default command)

```
it2agent-flag <key>
```

- Flag ON  → prints `1` to stdout, exits **0**.
- Flag OFF / absent / missing-config → prints `0` to stdout, exits **1**.

This makes both usages work:

```sh
if it2agent-flag agent.messaging >/dev/null; then …; fi   # exit-code form
state=$(it2agent-flag agent.messaging)                     # captured stdout ("1"/"0")
```

An unknown key on a query is treated as OFF (`0`, exit 1) with a warning on stderr;
stdout stays clean.

### Subcommands

| Command | Behavior | Exit |
|---------|----------|------|
| `list` | Print every known flag and its effective on/off state (`<key> on\|off`). | 0 |
| `enable <key>` | Turn the flag ON; create the dir + file with the full seeded schema if absent, preserving other flags. | 0 |
| `disable <key>` | Turn the flag OFF; same file-creation semantics. | 0 |
| `path` | Print the resolved config file path. | 0 |
| `-h`, `--help` | Print usage. | 0 |

### Exit codes (summary)

| Exit | Meaning |
|------|---------|
| `0` | Query ON; or a subcommand (`list`/`enable`/`disable`/`path`/`--help`) succeeded. |
| `1` | Query OFF / absent / missing-config. |
| `2` | Usage error: no args, unexpected extra args, or `enable`/`disable` on an unknown key. |

Errors and warnings go to **stderr**; only the contract values (`1`/`0`, the list, the
path) go to **stdout**.

## Testing

```sh
bash it2agent/flags/tests/test_flags.sh
```

Covers default-OFF (no config), enable→ON, disable→OFF, `list`, prefix normalization,
`path`, unknown-key handling, no-args usage errors, `is_enabled()` import, and full
shell/Python parity (identical stdout, exit codes, and on-disk config files).
