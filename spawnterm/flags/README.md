# spawnterm/flags

Foundation (#11) — the per-user feature-flag framework every spawnTerm capability
gates on. **All flags default OFF.**

- `spawnterm-flag` — canonical CLI (pure shell, bash 3.2+).
- `spawnterm_flag.py` — Python twin with identical CLI (`python3 spawnterm_flag.py …`
  or `python3 -m spawnterm_flag …`) plus an importable `is_enabled(key) -> bool` for
  the daemon.
- `tests/test_flags.sh` — parity + behavior tests.

Config: `$XDG_CONFIG_HOME/spawnterm/config.toml` (falls back to `~/.config/...`).

Full schema, file format, and the CLI contract/exit codes:
**`spawnterm/docs/feature-flags.md`**.

```sh
spawnterm-flag list
spawnterm-flag enable spawnterm.messaging
if spawnterm-flag spawnterm.messaging >/dev/null; then echo on; fi
```

`scope:external-tooling` — do not modify iTerm2 source here.
