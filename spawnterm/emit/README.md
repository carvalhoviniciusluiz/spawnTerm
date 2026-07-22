# spawnterm/emit

Tier 0.1 (#7) / Tier 0.2 (#8) — escape-code emitter. Agents call this to signal
their own state by writing iTerm2 proprietary escape codes to their own stdout.
`scope:external-tooling` — runs *on* iTerm2's escape codes; does not modify
iTerm2 source.

> To stamp a *newly spawned* tab's identity from birth, see the spawn wrapper in
> [`spawnterm/spawn`](../spawn/README.md) (#10), which calls these commands.

Two byte-for-byte-identical implementations are provided so agents can use
whichever is convenient:

- `spawnterm-emit` — pure POSIX shell
- `spawnterm_emit.py` — Python 3 (no external deps)

## Usage

```
spawnterm-emit [--no-gate] <command> [args]
```

| Command | Emits (framing: `OSC = ESC ]`, terminator `ST = BEL 0x07`) |
| --- | --- |
| `status <value>` | `ESC ] 1337 ; SetUserVar=agent_status=<base64(value)> BEL` |
| `role <value>` | `ESC ] 1337 ; SetUserVar=agent_role=<base64(value)> BEL` |
| `task <value>` | `ESC ] 1337 ; SetUserVar=agent_task=<base64(value)> BEL` |
| `attention [message]` | `ESC ] 1337 ; RequestAttention=yes BEL` then `ESC ] 9 ; <message> BEL` |
| `mark` | `ESC ] 1337 ; SetMark BEL` |
| `progress <state> <pct>` | `ESC ] 9 ; 4 ; <state> ; <pct> BEL` (ConEmu style) |
| `color <role-or-status>` | `ESC ] 1337 ; SetColors=tab=<RRGGBB> BEL` |
| `badge [format]` | `ESC ] 1337 ; SetBadgeFormat=<base64(format)> BEL` |

`SetUserVar` values are base64-encoded because iTerm2 requires it. `progress`
`state` is one of `0` (remove), `1` (normal), `2` (error), `3` (indeterminate),
`4` (paused); `pct` is an integer `0..100`. The default `attention` message is
`spawnterm: agent needs attention`.

`color` sets the **tab** color (key `tab`; iTerm2 has no `tabbg` key). It accepts
a lifecycle status — `busy`, `blocked`, `done`, `idle` — mapped to a
colorblind-safe (Okabe-Ito) hex, or a raw `RGB`/`RRGGBB` hex for other roles.
`badge` sets the session badge; iTerm2 requires the format base64-encoded, and
the default `\(user.agent_role) · \(user.agent_task)` interpolates the user vars
set by `role`/`task`. The full palette, colorblind rationale, and exact byte
sequences are in [`docs/colors.md`](docs/colors.md).

The **only** thing written to stdout is the escape sequence itself. Nothing is
logged; values that iTerm2 requires base64 for are base64'd.

## Feature-flag gating

Like every spawnTerm capability, the emitter gates on its feature flag —
`spawnterm.status_board` — checked via the `spawnterm-flag` helper (#11):

- Emits only when `spawnterm-flag spawnterm.status_board` reports ON (exit 0).
- If the flag is OFF, or `spawnterm-flag` is not on `PATH` (fail-safe:
  capabilities are OFF by default), it emits nothing and exits `0` quietly.
- Bypass for local testing with `--no-gate` or `SPAWNTERM_FORCE=1`.

The coupling is loose: it shells out to `spawnterm-flag` (never imports #11's
internals), so the two land independently.

## Tests

```
bash spawnterm/emit/tests/test_emit.sh
```

Verifies shell/Python output is byte-identical (visualized with `cat -v` /
`od`), exercises the gate (bypass, OFF, ON, and fail-safe-absent paths), and
checks input validation.
