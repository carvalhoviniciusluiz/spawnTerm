# spawnTerm — execution plan & dependency graph

> **Start here.** This is the ordered plan for building spawnTerm. It is self-contained: read this +
> the tracking issues (`gh issue list --repo carvalhoviniciusluiz/spawnTerm`) and you know exactly
> what to do and in what order. All code lives under `spawnterm/`. Do **not** edit iTerm2 source
> except in `scope:iterm2-core` issues (Tier 4 / the settings pane).

## What & why (self-contained)
spawnTerm orchestrates multiple AI coding agents running in separate **iTerm2** tabs/panes: spawn
with identity, cross-tab messaging, durable handoffs, at-a-glance status board. It is built on
iTerm2's API + escape codes + a small external broker + tmux. **Motivation (from market research):**
the whole field converges on *git-worktree + tmux + human-gated merge*; the **unsolved frontier is
reliable agent↔agent messaging + durable context/handoff**. tmux `send-keys` is ~70–80% reliable and
drops — so spawnTerm's broker (Tier 2) is **file-based with ack**, which is the differentiator.
iTerm2-native (panes, escape codes, Python API, tmux -CC, arrangements) is the moat vs cloud tools.
**Everything is a feature-flag** (per-user on/off, default OFF) — see #11.

## Rule for every issue
Claim (`gh issue edit N --add-assignee @me --add-label status:in-progress` + a claim comment) →
build under `spawnterm/` → **one PR per issue** with `Closes #N` → the issue comment thread is the
durable log. Each capability **gates on its feature-flag** (`spawnterm.<key>`, default OFF; see #11).

## Dependency graph — who requires who, who unlocks who
```
#11 feature-flag framework ──┬─► (the on/off flag of EVERY capability below)
                             └─► #12 settings pane (GUI for the flags)

#2 Tier 0 board
   └─ #7 emit helper ──┬─► #8 colors/badge
                       ├─► #9 triggers export
                       └─► #10 spawn-tl integration
   (Tier 0 done) ─────────► #3 Tier 1 daemon

#3 Tier 1 daemon ──┬─► #13 worktree+$PORT isolation ─► #15 janitor
                   ├─► #4  Tier 2 broker (file-based+ack) ─┬─► #17 agent inbox
                   │                                        └─► #18 MCP surface
                   ├─► #5  Tier 3 tmux -CC persistence
                   ├─► #14 diff/review surface
                   └─► #16 cost/token dashboard

#3 + #4 (usage evidence) ─────► #6 Tier 4 iTerm2 core changes (fork-direct)
```

## Execution order (phases — do in this order)
**Phase 0 — Foundation (both `ready-for-agent` now; start here):**
1. **#11 feature-flag framework** — DO FIRST; it unlocks the on/off toggle every other capability needs.
2. **#7 Tier 0.1 emit helper** — the base of the status board; then **#8, #9, #10** (each needs #7).
   (#2 is the Tier 0 parent; it is "done" when #7–#10 are done.)

**Phase 1 — Backbone:**
3. **#3 Tier 1 daemon** — needs Tier 0 done. Unlocks most of the rest.
4. **#12 settings pane** — needs #11. `scope:iterm2-core` (edited directly in this fork).

**Phase 2 — Capabilities (all need #3; can run in parallel):**
5. **#4 Tier 2 broker (file-based + ack)** — the differentiator. **#13** worktree+$PORT · **#5** tmux -CC · **#14** review surface · **#16** cost dashboard.

**Phase 3 — Advanced (need their deps):**
6. **#15 janitor** (needs #13) · **#17 agent inbox** (needs #4) · **#18 MCP surface** (needs #4).

**Phase 4 — iTerm2 core (fork-direct):**
7. **#6 Tier 4 core changes** — edited directly in this personal fork (never submitted upstream).

## Operator decision (2026-07-22) — fork is the product
spawnTerm is a **personal-use AI-agent terminal**, a fork of iTerm2 for the agent-orchestration
support iTerm2 lacks. **It will NOT be submitted to `gnachman/iTerm2`.** Therefore the
`scope:iterm2-core` items (#12 settings pane, #6 Tier 4) are built **directly in this fork** —
no upstream discussion, no upstream PR. `scope:iterm2-core` now just means "edits iTerm2 source,
built + tested here." Core changes are verified by compiling (`tools/build.sh`, `ModernTests` via
`tools/run_tests.expect`); the full-app run is the operator's test phase. Follow `CLAUDE.md` strictly
for any iTerm2 source (it_fatalError, external template loader for JS/HTML/CSS, `add_file_to_xcodeproj.rb`,
`build_proto.sh` after proto edits, warnings-as-errors, update `docs/notes-3.7.txt`). The core
implementation plans (with file:line grounding) live under `docs/iterm-core/`. **Official policy:**
changes to iTerm2 source are made directly in this personal fork; never submitted upstream — see the
"Fork policy" comment on Epic #1.

## Progress (live)
**All external-tooling tiers (0–3) + every capability are DONE ✅ and merged. 614 unit tests green on `master` (all pure / iTerm2-free).**
- **Tier 0 (#2) ✅**: #11 feature-flags · #7 emit · #8 colors/badge · #9 triggers · #10 spawn wrapper. Hotfix #23: user-var keys are **dot-free** (`agent_status`, not `agent.status`; iTerm2 rejects `.`).
- **Tier 1 (#3) ✅**: #26 daemon skeleton+registry+subscriptions · #27 spawn+identity+cwd · #28 in-memory router · #29 status-bar dashboard.
- **Tier 2 (#4, the differentiator) ✅**: #34 broker core (sqlite+unix-socket) · #35 durable mailbox+ack+replay · #36 persistent registry+handoff store · #37 daemon↔broker bridge + ack-by-observation. Durable, db-backed messaging with ack — the moat vs tmux send-keys.
- **Tier 3 (#5) ✅**: run agents under `tmux -CC`; API-over-tmux-CC validation shipped as a runnable harness + manual checklist, marked **UNVALIDATED** (needs a live iTerm2+tmux run — do it in the testing phase).
- **Capabilities ✅**: #13 worktree+$PORT · #14 review surface · #15 janitor · #16 cost dashboard · #17 agent inbox · #18 MCP surface.

## What remains
Only **`scope:iterm2-core`** — edits iTerm2 source **directly in this fork** (never upstream); built + tested here:
- **#6** Tier 4 core changes, decomposed: **#51** user-var sidecar (in progress) → **#50** queryable registry + labels · **#49** optional `async_send_text` delivery ack.
- **#12** settings pane (AI-tab GUI to toggle the flags) — depends on #11 (done); after #6 (involves XIB editing).
Plus the live tmux-CC API validation (#5's checklist) to run against a real iTerm2 in the test phase.

## Flags in the schema (all default OFF)
`spawnterm.status_board · worktree_isolation · messaging · agent_inbox · cost_dashboard · janitor · mcp · daemon · broker · review · tmux`

## Reference
- Epic index: issue **#1** (pinned). Architecture + iTerm2 capability map: `spawnterm/docs/design.md`.
- Agent entry point: `spawnterm/AGENTS.md`. Workflow: `spawnterm/CONTRIBUTING.md`.
- gh account: `carvalhoviniciusluiz` (`gh auth switch --user carvalhoviniciusluiz`).
- Pending (needs the operator's gh scope): a GitHub Project board (`gh auth refresh -h github.com -s project`).
