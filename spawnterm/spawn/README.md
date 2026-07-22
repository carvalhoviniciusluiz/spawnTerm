# spawnterm/spawn

Tier 0.4 (#10) — spawn integration. `scope:external-tooling` — glue that drives
iTerm2 via its AppleScript API and shells out to `spawnterm-emit`; it never
modifies iTerm2 source and never re-implements escape codes.

`spawnterm-spawn` opens a **new iTerm2 tab**, `cd`s it into a working directory,
stamps the new agent's identity (role / task / status / color / badge) from
birth, and then runs a command. It is a **self-contained reference wrapper**: it
works on its own, and it doubles as a copy-paste template for retrofitting the
operator's real `spawn-tl` flow (see [Wiring into an existing `spawn-tl`](#wiring-into-an-existing-spawn-tl)).

## Usage

```
spawnterm-spawn [options] [--] <command> [args...]
```

The trailing args (after `--`, or after the last recognized option) are the
command run in the new tab. A command is required.

```sh
# Inherit the current folder, tag as a worker, run Claude:
spawnterm-spawn --role worker --task "build #10" -- claude --dangerously-skip-permissions

# Open in a specific folder, tag as an idle reviewer, start a login shell:
spawnterm-spawn --dir ~/proj/api --role reviewer --status idle -- "$SHELL" -l
```

### Working directory (the #3 quick win — no daemon)

The whole point of the #3 quick win is that you should not have to retype the
folder. `spawnterm-spawn` captures the spawner's directory and opens the new tab
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

After the tab is created, `spawnterm-spawn` runs the merged `spawnterm-emit`
**in the new session**, once per facet, so the tab is tagged/colored/badged
immediately:

| Flag | Emit call made in the new session | Sets |
| --- | --- | --- |
| `--role <role>` | `spawnterm-emit role <role>` | user var `agent_role` |
| `--task <task>` | `spawnterm-emit task <task>` | user var `agent_task` |
| `--status <s>` | `spawnterm-emit status <s>` | user var `agent_status` |
| *(from status)* | `spawnterm-emit color <s>` | tab color (lifecycle → hex) |
| *(always)* | `spawnterm-emit badge` | session badge (`role · task`) |

`--status` is one of `busy`, `blocked`, `done`, `idle` (default `busy`); the tab
color is derived from it via `spawnterm-emit color`, which maps each lifecycle
status to a colorblind-safe (Okabe-Ito) hex. The user-var names are **dot-free**
(`agent_role`, `agent_task`, `agent_status`) because iTerm2 forbids a `.` in a
`SetUserVar` key — `spawnterm-emit` owns that detail; we just call it.

Identity is **entirely delegated** to `spawnterm-emit`; this wrapper contains no
escape codes of its own. It finds the emit helper next to itself
(`../emit/spawnterm-emit`) or on `PATH`, and resolves it to an absolute path so
the call works regardless of the new session's `PATH`.

## Feature-flag gating

Spawning a tab is core, so it **always** happens. The identity emits gate
themselves: each is a plain call to `spawnterm-emit`, which self-gates on the
`spawnterm.status_board` feature flag (via `spawnterm-flag`). When the flag is
OFF the emit calls produce nothing and exit 0, so the tab opens but simply isn't
tagged. This is deliberately the simplest correct design — **one gate, in one
place** (emit), rather than a second gate here that could drift.

Bypass the gate for local testing:

- `--no-gate` — forwarded as `--no-gate` to every emit call.
- `SPAWNTERM_FORCE=1` in the spawner's environment — forwarded as `--no-gate`
  into the emit calls written to the new session (so your force intent survives
  the hop into the child session, whose environment you don't otherwise
  inherit).

## How the tab is opened (AppleScript)

On stock iTerm2 without the Tier 1 daemon, the tab is created with `osascript`
against iTerm2's scripting model, which exposes `create tab with default
profile`, `current session`, and `write text`. The wrapper:

1. creates a tab in the current window (or a new window if none is open),
2. writes `cd '<resolved-cwd>'` into `current session`,
3. writes each `spawnterm-emit …` identity call,
4. writes the user command.

All values are shell-quoted for the new session and then escaped for the
AppleScript string literal. Run with `--dry-run` (or `SPAWNTERM_SPAWN_DRYRUN=1`)
to print the resolved cwd, the exact emit calls, the command, and the full
AppleScript **without executing anything** — useful for debugging and for
verifying the plan on a machine where iTerm2 isn't running.

## Wiring into an existing `spawn-tl`

If you already have a `spawn-tl` that opens tabs its own way, you don't need this
wrapper's tab-opening — you only need the **identity block**. Drop these lines
into your flow, right after the new session exists and has `cd`'d into place:

```sh
# --- spawnterm identity stamp (paste into your spawn-tl) ---------------------
# Resolve the merged emit helper once (sibling copy, else PATH).
EMIT="$(command -v spawnterm-emit || echo /path/to/spawnterm/emit/spawnterm-emit)"

ROLE="worker"          # or whatever your flow already knows
TASK="build #10"
STATUS="busy"          # busy | blocked | done | idle

# cwd inheritance (the #3 quick win): default to the spawner's $PWD.
TARGET_DIR="${TARGET_DIR:-$PWD}"     # override with a --dir arg or $HOME as needed
cd "$TARGET_DIR" || exit 1

# Stamp identity. Each call self-gates on spawnterm.status_board, so these are
# safe to leave in unconditionally — they no-op when the flag is OFF.
"$EMIT" role   "$ROLE"
"$EMIT" task   "$TASK"
"$EMIT" status "$STATUS"
"$EMIT" color  "$STATUS"
"$EMIT" badge
# ---------------------------------------------------------------------------
```

Because emit self-gates, there is nothing else to guard: leave the block in and
it costs nothing until the operator turns `spawnterm.status_board` on.

## Tests

```
bash spawnterm/spawn/tests/test_spawn.sh
```

Runs entirely in `--dry-run` (no iTerm2 needed) and asserts: default cwd equals
the spawner's `$PWD`; `--dir`/`--home` overrides; the identity emits shell out to
the merged `spawnterm-emit` and set the dot-free user vars; gate forwarding;
`--help` exits 0; error paths exit 2; and that the generated AppleScript
compiles (`osacompile`, when available).

## Notes / scope

- No external dependencies beyond `osascript` (macOS built-in) and
  `spawnterm-emit`.
- Only a POSIX shell implementation is shipped. A Python twin was optional for
  this tier (parity is not required here) and was intentionally omitted to keep
  a single source of truth for the spawn glue.
