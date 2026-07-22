# it2agent/flags

Foundation (#11) — the per-user feature-flag framework every it2agent capability
gates on. **All flags default OFF.**

- `it2agent-flag` — canonical CLI (pure shell, bash 3.2+).
- `it2agent_flag.py` — Python twin with identical CLI (`python3 it2agent_flag.py …`
  or `python3 -m it2agent_flag …`) plus an importable `is_enabled(key) -> bool` for
  the daemon.
- `tests/test_flags.sh` — parity + behavior tests.

Config: `$XDG_CONFIG_HOME/it2agent/config.toml` (falls back to `~/.config/...`).

Full schema, file format, and the CLI contract/exit codes:
**`it2agent/docs/feature-flags.md`**.

```sh
it2agent-flag list
it2agent-flag enable agent.messaging
if it2agent-flag agent.messaging >/dev/null; then echo on; fi
```

`scope:external-tooling` — do not modify iTerm2 source here.
