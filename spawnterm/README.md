# spawnTerm

Orchestrate multiple AI coding agents running in separate **iTerm2** tabs/panes on macOS:
**spawn** agents with identity, **message across tabs**, durable **handoffs**, and an at-a-glance
**status board**. Built on iTerm2's API + escape codes + a small external broker + tmux.

> This directory (`spawnterm/`) holds the project's own tooling. The rest of the repo is a **fork of
> `gnachman/iTerm2`**, touched only for **Tier 4** (upstream core PRs). See root `AGENTS.md`.

## Why
Today, spawning/coordinating agents across terminal tabs is manual and lossy: no view of who is
working/blocked/done, no reliable cross-tab messaging, handoffs are files someone must find, and a
human can't easily observe or intervene. spawnTerm makes the terminal the substrate and adds the
missing control plane.

## Architecture (one line)
iTerm2 is the **substrate** (spawn/tag/observe/inject/display/persist). The **durable queue +
registry + state + ack** live in an **external broker** — iTerm2 is transport, not router. A Python
daemon bridges the two.

## Roadmap (tracked as issues)
Start at **Epic #1**. Tiers, in order of dependency:

| Tier | What | Scope |
|------|------|-------|
| 0 | escape-code status board (agents emit state; iTerm2 paints it) | external-tooling |
| 1 | orchestration daemon (Python API: registry, spawn-with-identity, router, dashboard) | external-tooling |
| 2 | external broker (durable mailbox, registry, state, ack) | external-tooling |
| 3 | tmux `-CC` persistence (agents survive crash/disconnect) | external-tooling |
| 4 | iTerm2 core PRs (ack on send_text, session registry, persisted user-vars) | iterm2-core (upstream) |

## Layout (planned)
```
spawnterm/
  emit/        # Tier 0 — escape-code helpers (shell + Python) + triggers
  daemon/      # Tier 1 — iTerm2 Python API orchestration daemon
  broker/      # Tier 2 — sqlite/unix-socket broker
  tmux/        # Tier 3 — tmux -CC integration + recovery
  docs/
```

## Contributing
See root `AGENTS.md` (agent workflow) and `CONTRIBUTING.md` (branch → PR → review). One PR per issue;
the issue comment thread is the durable log.
