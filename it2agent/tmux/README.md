# it2agent/tmux — Tier 3: persistence via `tmux -CC`

Tracking issue **#5**. `scope:external-tooling` — everything here is glue that
drives iTerm2 (AppleScript) and shells out to `tmux` + the sibling `it2agent-*`
helpers. **It never modifies iTerm2 source.**

## The persistence model

`tmux -CC` is iTerm2's **only native persistence**. Launching `tmux -CC
new-session`/`attach` in an iTerm2 tab makes iTerm2 open real, native iTerm2
windows/tabs — but the processes are owned by the **tmux server**, not iTerm2.
So:

- windows/tabs/panes **reopen in the same state** after you quit or disconnect;
- **agents survive** an iTerm2 crash or a dropped SSH link;
- **multiple humans can attach** to the same session.

This is **not** a broker replacement. It is a clean split of ownership:

| Layer | Owns | it2agent tier |
|-------|------|----------------|
| tmux `-CC` | the **process** + the **window/pane layout** | Tier 3 (this dir, #5) |
| broker | the **durable messages / handoff / state** | Tier 2 (#4) |

tmux brings an agent *back*; the broker tells it *where it was*. See
[`RECOVERY.md`](RECOVERY.md).

## The helper: `it2agent-tmux`

A POSIX-sh wrapper (matching the `it2agent-spawn` / #10 style) with three
subcommands:

```
it2agent-tmux spawn  [options] [--] <command>   # start-or-reattach + run the agent under tmux -CC
it2agent-tmux attach [--id|--role/--task|--session]   # reopen a surviving session (recovery)
it2agent-tmux name   [--id|--role/--task|--session]   # print the derived tmux session name (pure)
```

`spawn` accepts the same working-dir / identity / isolation options as
`it2agent-spawn` (`--dir`/`--home`, `--role`/`--task`/`--status`, `--id` /
`--base-port` / `--no-probe`) plus `--session <name>`.

### Command construction (what `spawn` actually builds)

With the flag ON, `spawn` opens one iTerm2 tab and types a **single** command
line into it — the whole `tmux -CC` invocation — so iTerm2 takes over natively.
The agent's setup script rides *inside* that line as a one-physical-line
`sh -lc` argument (`;`-joined, so iTerm2's `write text` doesn't submit it
piecemeal at newlines):

```
tmux -CC new-session -A -s <session> <login-shell> -lc '<inner>'
```
where `<inner>` is:
```
cd '<cwd>' ; [export IT2AGENT_* …] ; \
  it2agent-emit role '<role>' ; it2agent-emit task '<task>' ; \
  it2agent-emit status '<status>' ; it2agent-emit color '<status>' ; \
  it2agent-emit badge ; exec '<agent command…>'
