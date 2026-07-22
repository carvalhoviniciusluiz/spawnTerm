# spawnTerm — session handoff (2026-07-22)

You are the fresh tech-lead for **spawnTerm** (independent project; a fork of iTerm2 that orchestrates
AI coding agents across iTerm2 tabs/panes). **All context you need is in this repo — no other local project.**

## Read, in order
1. `AGENTS.md` (root banner) + `spawnterm/AGENTS.md` (agent entry point).
2. **`spawnterm/PLAN.md`** — execution order + dependency graph (the "where do I start").
3. Pinned Epic **#1**: `gh issue view 1 --repo carvalhoviniciusluiz/spawnTerm`.
4. `spawnterm/docs/design.md` — architecture + iTerm2 capability map + motivation.

## State
- Repo `carvalhoviniciusluiz/spawnTerm` has **18 issues**: Epic #1; Tiers #2–#6; Tier 0 sub-tasks #7–#10; capability additions #11–#18. Full docs + scaffolding committed under `spawnterm/`.
- **Start now** (both `ready-for-agent`, no blockers): **#11** feature-flag framework (do FIRST — unlocks every capability's toggle) and **#7** emit helper. Order & unlocks: see `spawnterm/PLAN.md`.

## How to work
- Per issue: claim (`gh issue edit N --add-assignee @me --add-label status:in-progress` + a claim comment) → build under `spawnterm/` → **one PR per issue** (`Closes #N`) → the issue thread is the durable log.
- Every capability gates on `spawnterm.<key>` (default OFF; see #11). Do NOT edit iTerm2 source except `scope:iterm2-core` issues. gh account: `carvalhoviniciusluiz`.

## Pending (needs the operator)
- GitHub Project board: run `gh auth refresh -h github.com -s project`, then it can be created.
- `spawnterm/docs/market-analysis.md`: the full market report isn't written yet (sources are cited in the Epic thread / design.md). Optional to write.

**Delete this handoff once absorbed.** Resume by reading `spawnterm/PLAN.md` and continuing the plan.
