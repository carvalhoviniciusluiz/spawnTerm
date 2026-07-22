# spawnTerm — iTerm2 core change plans (fork-direct)

These are the **`scope:iterm2-core`** items: they change **iTerm2 source**. Per the **Fork policy**
(the pinned "Fork policy" comment on Epic #1), spawnTerm is a **personal fork** — changes to iTerm2
source are made **directly in this fork and are never submitted upstream** to `gnachman/iTerm2`.
There is no maintainer discussion and no upstream PR. These issues are claimed and built like any
other, as soon as their dependencies close.

How core work differs from the Tier 0–3 external tooling:
1. It edits files **outside `spawnterm/`** (the iTerm2 source tree). Follow `CLAUDE.md` strictly
   (`it_fatalError`/`it_assert`, `DLog`/`RLog`, `[iTermUserDefaults userDefaults]`, `NoSync` prefix
   for local-only state, `tools/add_file_to_xcodeproj.rb` for new files, `tools/build_proto.sh`
   after proto edits, warnings-as-errors, update `docs/notes-3.7.txt`).
2. It is verified by **compiling** (`tools/build.sh`) and **`ModernTests`** (`tools/run_tests.expect`).
   The full-app run is the operator's test phase.
3. **Don't change stock defaults silently** — gate new behavior behind an advanced setting / flag,
   default OFF.

## Guiding principle (applies to every item)
iTerm2 is the **substrate/transport**; spawnTerm's external broker (Tier 2) owns the durable
queue/registry/state/ack. **No core change turns iTerm2 into a message broker/queue.** Each is a
minimal, orthogonal primitive on top of machinery iTerm2 already has.

## Index (implementation plans, grounded with `file:line`)
- [`12-settings-pane.md`](12-settings-pane.md) — issue #12: a spawnTerm capability section in
  **Settings → General → AI** (checkboxes bound to the `spawnterm.<key>` flags).
- [`06-tier4-core-prs.md`](06-tier4-core-prs.md) — issue #6: three independent core primitives —
  (A) optional delivery ack on `async_send_text`, (B) queryable session registry + labels,
  (C) user-var sidecar persistence. Decomposed into #49 / #50 / #51.

> Historical note: these docs were first drafted as upstream proposals before the fork-policy
> decision. The technical grounding (files, lines, minimal change surface) still stands; only the
> "post upstream / discuss with maintainer" framing is void — the work is done here, in the fork.
