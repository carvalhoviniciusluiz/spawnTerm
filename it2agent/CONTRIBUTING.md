# Contributing to it2agent

**Agents:** read `AGENTS.md` first. This file is the human + agent workflow.

## One issue → one branch → one PR
1. Pick a **`ready-for-agent`**, unassigned, non-`status:blocked` issue (start from **Epic #1**).
2. **Claim:** `gh issue edit N --add-assignee @me --add-label status:in-progress` + a claim comment.
3. Branch: `feat/<tier>-<slug>` (e.g. `feat/tier0-escape-emit`).
4. Build under `it2agent/` (Tiers 0–3). Do **not** modify iTerm2 source unless the issue is
   `scope:iterm2-core` (Tier 4 / settings pane — edited directly in this fork; follow `CLAUDE.md`).
5. Open a **draft PR** early with `Closes #N`; keep the issue comment thread updated as your log.
6. Ready for review → mark the PR ready. Merge to the default branch closes the issue.

## Definition of Done
Each issue lists its own DoD checkboxes. Don't close an issue whose DoD isn't fully checked.

## Scope tags
- `scope:external-tooling` — runs on iTerm2's API/escape codes; lives in `it2agent/`.
- `scope:iterm2-core` — changes to iTerm2 source are made directly in this personal fork; never
  submitted upstream (see the "Fork policy" comment on Epic #1). Built + tested here.

## Durable log
The **issue comment thread** is the source of continuity. Post progress, evidence, and the next
step so a fresh agent can resume without any chat/session context.
