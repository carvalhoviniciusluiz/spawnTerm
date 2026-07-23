# AGENTS.md — it2agent

> **Read this first.** This repository is **it2agent**, a project to orchestrate multiple AI
> coding agents running in separate **iTerm2** tabs/panes. It is a **personal fork of
> `gnachman/iTerm2`** — a parallel version for our own use, **never submitted upstream**. The iTerm2
> source tree is touched **only** for **Tier 4** (iTerm2 core changes made directly in this fork).
> Everything else — the program's own tooling (Tiers 0–3) — lives under **`it2agent/`** and does
> **not** modify iTerm2 source.

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
- **`scope:external-tooling`** issues (Tiers 0–3) → build under `it2agent/`; **never** edit iTerm2
  source for these. They run *on* iTerm2's API + escape codes.
- **`scope:iterm2-core`** issues (Tier 4 / settings pane) → these DO change iTerm2 source, edited
  **directly in this personal fork** (never submitted upstream — see the "Fork policy" comment on
  Epic #1). Follow `CLAUDE.md`; build (`tools/build.sh`) + `ModernTests` to verify.
- **Non-goal:** do not build the durable queue/broker inside iTerm2 (wrong layer — that is Tier 2,
  an external process).
- **Claude Code config convention (standing rule):** any config we need Claude Code to use (hooks,
  env, MCP wiring) is **always** written to the **active project's** `<git-root>/.claude/settings.local.json`
  (per-project, gitignored, machine-local — never the committed `.claude/settings.json`, never global
  unless explicitly opted in), exposed as a **feature-flag** with safe install/uninstall where
  "installed = enabled." Reuse the shared install mechanism; hooks are always exit-0 observers.
  Full rationale + pattern: `it2agent/docs/claude-config-convention.md`.

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
