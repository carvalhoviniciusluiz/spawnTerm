# it2agent-review — per-agent diff/review surface (#14)

Human review is **the** throughput bottleneck. When you run agents in parallel
(one worktree each, via #13), review debt multiplies: every branch needs a human
to look at the diff and decide *merge* or *send back*. `it2agent-review` gives a
reviewer **one command per agent** to do exactly that, next to the agent's tab.

`scope:external-tooling`: it only shells out to `git`, the #13 allocator, the #11
flag helper, the #34/#35 broker, and `osascript`. It never modifies iTerm2
source.

```
it2agent-review resolve         <agent> [--role R]        # pure: branch/worktree/base
it2agent-review show            <agent> [--role R]        # diff vs base (--stat + patch)
it2agent-review approve         <agent> [--role R] [--cleanup]
it2agent-review request-changes <agent> "<note>" [--role R]
it2agent-review pane            <agent> [--role R]        # open show in an iTerm2 split
```

## How the target is resolved (reuses #13)

You address an agent the same way you spawned it — by **id (+ role)**.
`it2agent-review` calls the #13 pure allocator (`it2agent-worktree plan --id
<id> --role <role>`) to derive the **same** branch (`it2agent/<slug>-<hash6>`)
and worktree path that agent was created on. No extra registry, no bookkeeping —
the deterministic hash *is* the lookup.

- `--branch <ref>` / `--worktree <dir>` override the derivation (review any
  branch/worktree directly, no agent id needed).
- **Base** is detected exactly like #13's `cleanup`: `origin/HEAD` → local
  `main` → local `master` → the current branch. Override with `--base <ref>`.

`resolve` prints the resolved `branch=/worktree=/base=/repo=` and is **pure** (no
git writes, no gate) — handy for scripting and for confirming what an action will
touch.

## The diff (`show`)

`show` renders the agent's work as **the changes its branch introduced since it
forked base**:

```
git diff <base>...<branch>     # three-dot: diff from the merge-base
```

It prints a `--stat` summary first, then the full patch. The patch is rendered
with, in order of preference:

1. **`delta`** if installed (syntax-highlighted, side-by-side capable),
2. otherwise **git's pager** when stdout is a TTY (git auto-pages through
   `less`),
3. otherwise **plain** `git --no-pager diff` (piped/non-interactive).

It is a normal terminal program, so it drops straight into an iTerm2 pane or any
TUI. `--dry-run` prints the exact `git diff` invocations instead of running them.

## Approve → merge (safe by construction)

`approve` merges the agent's branch into base **only if it is safe**, reusing the
spirit of #13's cleanup safety rules plus a merge-conflict check:

1. **Agent worktree must be clean** — refuses on uncommitted/untracked changes
   (the agent must commit or stash first).
2. **Main checkout must be clean** — so we can check out base and merge without
   clobbering in-progress work.
3. **The merge must be conflict-free** — checked *without* touching the working
   tree via `git merge-tree --write-tree` (git ≥ 2.38), falling back to the
   legacy `git merge-tree` + conflict-marker scan on older git.
4. If the branch has **no commits beyond base**, it is a benign no-op (exit 0).

On success it does `git checkout <base>` → `git merge <branch>` and then
**restores the branch you had checked out** (the main checkout ends where it
started). The merge mode is **`--no-ff` by default** — an auditable merge commit
(`it2agent-review: merge <branch> into <base>`) that records the review event;
pass `--ff-only` to fast-forward instead. Any refusal merges **nothing** and
exits `1`; a failed merge is auto-aborted.

`--cleanup` runs #13's `it2agent-worktree cleanup` after a successful merge
(the branch is now reachable from base, so #13's own merged-check passes and the
worktree+branch are removed safely).

## Request changes (route a note back to the agent)

`request-changes <agent> "<note>"` gets the reviewer's note to the agent. It
prefers the **durable broker mailbox** and degrades gracefully:

- **Broker (preferred, #34/#35).** If a broker socket is reachable
  (`--broker-sock`, else `$IT2AGENT_BROKER_SOCK`, else the XDG default) and
  `python3` is present, it sends a `{"op":"send","to":<agent>,"from":<reviewer>,
  "body":"[review: changes requested] <note>"}` request via the broker
  `BrokerClient` (the thin `review_notify.py` seam). The note is then durable,
  ordered, and re-delivered until the agent **acks** it — strictly better than a
  fire-and-forget nudge.
- **Fallback (no broker).** If the broker is unreachable, or you pass
  `--no-broker`, the note is written to
  `<worktree>/.it2agent-review/CHANGES-REQUESTED-<timestamp>.md` (reviewer,
  branch, base, and the message) **and** printed. The agent — or you — sees it in
  the worktree.

`--from` sets the reviewer id (default `$IT2AGENT_REVIEWER`, else `$USER`, else
`reviewer`). `--dry-run` shows which route it would take and the exact command /
file path, without sending or writing anything — so the routing decision is
observable and testable with no running broker.

## iTerm2 panes / arrangements

Because `show` is a plain terminal program, opening it beside an agent is just an
iTerm2 split. Two ways:

**Helper (optional):**

```sh
it2agent-review pane <agent> --role <role>
```

runs, in the current window, the AppleScript equivalent of *split the current
session vertically and run `it2agent-review show <agent> --role <role>` in the
new pane* — via `osascript` (the same approach as #10's `it2agent-spawn`; no
iTerm2 source is touched). `--dry-run` prints the AppleScript instead of running
it, so it is testable off a Mac.

**By hand / arrangements:** in iTerm2, `⌘D` to split, then run the `show` command
in the new pane; save the layout as an **Arrangement** (Window ▸ Save Window
Arrangement) to reopen a review layout later. The review pane and the agent pane
live side by side; re-run `show` (or `approve` / `request-changes`) as the agent
pushes commits.

## Feature flag

Everything gates on **`agent.review`** (default **OFF**, like every it2agent
capability). The actions (`show` / `approve` / `request-changes` / `pane`)
self-gate via `it2agent-flag`; `resolve` is pure and never gates.

```sh
it2agent-flag enable agent.review
```

Fail-safe: if the flag is OFF (or the flag helper is missing), the action is a
**no-op that says the feature is disabled** and exits `0`. Bypass for local
testing with `--no-gate` or `IT2AGENT_FORCE=1` — the same convention as
`it2agent-emit` / `-spawn` / `-worktree`. The flag is registered in both flag
implementations (`it2agent/flags/it2agent-flag` and `it2agent_flag.py`) and
documented in `it2agent/docs/feature-flags.md`.

## Interop

- **#13 worktrees** (`it2agent/spawn/it2agent-worktree`, `WORKTREE.md`): review
  resolves the agent's branch/worktree from its id via the #13 **plan**
  allocator, uses the same **base detection** as #13 cleanup, and can invoke #13
  **cleanup** after an approved merge (`--cleanup`).
- **Broker #34/#35** (`it2agent/broker`): `request-changes` routes notes through
  the broker `send` op (durable mailbox with ack/replay) when it is running.

## Testing

```sh
bash it2agent/review/tests/test_review.sh
```

Covers: pure resolution reusing #13; gate-off no-op + gate-on; `show` building
the right `git diff <base>...<branch>` invocation; `approve` merging a clean
branch and refusing a dirty worktree / conflicting merge in a throwaway repo;
`request-changes` routing to the broker when reachable and falling back to a
worktree note file when not (plus the broker payload shape); and exit
codes/usage. All real git work runs in a private tmpdir — fast, no sleeps, no
external services.
