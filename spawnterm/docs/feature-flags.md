# spawnTerm feature flags

Every spawnTerm capability is an individually toggleable, **per-user** feature flag.
**Every flag defaults OFF.** Nothing is forced on; users opt in. Each capability issue
(#7–#18) gates on its flag before doing any work.

Tracking issue: **#11** (Foundation). See also `spawnterm/PLAN.md` and `AGENTS.md`.

## Config location

A single config source:

```
$XDG_CONFIG_HOME/spawnterm/config.toml
```

If `$XDG_CONFIG_HOME` is unset/empty, it falls back to:

```
~/.config/spawnterm/config.toml
```

For tests and tooling, `$SPAWNTERM_CONFIG` (a full path to the config file) overrides
both. Precedence: `SPAWNTERM_CONFIG` > `XDG_CONFIG_HOME` > `~/.config`.

Resolve it at runtime with `spawnterm-flag path`.

## File format (strict)

Booleans only, under a `[features]` table, with **quoted, fully-namespaced keys**.
The file may also carry a `[settings]` table (owned by `spawnterm-lang`, e.g.
`language`); the writer preserves it via read-modify-write, mirroring how
`spawnterm-lang` preserves `[features]`. Canonical table order is `[features]`
first, then `[settings]`, and a table is emitted only when it has content:

```toml
# spawnterm config
# Managed by spawnterm-flag (features) and spawnterm-lang (settings).
# Docs: spawnterm/docs/feature-flags.md
[features]
"spawnterm.status_board" = false
"spawnterm.worktree_isolation" = false
"spawnterm.messaging" = true
"spawnterm.agent_inbox" = false
"spawnterm.cost_dashboard" = false
"spawnterm.janitor" = false
"spawnterm.mcp" = false
"spawnterm.daemon" = false
"spawnterm.broker" = false
"spawnterm.review" = false
"spawnterm.tmux" = false
"spawnterm.claude_statusbar" = false
"spawnterm.agent_menubar" = false
"spawnterm.codex_status" = false
```

The format is deliberately constrained so a pure-shell parser and Python's `tomllib`
agree exactly:

- One `[features]` table (an optional `[settings]` table may follow it).
- Keys are quoted string keys of the form `"spawnterm.<capability>"`.
- Values are the bare literals `true` or `false` (no other TOML types).

`spawnterm-flag enable`/`disable` always rewrite the file with the **full seeded schema**
in canonical order, so the file is deterministic regardless of which implementation
(shell or Python) wrote it — the two produce byte-for-byte identical output.

## Flag schema

Namespaced keys are `spawnterm.<capability>`. All default **`false`**.

| Key | Capability |
|-----|------------|
| `spawnterm.status_board` | Tier 0 escape-code status board (agents emit state; iTerm2 paints it). |
| `spawnterm.worktree_isolation` | Per-agent git-worktree + `$PORT` isolation. |
| `spawnterm.messaging` | Cross-tab agent-to-agent messaging via the broker. |
| `spawnterm.agent_inbox` | Durable per-agent inbox surface. |
| `spawnterm.cost_dashboard` | Token/cost dashboard. |
| `spawnterm.janitor` | Background cleanup of stale worktrees/sessions. |
| `spawnterm.mcp` | MCP surface exposing spawnterm to agents. |
| `spawnterm.daemon` | Tier 1 iTerm2 Python API orchestration daemon (registry + ingest/idle). |
| `spawnterm.broker` | Tier 2 external broker (durable sqlite mailbox/registry/state/ack over a unix socket). |
| `spawnterm.review` | Per-agent diff/review surface (show worktree diff vs base; approve→merge / request-changes). |
| `spawnterm.tmux` | Tier 3 tmux `-CC` persistence: spawn agents inside a native tmux `-CC` session so windows/agents survive quit/crash and can be reattached. |
| `spawnterm.claude_statusbar` | Claude Code session status aggregator status-bar component (Waiting/Working/Idle across all windows), adapted from gnachman/iTerm2#648. |
| `spawnterm.agent_menubar` | Menu bar status item with a live count badge of busy AI agents, adapted from gnachman/iTerm2#670. |
| `spawnterm.codex_status` | Show Codex CLI working/idle activity in the tab status by decoding the braille-spinner title prefix, adapted from gnachman/iTerm2#673. |

## Default-OFF rule

A flag reads as **OFF** when any of the following is true:

- the config file does not exist,
- the `[features]` table is missing,
- the key is absent, or
- the value is not exactly `true`.

**Reads never write a file.** A query on a machine with no config succeeds as "OFF"
without creating anything. Only `enable`/`disable` create or modify the file.

## `spawnterm-flag` contract

Canonical CLI: `spawnterm/flags/spawnterm-flag` (pure shell). Python twin:
`spawnterm/flags/spawnterm_flag.py`, runnable as `python3 spawnterm_flag.py …` or
`python3 -m spawnterm_flag …`, with identical behavior and output. The Python module
also exposes `is_enabled(key: str) -> bool` for the daemon.

Keys may be given **with or without** the `spawnterm.` prefix; they are normalized.

### Query (default command)

```
spawnterm-flag <key>
```

- Flag ON  → prints `1` to stdout, exits **0**.
- Flag OFF / absent / missing-config → prints `0` to stdout, exits **1**.

This makes both usages work:

```sh
if spawnterm-flag spawnterm.messaging >/dev/null; then …; fi   # exit-code form
state=$(spawnterm-flag spawnterm.messaging)                     # captured stdout ("1"/"0")
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
bash spawnterm/flags/tests/test_flags.sh
```

Covers default-OFF (no config), enable→ON, disable→OFF, `list`, prefix normalization,
`path`, unknown-key handling, no-args usage errors, `is_enabled()` import, and full
shell/Python parity (identical stdout, exit codes, and on-disk config files).