```

- `new-session -A` = **create-or-reattach**: first spawn creates the session and
  runs `<inner>`; a later spawn of the same agent reattaches the still-running
  agent and `<inner>` is **not** re-run. That is the persistence/reattach contract.
- Identity is **delegated to `it2agent-emit`** (the same escape-code helper
  Tier 0 uses); `it2agent-tmux` never re-implements escape codes.
- `exec` makes the agent the tmux pane's main process, so on detach it keeps
  running and on exit the pane closes.
- When `agent.worktree_isolation` (#13) is also ON, `spawn` delegates to
  `it2agent-worktree` for a per-agent worktree/branch and injects
  `IT2AGENT_PORT` / `IT2AGENT_NS` / `IT2AGENT_WORKTREE` / `IT2AGENT_BRANCH`
  into `<inner>` before the emits/command.

See the exact output with `--dry-run` (prints the session name, the inner
script, the tmux command, and the AppleScript; runs nothing):

```sh
it2agent-tmux spawn --no-gate --role worker --task "build #5" --dry-run -- claude --resume
```

### Session naming (deterministic, collision-safe)

`it2agent-tmux name` derives the session name: lowercase → every char outside
`[a-z0-9_-]` becomes `-` → runs collapsed → trimmed → truncated to 40 →
prefixed `st-`. tmux-hostile `.`/`:` never survive; an empty/all-junk basis
collapses to `st-agent`. The mapping is **deterministic**, so the same agent id
always maps to the same session — a re-spawn *reattaches* (via `-A`) instead of
duplicating. Basis precedence: `--session` > `--id` > `<role>-<task>` >
`<role>` > `<task>`.

## The feature flag: `agent.tmux`

New in #5; added to the schema in **both** flag implementations
(`it2agent-flag` shell + `it2agent_flag.py`) and documented in
[`../docs/feature-flags.md`](../docs/feature-flags.md). **Default OFF.**

`spawn` gates on it with the standard fail-safe / `--no-gate` / `IT2AGENT_FORCE=1`
convention:

- **OFF** (or the flag helper is missing): **no tmux wrapping.** `spawn`
  delegates to `it2agent-spawn` unchanged — behavior is exactly #10 (plain tab,
  `$PWD` inheritance, identity self-gated on `agent.status_board`).
- **ON** (or `--no-gate` / `IT2AGENT_FORCE=1`): wrap the agent in `tmux -CC`.

`attach` and `name` are recovery/introspection and never gate.

```sh
it2agent-flag enable agent.tmux      # opt in
```

## Validating the Python API over tmux-CC (the open research question)

Does the Tier 1 daemon still see/control sessions that are tmux-CC clients —
`new_session`, `custom_escape_sequence`, `prompt`, `async_get_screen_contents`,
`async_set_variable`? Two of these (custom escape sequences and user vars, both
OSC 1337) are at real risk of being swallowed by tmux without passthrough.

- **Automated harness:** [`validate_api_over_tmux.py`](validate_api_over_tmux.py)
  connects via the iTerm2 Python API and measures the surfaces, printing a
  PASS/FAIL table. **It requires a live macOS + iTerm2 + tmux run**; without the
  `iterm2` package / a running iTerm2 it prints setup instructions and exits
  non-zero — it never fabricates results.
- **Manual checklist:** [`API_VALIDATION.md`](API_VALIDATION.md) walks each
  surface by hand, including the crash→reattach persistence check.

### Findings (API over tmux-CC)

> **Status: UNVALIDATED in this environment.** This PR was prepared without a
> live macOS/iTerm2/tmux session available to the author, so the table below is
> intentionally empty. Fill it in from a real run of the harness or the manual
> checklist (do not guess). Expected-risk notes are hypotheses, not results.

| Surface | Result | Notes |
|---------|--------|-------|
| `new_session` (NewSessionMonitor) | _(pending live run)_ | expected OK — tmux windows are real iTerm2 sessions |
| `custom_escape_sequence` (raw OSC 1337) | _(pending live run)_ | **at risk** — tmux may require passthrough wrapping |
| `custom_escape_sequence` (tmux-passthrough) | _(pending live run)_ | fallback to verify if raw fails |
| `prompt` (PromptMonitor) | _(pending live run)_ | needs shell-integration marks through tmux |
| `async_get_screen_contents` | _(pending live run)_ | ack-by-observation |
| `async_set_variable` / user vars | _(pending live run)_ | **at risk** — same OSC 1337 family |

## Recovery / reattach

Full write-up in [`RECOVERY.md`](RECOVERY.md). In short: after a crash, the tmux
server (and the broker) keep running; `it2agent-tmux attach --id <id>` reopens
the window with the agent alive, the daemon re-registers it on re-seed, and the
agent drains its broker inbox/handoff (#35/#36) to resume.

## Interop

- **#13 worktree + `$PORT` isolation:** when that flag is ON, the tmux path
  runs the agent inside its per-agent worktree with the `IT2AGENT_*` env
  exported — same allocation as `it2agent-spawn`, just wrapped in tmux.
- **#4 broker (Tier 2):** the durability layer recovery depends on — see above
  and `RECOVERY.md`. tmux persists process+layout; the broker persists context.
- **#10 spawn:** the gate-OFF path *is* `it2agent-spawn`; the gate-ON path
  reuses its identity (`it2agent-emit`) and isolation (`it2agent-worktree`)
  helpers rather than re-implementing them.

## Tests

Pure / dry-run, **no live tmux or iTerm2 required**:

```sh
bash it2agent/tmux/tests/test_tmux.sh
```

Covers session-name sanitization + collision-safety + determinism, the gate
(OFF → delegates to `it2agent-spawn` with no tmux; ON → `tmux -CC new-session
-A`), the exact command construction and single-line inner script, a round-trip
proving the `-lc` payload survives quoting as one argv element, `attach`,
`name`, exit codes, and (when `osacompile` is present) that the generated
AppleScript compiles with nested quoting intact. Any live iTerm2+tmux validation
is the **manual** checklist above, clearly marked.
