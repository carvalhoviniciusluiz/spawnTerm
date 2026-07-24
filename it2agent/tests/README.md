# it2agent test gates

Two layers, mirrored by two CI workflows. Run these from the **repo root**.

## Pure gate (no running terminal — what hosted CI runs)

```sh
sh it2agent/tests/run_headless.sh
```

Discovers and runs every `test_*.py` / `test_*.sh` under `it2agent/**/tests/`, plus
`live_smoke.py --only ccstatus` (exact OSC 21337 bytes — the one live surface that needs no
terminal). It isolates the flag config via a temp `IT2AGENT_CONFIG` so flags read OFF, matching a
fresh CI host. Exit 0 iff every suite passes. Runs in `.github/workflows/headless-tests.yml`.

## Live gate (needs a dev build + Python API)

```sh
python3 it2agent/tests/live_smoke.py --json
```

Drives the `spawn` / `tmux` / `mcp` surfaces against a running development build with the iTerm2
Python API enabled (Settings → General → Magic → Enable Python API). Fails on any surface that is
not `PASS`. Scope a single surface with `--only <spawn|tmux|mcp|ccstatus>`. Runs on a self-hosted
macOS runner in `.github/workflows/live-smoke.yml` (see `../docs/live-smoke-ci.md`).

## Moat validation (team bridge)

Headless proof of the durable mirror (register + handoff + notify, idempotent, survives broker
death):

```sh
python3 it2agent/tests/coop_team_bridge_mirror.py
```

The full recipe — including the human-in-the-loop step of running a real Claude Code team and
killing the lead — is AC6 in `COOPERATION_VALIDATION_PROMPT.md`.

## Other cooperation drivers

`coop_*.py` in this directory each prove one cooperation acceptance criterion headless
(`coop_mcp_orchestrate.py`, `coop_flag_noop.py`, `coop_fleet_ports.py`, `coop_isolate_exports.py`,
`coop_canonical_singleton.py`). Run any directly with `python3 it2agent/tests/<name>.py`.
