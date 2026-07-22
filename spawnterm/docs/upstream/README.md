# spawnTerm — upstream (iTerm2 core) proposals

These are the **`scope:iterm2-core`** items: they change **iTerm2 source** and therefore
follow a different, slower path than the Tier 0–3 external tooling. Per the project rules
(`AGENTS.md`), for each item:

1. **Discuss upstream first** — open an issue/discussion on `gnachman/iTerm2` (the maintainer).
   Do **not** open a code PR before the maintainer weighs in on the design.
2. **Prototype on the fork** `carvalhoviniciusluiz/iTerm2` (reserved for Tier 4 code).
3. **Upstream PR** from the fork, then follow review.

> **Status: DRAFTS for the operator to review.** Nothing here has been posted upstream and no
> iTerm2 source has been touched. These documents exist so the operator can approve the framing
> before any upstream discussion is opened. The whole point of the guardrail is that a change to
> iTerm2's own codebase is the maintainer's call, not ours to merge.

## Guiding principle (applies to every item)
iTerm2 is the **substrate/transport**; spawnTerm's external broker (Tier 2) owns the durable
queue/registry/state/ack. **No proposal turns iTerm2 into a message broker/queue.** Each ask is a
minimal, orthogonal, *general-purpose* primitive on top of machinery iTerm2 already has — useful to
any scripter or automation, not just spawnTerm. That framing is what makes an upstream PR
acceptable.

## Why now (usage evidence)
Tiers 0–3 are built and merged as external tooling that runs *on* iTerm2's escape codes + Python
API. Real usage surfaced the exact edges the current API cannot reach cleanly — see each proposal's
"Evidence from the working tooling" section. We ask upstream only for what the tooling proved it
needs.

## Index
- [`12-settings-pane.md`](12-settings-pane.md) — issue #12: a spawnTerm capability section in
  **Settings → General → AI** (checkboxes bound to the `spawnterm.<key>` flags).
- [`06-tier4-core-prs.md`](06-tier4-core-prs.md) — issue #6: three independent core primitives —
  (A) optional delivery ack on `async_send_text`, (B) queryable session registry + labels,
  (C) user-var sidecar persistence.

Each proposal is written to be pasted (lightly adapted) into a `gnachman/iTerm2` discussion, and
carries `file:line` grounding so the maintainer can see the change is localized.
