# spawnterm/i18n

Foundation (#66) — the cross-cutting internationalization layer for spawnTerm
surfaces. **English is the default**; **pt-BR** ships alongside, and the
framework is extensible (drop a new `<lang>.json` catalog here). This is the
framework + catalogs + config setting + CLIs only; the Settings GUI picker is a
separate issue (#67).

Not gated by a feature flag — i18n is transversal infra and always available.
Default `en` preserves current behavior.

## Contents

- `en.json` — the **canonical key set** (source of truth for keys).
- `pt-BR.json` — Brazilian Portuguese catalog. Every key must also exist in `en.json`.
- `spawnterm-i18n` — canonical lookup CLI (pure shell, bash 3.2+).
- `spawnterm_i18n.py` — Python twin with an importable `t(key, *args) -> str`
  and `active_language() -> str`.
- `spawnterm-lang` — canonical language selector CLI (shell).
- `spawnterm_lang.py` — Python twin of the selector.
- `tests/test_i18n.sh` (+ `tests/run_tests.sh`) — parity + behavior tests.

## Config

The active language lives under a `[settings]` table in the shared config
(`$XDG_CONFIG_HOME/spawnterm/config.toml`, same path resolution as
`spawnterm-flag`: `$SPAWNTERM_CONFIG` > `$XDG_CONFIG_HOME` > `~/.config`):

```toml
[settings]
language = "en"     # "en" | "pt-BR" | "system"
```

- Missing file / missing table / missing / invalid value all read as **`en`**,
  and a read **never** writes a file.
- `"system"` resolves from the locale environment (`$LC_ALL`, then `$LANG`): a
  `pt` prefix resolves to `pt-BR`, everything else to `en`.
- `spawnterm-lang set` performs a **read-modify-write that preserves an existing
  `[features]` table** (spawnterm-flag's data). The two tables never clobber each
  other, and the shell/Python twins serialize byte-identically.

## Lookup semantics

`spawnterm-i18n t <key> [args...]` prints the string for the active language.

- **Fallback chain:** active language → `en` → the key itself.
- **Interpolation:** positional `{0} {1} …` placeholders, filled from the
  trailing args. Out-of-range placeholders are left intact.

```sh
spawnterm-i18n lang                     # -> en | pt-BR (resolved)
spawnterm-i18n t cap.messaging.name     # -> Messaging  (or "Mensagens" under pt-BR)
spawnterm-i18n t gate.off messaging     # -> spawnterm.messaging is off. Enable it with: ...

spawnterm-lang get                      # -> resolved active language
spawnterm-lang set pt-BR                # -> writes [settings] language, keeps [features]
spawnterm-lang list                     # -> en, pt-BR  (one per catalog on disk)
```

Python import:

```python
import spawnterm_i18n as i18n
i18n.t("cap.messaging.name")            # "Messaging"
i18n.t("gate.off", "messaging")         # interpolated
i18n.active_language()                  # "en"
```

## Key namespace

Keys are dotted. Initial namespaces:

- `cap.<capability>.name` / `cap.<capability>.desc` — display name and one-line
  description for each of the 14 capabilities (`status_board`,
  `worktree_isolation`, `messaging`, `agent_inbox`, `cost_dashboard`, `janitor`,
  `mcp`, `daemon`, `broker`, `review`, `tmux`, `claude_statusbar`,
  `agent_menubar`, `codex_status`).
- `help.*` — the `spawnterm-help` header/intro/section strings.
- `gate.off` — the common capability-gated-off message (`{0}` = capability name).

## Catalog format

Each catalog is a flat JSON object `{"key": "string"}` with **one entry per
line**, values in plain UTF-8. To keep the pure-shell reader simple and in
byte-parity with the Python twin, **catalog values must not contain embedded
double-quotes (`"`) or backslashes (`\`)**, and each entry stays on its own line.
Use straight text (accented characters are fine); avoid `\u`/`\n` escapes.

## How to add a key

1. Add `"your.key": "English text"` to `en.json` (the canonical set), keeping
   one entry per line.
2. Add the same key with its translation to `pt-BR.json` (and any other
   catalog). Missing translations simply fall back to `en`.
3. Use it: `spawnterm-i18n t your.key` (shell) or `i18n.t("your.key")` (Python).
4. Run `bash tests/run_tests.sh` — the no-orphan-keys check guards against a
   `pt-BR` key with no `en` counterpart.

## How to add a language

1. Copy `en.json` to `<lang>.json` (e.g. `es.json`) and translate the values.
   Keep exactly the keys present in `en.json`.
2. That's it — `spawnterm-lang list` auto-discovers it. To make it selectable via
   `spawnterm-lang set`, add the code to `VALID_LANGUAGES` in `spawnterm_i18n.py`
   and the `is_valid_lang`/usage lists in `spawnterm-lang` (+ its Python twin).

`scope:external-tooling` — do not modify iTerm2 source here.
