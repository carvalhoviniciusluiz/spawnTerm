# autobrief — SessionStart discovery hook (#113)

When a Claude Code session starts in a project where this hook is installed **and**
`agent.autobrief` is ON, the hook injects a short it2agent capabilities brief into
the model's context via the documented `SessionStart` `additionalContext` channel.
A fresh agent is then born knowing the agentic tooling exists and how to reach it —
always current, because the brief is rendered from the live flag schema + MCP tool
registry ([`../guide/it2agent_guide.py`](../guide/it2agent_guide.py)).

## Observer contract

The event path is a passive **observer**: it **always exits 0** and writes to stdout
**only** the `additionalContext` JSON, and **only** when the gate is open. Flag OFF,
not installed, malformed/empty stdin, render failure, any exception → nothing on
stdout, still exit 0. It can never block or steer Claude Code (only exit code 2
blocks). Diagnostics go to stderr.

## Gate

`agent.autobrief` is a **positive gate, default OFF**: the brief is injected only
when the flag is explicitly ON. Installing the hook wires it into the project;
turning the flag on is the separate "actually inject" switch. Bypass for local
testing with `--no-gate` or `IT2AGENT_FORCE=1`.

## Install (project-local, gitignored)

```sh
it2agent-flag enable agent.autobrief                 # turn injection ON (default OFF)
python3 it2agent/autobrief/it2agent_autobrief_hook.py install --scope project
# ...uninstall removes ONLY our entry:
python3 it2agent/autobrief/it2agent_autobrief_hook.py uninstall --scope project
```

`--scope project` (default) deep-merges one `SessionStart` hook into
`<git-root>/.claude/settings.local.json` (machine-local, **gitignored**, never
committed) and ensures the file is gitignored. `--scope user` targets
`~/.claude/settings.json`. `IT2AGENT_CLAUDE_SETTINGS` overrides the path (tests).
The install reuses the shared settings mechanism in
[`../hookkit/claude_settings.py`](../hookkit/claude_settings.py) — the same
deep-merge / gitignore / marker-based uninstall contract as the team bridge.

See [`../docs/claude-config-convention.md`](../docs/claude-config-convention.md).

## Tests

```sh
python3 it2agent/autobrief/tests/test_autobrief_hook.py
```

Stdlib only, hermetic (temp config + temp git repo; never touches a real
`~/.claude`). Covers the always-exit-0 / stdout-discipline contract (flag OFF ⇒
empty, flag ON ⇒ the brief), and project-local install/uninstall/status.
