# it2agent/spawn — worktree + `$PORT` / namespace isolation (#13)

`scope:external-tooling` — glue that shells out to `git` and `it2agent-flag`;
it never modifies iTerm2 source.

Git worktrees isolate **files** between parallel agents, but two agents in
separate worktrees still collide the moment they start a dev server, bind a
socket, or open a database — they all reach for port 3000 and the `dev` schema.
The differentiator in `agent.worktree_isolation` is that each agent also
gets its **own port** and its **own service/DB namespace**, injected into the
session environment, so parallel agents never step on each other.

This is delivered by the companion helper **`it2agent-worktree`**, which
`it2agent-spawn` (#10) calls when the flag is ON.

## The isolation model

When `agent.worktree_isolation` is **ON**, spawning an agent:

1. creates a **git worktree on its own branch**, and opens the new tab there
   (instead of plain `$PWD` inheritance), and
2. exports four env vars into the session **before the agent command runs**:

   | Env var | Meaning | Example |
   | --- | --- | --- |
   | `$IT2AGENT_PORT` | The agent's dedicated TCP port. | `41724` |
   | `$IT2AGENT_NS` | Service/DB/schema prefix, safe as an identifier. | `worker_d8763d` |
   | `$IT2AGENT_WORKTREE` | Absolute path of the agent's worktree. | `…/worker-13-d8763d` |
   | `$IT2AGENT_BRANCH` | The agent's branch. | `it2agent/worker-13-d8763d` |

When the flag is **OFF** (the default), none of this happens: `it2agent-spawn`
behaves **exactly as #10** — plain `$PWD` inheritance, no worktree, no port.

### The `$IT2AGENT_PORT` / `$IT2AGENT_NS` contract for agent commands

The agent command is responsible for *honoring* the env vars. it2agent hands
them out; your command wires them in. Typical patterns:

```sh
# start a dev server on the agent's dedicated port
PORT="$IT2AGENT_PORT" npm run dev
vite --port "$IT2AGENT_PORT"

# namespace a database / schema / redis / docker so services don't collide
createdb "${IT2AGENT_NS}_app"
docker compose -p "$IT2AGENT_NS" up
export REDIS_PREFIX="$IT2AGENT_NS:"
```

Both vars default to unset, so a command written for isolation still runs fine
when the flag is OFF (it just uses its own defaults).

## The pure allocator (`plan` / `env`)

All naming/numbering is a **pure, deterministic function** of the inputs — same
`(repo root, agent id, role, base port, span)` always yields the same
`(branch, worktree, port, namespace)`. Inspect it without side effects:

```sh
it2agent-worktree plan --repo . --id 13 --role worker --task "build isolation"
# branch=it2agent/worker-13-d8763d
# worktree=/…/.it2agent-worktrees/<repo>/worker-13-d8763d
# port=41724
# namespace=worker_d8763d
# hash=d8763d
# repo=/…/<repo>

it2agent-worktree env  --repo . --id 13 --role worker   # eval-able exports
```

### Naming / numbering scheme

Let `HASH = sha1("<canonical-repo-path>|<agent-id>")` (hex).

- **branch** = `it2agent/<slug>-<hash6>`
  - `slug` = sanitize(`<role>-<id>`): lowercased, every char outside `[a-z0-9]`
    becomes `-`, runs collapsed, trimmed, truncated to 40 chars; empty → `agent`.
  - `hash6` = first 6 hex chars of `HASH`. This is the **collision-safety
    anchor**: two ids whose slugs happen to collapse to the same slug still get
    distinct branches, because the id feeds the hash. The result is always a
    valid git ref (no spaces, `#`, `~^:?*[`, …).
- **worktree** = `$IT2AGENT_WORKTREE_ROOT/<slug>-<hash6>`, default root
  `<parent-of-repo>/.it2agent-worktrees/<repo-basename>`. Kept **outside** the
  repo tree so it never shows up as untracked in the main checkout. Symlinked
  roots (e.g. macOS `/var → /private/var`) are canonicalized to match what
  `git worktree` stores.
- **port** = `base + (int(first 7 hex of HASH) % span)`. Default `base=41000`,
  `span=1000` ⇒ **range `41000..41999`**. Override with `--base-port` / `--span`.
  This is a deterministic *candidate*; see probing below.
- **namespace** = `<ns-role>_<hash6>`, where `ns-role` = sanitize(role) into
  `[a-z0-9_]` with a guaranteed leading letter (a digit start is prefixed `a`),
  empty → `agent`. Safe to use directly as a DB/schema/service prefix.

Re-spawning the **same agent id** re-derives the **same** branch/port/namespace,
so `create` is idempotent (it reuses an existing worktree).

### Port allocation, probing & the lease

`plan` / `env` and any `--dry-run` report the **deterministic** candidate port
(reproducible on any machine). The real `create` then allocates upward from the
candidate — wrapping within `[base, base+span)` — for a port that is neither a
live TCP listener (via `lsof`/`nc` when available) nor already **leased**, so two
agents that hash to the same port still end up on different ports. Pass
`--no-probe` to force the deterministic port as-is (and skip leasing).

**Why the lease.** The `lsof`/`nc` probe alone is a check-then-use: two agents
spawned near-simultaneously can both observe the same port as free and both bind
it (a TOCTOU race). `create` closes that gap by claiming its chosen port with a
**persisted lease** under an allocation mutex (`flock` where available, else an
atomic `mkdir` lock — the fallback used on macOS, which has no `flock`). The
check-and-claim is therefore atomic across concurrent spawns of the same repo.

- **Where.** Lease files live in `$IT2AGENT_WORKTREE_ROOT/.leases/` (default
  `<parent-of-repo>/.it2agent-worktrees/<repo>/.leases/`) — beside the per-repo
  worktrees, so every concurrent spawn of the repo shares one lease dir and
  `cleanup` (and a future janitor) can see them. Created lazily by `create`;
  never by the pure `plan`/`env`.
- **Contents.** `<port>.lease` records `id`, `repo`, `pid`, `epoch`, and
  `worktree` (key=value lines).
- **Stale reclaim.** A lease is reclaimed (deleted + its port reused) when its
  `worktree` path no longer exists, **or** it records a positive owner `pid` that
  is no longer alive. `pid=0` (the default) means "tied to the worktree only",
  since `create` is itself ephemeral; pass `--lease-pid <n>` to bind a lease to a
  durable process for tighter reclaim. Reclaim runs during allocation, so leases
  never leak permanently even if `cleanup` is skipped.
- **Release.** `cleanup` releases every lease whose `worktree` matches the one it
  removes (matching on identity, since the claimed port may have advanced past
  the deterministic candidate). This is where an explicit teardown hooks in.

## `create` / `cleanup` (the side-effect layer)

```sh
# create the per-agent worktree (gated). --dry-run prints the git plan only.
it2agent-worktree create  --repo . --id 13 --role worker --dry-run

# remove it when done (gated, safe by default).
it2agent-worktree cleanup --repo . --id 13 --role worker
```

`--dry-run` prints exactly the `git` commands it *would* run (as `would-run: …`
lines) plus the resolved allocation, and executes nothing.

A **dirty main checkout does not block** `create`: `git worktree add -b`
branches from committed `HEAD`, leaving the spawner's uncommitted changes
untouched. If the branch already exists, `create` adds a worktree on it; if the
worktree path is already a registered worktree, it reuses it.

### Cleanup safety rules

`cleanup` **never destroys unmerged work**. It refuses (exit 1, nothing removed)
when either guard trips:

1. **Dirty worktree** — `git status --porcelain` in the worktree is non-empty
   (uncommitted or untracked changes). *Fix:* commit/stash, or `--force`.
2. **Unmerged branch** — the branch is not listed by `git branch --merged
   <base>` (it has commits not reachable from the base branch). *Fix:* merge it,
   or `--force`.

The base branch for the merged check is auto-detected (`origin/HEAD`, then a
local `main`/`master`, then the current branch) or given explicitly with
`--base <ref>`. A branch with **no commits beyond base** counts as merged
(“unchanged”) and is safely removed. `--force` overrides both guards and is
**destructive** — it discards uncommitted changes and deletes unmerged branches.
When safe, cleanup runs `git worktree remove`, `git branch -d`, and
`git worktree prune`.

## The feature flag

Everything above gates on **`agent.worktree_isolation`** (seeded OFF in
#11). `it2agent-spawn` checks it via `it2agent-flag`; `it2agent-worktree`'s
`create`/`cleanup` **self-gate** on it too, with the same fail-safe convention
as `it2agent-emit` / `it2agent-spawn`:

- flag absent, config missing, or `it2agent-flag` not found ⇒ treated **OFF**
  (fail-safe), the operation is a no-op, exit 0.
- bypass for local testing with `--no-gate` or `IT2AGENT_FORCE=1`.

`plan` and `env` are pure and **never** gate — they only compute.

```sh
it2agent-flag enable agent.worktree_isolation   # turn it on
it2agent-flag disable agent.worktree_isolation  # back to #10 behavior
```

## Tests

```sh
bash it2agent/spawn/tests/test_worktree.sh   # the helper (pure + real git)
bash it2agent/spawn/tests/test_spawn.sh      # spawn integration (gate on/off)
```

`test_worktree.sh` covers the pure allocator (determinism, sanitization, port
range + collision-avoidance), the gate-off no-op, `--dry-run` (asserts the git
plan, no side effects), a **real** `git worktree add`/`remove` cycle in a
throwaway tmp repo, and the cleanup safety refusals (dirty + unmerged). It is
fast and non-flaky. `test_spawn.sh` additionally asserts that spawn with the
gate OFF is byte-for-byte the #10 behavior, and that with the gate ON the tab
`cd`s into the worktree and exports `$IT2AGENT_PORT` / `$IT2AGENT_NS`.
