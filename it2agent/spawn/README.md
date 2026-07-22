# it2agent/spawn

Tier 0.4 (#10) — spawn integration. `scope:external-tooling` — glue that drives
iTerm2 via its AppleScript API and shells out to `it2agent-emit`; it never
modifies iTerm2 source and never re-implements escape codes.

`it2agent-spawn` opens a **new iTerm2 tab**, `cd`s it into a working directory,
stamps the new agent's identity (role / task / status / color / badge) from
birth, and then runs a command. It is a **self-contained reference wrapper**: it
works on its own, and it doubles as a copy-paste template for retrofitting the
operator's real `spawn-tl` flow (see [Wiring into an existing `spawn-tl`](#wiring-into-an-existing-spawn-tl)).

## Usage

```
it2agent-spawn [options] [--] <command> [args...]
```

The trailing args (after `--`, or after the last recognized option) are the
command run in the new tab. A command is required.

```sh
# Inherit the current folder, tag as a worker, run Claude:
it2agent-spawn --role worker --task "build #10" -- claude --dangerously-skip-permissions

# Open in a specific folder, tag as an idle reviewer, start a login shell:
it2agent-spawn --dir ~/proj/api --role reviewer --status idle -- "$SHELL" -l
```

### Working directory (the #3 quick win — no daemon)

The whole point of the #3 quick win is that you should not have to retype the
folder. `it2agent-spawn` captures the spawner's directory and opens the new tab
there:

| Selection | Resolved cwd |
| --- | --- |
| *(default)* | The spawner's `$PWD` — the new tab inherits your current folder |
| `--dir <path>` | That specific folder |
| `--home` | The user's home directory (`$HOME`) |

Precedence is `--home` > `--dir` > inherit `$PWD`. `--home` and `--dir` are
mutually exclusive. This is the no-daemon path: the tab is opened at the
resolved directory via a `cd` written into the new session (Tier 1's daemon will
later read the parent session's cwd directly).

### Identity flags (stamped on spawn)

After the tab is created, `it2agent-spawn` runs the merged `it2agent-emit`
**in the new session**, once per facet, so the tab is tagged/colored/badged
immediately:

| Flag | Emit call made in the new session | Sets |
| --- | --- | --- |
| `--role <role>` | `it2agent-emit role <role>` | user var `agent_role` |
| `--task <task>` | `it2agent-emit task <task>` | user var `agent_task` |
| `--status <s>` | `it2agent-emit status <s>` | user var `agent_status` |
| *(from status)* | `it2agent-emit color <s>` | tab color (lifecycle → hex) |
| *(always)* | `it2agent-emit badge` | session badge (`role · task`) |

`--status` is one of `busy`, `blocked`, `done`, `idle` (default `busy`); the tab
color is derived from it via `it2agent-emit color`, which maps each lifecycle
status to a colorblind-safe (Okabe-Ito) hex. The user-var names are **dot-free**
(`agent_role`, `agent_task`, `agent_status`) because iTerm2 forbids a `.` in a
`SetUserVar` key — `it2agent-emit` owns that detail; we just call it.

Identity is **entirely delegated** to `it2agent-emit`; this wrapper contains no
escape codes of its own. It finds the emit helper next to itself
(`../emit/it2agent-emit`) or on `PATH`, and resolves it to an absolute path so
the call works regardless of the new session's `PATH`.

### Capability-guide header (issue #56)

After the identity emits, `it2agent-spawn` prints a **one-line** pointer into the
new session so a fresh agent learns the it2agent capabilities exist:

```
# it2agent capabilities available — run: it2agent help
```

It points at the guide ([`../AGENT_GUIDE.md`](../AGENT_GUIDE.md), served by
`it2agent help`); it does **not** dump the guide. The header is not gated (it is
a hint, not a capability). Opt out with `--no-guide`. The `--dry-run` output shows
a `guide header : on|off` line and the injected `printf` in the session commands.

### Worktree + port/namespace isolation (issue #13 — gated OFF by default)

Parallel agents in separate git worktrees still collide on ports/DBs/services.
When the feature flag `agent.worktree_isolation` is **ON**, `it2agent-spawn`
delegates to the companion helper [`it2agent-worktree`](./it2agent-worktree) to
give each agent its **own git worktree on its own branch** (used as the tab's
working directory) plus a per-agent `$IT2AGENT_PORT` and service/DB namespace
`$IT2AGENT_NS`, exported into the session before the command runs:

```sh
# per-agent worktree + port + namespace (needs agent.worktree_isolation ON)
it2agent-spawn --id 13 --role worker --task "isolate" -- claude
```

Relevant spawn flags: `--id <id>` (anchors the deterministic allocation;
defaults from `--role`/`--task`), `--base-port <n>` (default 41000), and
`--no-probe`. When the flag is **OFF**, spawn behaves **exactly as #10** — plain
`$PWD` inheritance, no worktree, no port. Full model, the
`$IT2AGENT_PORT`/`$IT2AGENT_NS` contract, the branch/worktree naming scheme,
and the cleanup safety rules are in **[WORKTREE.md](./WORKTREE.md)**.

## Feature-flag gating

Spawning a tab is core, so it **always** happens. The identity emits gate
themselves: each is a plain call to `it2agent-emit`, which self-gates on the
`agent.status_board` feature flag (via `it2agent-flag`). When the flag is
OFF the emit calls produce nothing and exit 0, so the tab opens but simply isn't
tagged. This is deliberately the simplest correct design — **one gate, in one
place** (emit), rather than a second gate here that could drift.

Worktree/port isolation is the one thing `it2agent-spawn` gates directly
(it is spawn-level behavior, not something delegated to a self-gating helper):
it checks `agent.worktree_isolation` via `it2agent-flag`, with the same
fail-safe / `--no-gate` / `IT2AGENT_FORCE=1` convention. See
[WORKTREE.md](./WORKTREE.md).

Bypass the gate for local testing:

- `--no-gate` — forwarded as `--no-gate` to every emit call.
- `IT2AGENT_FORCE=1` in the spawner's environment — forwarded as `--no-gate`
  into the emit calls written to the new session (so your force intent survives
  the hop into the child session, whose environment you don't otherwise
  inherit).

## How the tab is opened (AppleScript)

On stock iTerm2 without the Tier 1 daemon, the tab is created with `osascript`
against iTerm2's scripting model, which exposes `create tab with default
profile`, `current session`, and `write text`. The wrapper:

1. creates a tab in the current window (or a new window if none is open),
2. writes `cd '<resolved-cwd>'` into `current session`,
3. writes each `it2agent-emit …` identity call,
4. writes the user command.

All values are shell-quoted for the new session and then escaped for the
AppleScript string literal. Run with `--dry-run` (or `IT2AGENT_SPAWN_DRYRUN=1`)
to print the resolved cwd, the exact emit calls, the command, and the full
AppleScript **without executing anything** — useful for debugging and for
verifying the plan on a machine where iTerm2 isn't running.

## Wiring into an existing `spawn-tl`

If you already have a `spawn-tl` that opens tabs its own way, you don't need this
wrapper's tab-opening — you only need the **identity block**. Drop these lines
into your flow, right after the new session exists and has `cd`'d into place:

```sh
# --- it2agent identity stamp (paste into your spawn-tl) ---------------------
# Resolve the merged emit helper once (sibling copy, else PATH).
EMIT="$(command -v it2agent-emit || echo /path/to/it2agent/emit/it2agent-emit)"

ROLE="worker"          # or whatever your flow already knows
TASK="build #10"
STATUS="busy"          # busy | blocked | done | idle

# cwd inheritance (the #3 quick win): default to the spawner's $PWD.
TARGET_DIR="${TARGET_DIR:-$PWD}"     # override with a --dir arg or $HOME as needed
cd "$TARGET_DIR" || exit 1

# Stamp identity. Each call self-gates on agent.status_board, so these are
# safe to leave in unconditionally — they no-op when the flag is OFF.
"$EMIT" role   "$ROLE"
"$EMIT" task   "$TASK"
"$EMIT" status "$STATUS"
"$EMIT" color  "$STATUS"
"$EMIT" badge
# ---------------------------------------------------------------------------
```

Because emit self-gates, there is nothing else to guard: leave the block in and
it costs nothing until the operator turns `agent.status_board` on.

## Tests

```
bash it2agent/spawn/tests/test_spawn.sh       # spawn (incl. isolation gate on/off)
bash it2agent/spawn/tests/test_worktree.sh    # the it2agent-worktree helper
```

`test_spawn.sh` runs entirely in `--dry-run` (no iTerm2 needed) and asserts:
default cwd equals the spawner's `$PWD`; `--dir`/`--home` overrides; the identity
emits shell out to the merged `it2agent-emit` and set the dot-free user vars;
gate forwarding; that isolation is OFF by default (= #10, no port export) and,
when forced ON, the tab `cd`s into the worktree and exports `$IT2AGENT_PORT` /
`$IT2AGENT_NS`; `--help` exits 0; error paths exit 2; and that the generated
AppleScript compiles (`osacompile`, when available).

`test_worktree.sh` covers the pure allocator (determinism, branch/namespace
sanitization, port range + collision-avoidance), the gate-off no-op, `--dry-run`
(git plan, no side effects), a real `git worktree add`/`remove` cycle in a
throwaway tmp repo, and the cleanup safety refusals (dirty + unmerged). See
[WORKTREE.md](./WORKTREE.md).

## Notes / scope

- No external dependencies beyond `osascript` (macOS built-in) and
  `it2agent-emit`.
- Only a POSIX shell implementation is shipped. A Python twin was optional for
  this tier (parity is not required here) and was intentionally omitted to keep
  a single source of truth for the spawn glue.
