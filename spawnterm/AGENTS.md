# AGENTS.md — spawnTerm

> **Read this first.** This repository is **spawnTerm**, a project to orchestrate multiple AI
> coding agents running in separate **iTerm2** tabs/panes. It happens to be a **fork of
> `gnachman/iTerm2`**: the iTerm2 source tree is upstream and is touched **only** for **Tier 4**
> (core PRs back to iTerm2). Everything else — the program's own tooling (Tiers 0–3) — lives under
> **`spawnterm/`** and does **not** modify iTerm2 source.

## Where to start
1. Open **Epic issue #1** — it is the single source of truth: the concept, the 5-tier plan with
   dependencies, and the working protocol. https://github.com/carvalhoviniciusluiz/spawnTerm/issues/1
2. The five tiers are sub-issues of the epic (#2–#6). Only issues labeled **`ready-for-agent`**,
   **unassigned**, and **not `status:blocked`** may be claimed.

## Working protocol (per issue)
1. **Claim atomically:** `gh issue edit N --add-assignee @me --add-label status:in-progress`, then
   comment "claimed by <agent>". If already assigned, pick another.
2. **The issue comment thread is the durable log / handoff** — post what you did, the evidence
   (green command output), decisions, and the next step. A fresh agent resumes by reading the thread.
   Nothing important lives only in chat.
3. **One PR per issue**, with `Closes #N` in the PR body (merge to the default branch closes it).
4. Respect dependencies (each issue lists its `Depends on`). Do not start a blocked issue.

## Scope discipline (critical)
- **`scope:external-tooling`** issues (Tiers 0–3) → build under `spawnterm/`; **never** edit iTerm2
  source for these. They run *on* iTerm2's API + escape codes.
- **`scope:iterm2-core`** issues (Tier 4) → these DO change iTerm2 source; open an upstream
  discussion on `gnachman/iTerm2` first, prototype here, then PR upstream.
- **Non-goal:** do not build the durable queue/broker inside iTerm2 (wrong layer — that is Tier 2,
  an external process).

## Labels
`tier:0-escape-codes … tier:4-core` · `scope:external-tooling|iterm2-core` ·
`status:ready|in-progress|blocked` · `ready-for-agent` · `priority:p0|p1|p2` · `type:epic|feature|chore`

## Handy gh recipes
```bash
# next claimable work
gh issue list --label ready-for-agent --search "no:assignee -label:status:blocked" --json number,title
# claim
gh issue edit N --add-assignee @me --add-label status:in-progress
gh issue comment N --body "claimed by <agent>. plan: …"
# link a PR
gh pr create --draft --title "…" --body "Closes #N"
```
