# spawnterm-janitor — verification/merge gate (#15)

Unverified merges compound bugs, and parallel agents multiply the risk. The
**janitor** runs a project's own verification gate (lint + typecheck + tests)
inside an agent's worktree *before* that branch is mergeable. It reports
pass/fail per check, **blocks** (non-zero exit) on any failure, and — combined
with optional file-level ownership — enables high-confidence auto-merge.

`spawnterm-janitor` is a self-contained `sh` tool (matching the #13/#14 suite).
It shells out only to `git`, the sibling `spawnterm-worktree` (#13) and
`spawnterm-flag` (#11), and the **project-provided** gate commands. It never
touches iTerm2 source.

```
spawnterm-janitor <command> [<agent-id>] [options]
```

| Command   | Pure? | Gated? | What it does |
|-----------|-------|--------|--------------|
| `config`  | yes   | no     | Print the resolved gate (source + per-check commands). |
| `resolve` | yes   | no     | Print the target (branch/worktree/base) via the #13 allocator. |
| `owns`    | pure-ish (reads git) | no | Report whether the agent's diff is auto-merge-eligible. |
| `check`   | side-effecting | **yes** (`spawnterm.janitor`) | Run the gate in the worktree; block on failure. |

---

## Gate config

The checks are **configurable per project** — no ecosystem is hardcoded.
Discovery precedence (first hit wins):

1. `--config <file>` — explicit TOML with a `[gate]` table.
2. `$SPAWNTERM_GATE_CONFIG` — same, from the environment.
3. `<repo>/.spawnterm/gate.toml` — repo-local TOML `[gate]` table.
4. `<repo>/spawnterm-gate.sh` — a repo-local executable; the **whole** gate is
   that one script (surfaced as a single check named `gate`).
5. **Auto-detect** (fallback): npm scripts (`package.json`) → Makefile targets →
   Python tools (`ruff`/`mypy`/`pytest`). Only checks that actually exist are
   emitted. Explicit config **always** overrides auto-detection.

### `[gate]` table format

Each key is a check name; its value is the shell command to run. File order is
preserved. Arbitrary check names are allowed — `lint`/`typecheck`/`test` are
merely the conventional ones.

```toml
[gate]
lint = "npm run lint"
typecheck = "npm run typecheck"
test = "npm test"
```

```toml
# a Python project
[gate]
lint = "ruff check ."
typecheck = "mypy ."
test = "pytest -q"
```

An **empty gate** (nothing configured or detected) verifies nothing: `check`
warns and reports `overall=empty` with exit 0 (an empty gate is not a failure).

### Auto-detection details

- **npm** (`package.json` present): maps `lint`→`npm run lint`,
  `typecheck`/`type-check`→`npm run typecheck`, `test`→`npm test`, but only for
  scripts the file actually declares (grep heuristic — use explicit config when
  you need precision).
- **Makefile**: maps `lint`/`typecheck`/`test` targets to `make <target>`.
- **Python** (`pyproject.toml`/`setup.cfg`/`setup.py`/`*.py`): emits
  `ruff check .`, `mypy .`, `pytest` for whichever of those tools is on `PATH`.

Inspect what will run without executing anything:

```
spawnterm-janitor config --repo /path/to/repo
```
```
repo=/path/to/repo
gate-source=config:/path/to/repo/.spawnterm/gate.toml
check=lint cmd=npm run lint
check=typecheck cmd=npm run typecheck
check=test cmd=npm test
checks=3
```

---

## The `check` contract

`check` resolves the agent's worktree (via #13 from `<agent-id>` + `--role`, or
an explicit `--worktree`), then runs each gate command with **CWD = the
worktree**.

### Exit codes

| Exit | Meaning |
|------|---------|
| `0`  | `overall=ok` (every check passed) **or** `overall=empty` (no gate). Mergeable. |
| `1`  | `overall=blocked` (≥1 check failed). **Do not merge.** |
| `2`  | Usage / resolution error (bad args, missing worktree, etc.). |

When the `spawnterm.janitor` flag is **OFF**, `check` is a no-op: it prints that
the feature is disabled and exits `0` (fail-safe, matching the suite).

### Machine-readable summary

`check` prints a stable `key=value` block to stdout **and** saves it to
`<worktree>/.spawnterm-janitor/summary.txt`:

```
repo=/path/to/repo
worktree=/path/to/wt/worker-ok15-ab12cd
branch=spawnterm/worker-ok15-ab12cd
gate-source=config:/path/to/gate.toml
check=lint status=pass
check=test status=fail exit=1 log=/path/to/wt/.../.spawnterm-janitor/test.log
overall=blocked
```

### Surfacing failing output

Every check's combined stdout+stderr is captured to
`<worktree>/.spawnterm-janitor/<check>.log`. For **failing** checks the janitor
also echoes the captured output to stdout, fenced so a reviewer (or #14's
review pane) can read it inline:

```
----- FAILED: test (exit 1) -----
<the test command's output>
----- end test -----
```

So the review surface has two ways to see failures: read `summary.txt` +
`<check>.log`, or just run `check` and show its stdout.

`--dry-run` prints the exact `(cd <worktree> && <cmd>)` line per check and
writes nothing.

---

## File-level ownership → conflict-free auto-merge (optional)

To let trusted, well-scoped changes auto-merge, declare which files each agent
may touch in an **ownership map** (`--map <file>`, default
`<repo>/.spawnterm/ownership.toml`):

```toml
[ownership]
"src/api/**" = "backend"
"src/ui/**"  = "frontend"
"docs/**"    = "docs-bot"
```

`owns <agent>` reports whether the agent's diff stays **entirely** within the
globs it owns:

```
spawnterm-janitor owns backend --repo /repo --role worker
```
```
agent=backend
map=/repo/.spawnterm/ownership.toml
branch=spawnterm/worker-backend-ab12cd
base=main
owned-globs=src/api/**
changed-files=3
eligible=yes
```

- Changed files come from `git diff --name-only <base>...<branch>` (the same
  three-dot range #14 shows/merges), or from an explicit `--files "a b c"` list
  (which makes the predicate testable in isolation).
- **Eligible** (exit 0) iff the diff is non-empty *and* every changed file
  matches at least one glob the agent owns. Otherwise **not eligible** (exit 1);
  unowned files are listed on an `unowned=` line, and an empty diff is not
  eligible (nothing to merge).
- Globs use shell `case` semantics: `*` (and `**`, normalized to `*`) match
  across `/`. Keep the map optional — nothing requires it.

The predicate is advisory: an auto-merge flow runs `check` (gate must pass)
**and** `owns` (diff must be owned) before merging without human review.

---

## Feature flag

`check` self-gates on **`spawnterm.janitor`** (seeded in #11, default OFF),
consulted via `spawnterm-flag`:

```
spawnterm-flag enable spawnterm.janitor    # turn the janitor on
```

- OFF (or the flag helper missing) → `check` no-ops and exits 0 (fail-safe).
- Bypass locally with `--no-gate` or `SPAWNTERM_FORCE=1`.
- `config`, `resolve`, and `owns` are pure and never gate.

---

## Interop

### #13 — worktrees (`spawnterm-worktree`)

`check`/`owns`/`resolve` reuse the #13 allocator (`spawnterm-worktree plan`) to
derive the **same** branch + worktree an agent was spawned on from its
`--id` (+ `--role`), so no extra bookkeeping is needed. `--branch`/`--worktree`
override the derivation. Base detection matches #13/#14
(`origin/HEAD` → `main` → `master` → current branch; override with `--base`).

### #14 — review surface (`spawnterm-review`)

`spawnterm-review approve` (or any auto-merge flow) consults the janitor **before
merging**:

```sh
# gate must pass ...
spawnterm-janitor check "$agent" --role "$role" --repo "$repo" || exit 1
# ... and (for unattended auto-merge) the diff must be owned:
spawnterm-janitor owns "$agent" --role "$role" --repo "$repo" || exit 1
spawnterm-review approve "$agent" --role "$role" --repo "$repo"
```

`check` exiting non-zero is the merge block; its surfaced failing output is what
a reviewer reads before requesting changes.

---

## Tests

Pure logic (gate-config parse + auto-detect, per-check aggregation, the
ownership predicate) and a real tiny gate run all execute against a throwaway
git repo in a tmpdir — fast, no sleeps, non-flaky:

```
bash spawnterm/janitor/tests/test_janitor.sh
```
