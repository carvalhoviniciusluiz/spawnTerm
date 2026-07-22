#!/usr/bin/env python3
"""spawnterm i18n lookup helper (Python implementation).

Cross-cutting i18n foundation for spawnTerm surfaces (#66). English is the
default; pt-BR ships alongside and the framework is extensible (drop a new
``<lang>.json`` catalog in this directory). This module is the Python half of
the shell/Python parity pair (see the sibling ``spawnterm-i18n`` shell script).
It exposes an importable ``t(key, *args) -> str`` and ``active_language() -> str``
plus a CLI identical to the shell tool.

The active language is read from ``[settings] language`` in the shared config
(``$XDG_CONFIG_HOME/spawnterm/config.toml``, same resolution as spawnterm-flag).
Valid values are ``en``, ``pt-BR`` and ``system``; missing/invalid reads as
``en`` and NEVER writes a file. ``system`` resolves from ``$LC_ALL``/``$LANG``
(a ``pt`` prefix -> ``pt-BR``, else ``en``).

Lookup fallback chain: active language -> en -> the key itself. Interpolation
is positional with ``{0} {1}`` placeholders.

Docs: spawnterm/i18n/README.md
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

DEFAULT_LANGUAGE = "en"
VALID_LANGUAGES = ("en", "pt-BR", "system")
CATALOG_DIR = Path(__file__).resolve().parent
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_catalog_cache: dict[str, dict] = {}


def _env(name: str) -> str | None:
    """Return a non-empty environment variable value, or None."""
    value = os.environ.get(name)
    return value if value else None


def config_path() -> Path:
    """Resolve the config file path.

    Precedence: ``$SPAWNTERM_CONFIG`` > ``$XDG_CONFIG_HOME/spawnterm/config.toml``
    > ``~/.config/spawnterm/config.toml``. Mirrors spawnterm-flag exactly.
    """
    override = _env("SPAWNTERM_CONFIG")
    if override:
        return Path(override).expanduser()
    base = _env("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "spawnterm" / "config.toml"


def _load_config() -> dict:
    """Return the parsed config, or an empty dict if unreadable/absent."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def configured_language() -> str:
    """Read ``[settings] language`` verbatim, or the default when absent/invalid."""
    settings = _load_config().get("settings")
    if isinstance(settings, dict):
        value = settings.get("language")
        if value in VALID_LANGUAGES:
            return value
    return DEFAULT_LANGUAGE


def resolve_system() -> str:
    """Resolve the ``system`` pseudo-language from the locale environment."""
    locale = _env("LC_ALL") or _env("LANG") or ""
    return "pt-BR" if locale.lower().startswith("pt") else "en"


def active_language() -> str:
    """Return the resolved active language (``en`` or ``pt-BR``)."""
    lang = configured_language()
    if lang == "system":
        return resolve_system()
    return lang


def available_languages() -> list[str]:
    """Return the languages with a catalog on disk, sorted (e.g. en, pt-BR)."""
    return sorted(p.stem for p in CATALOG_DIR.glob("*.json"))


def _load_catalog(lang: str) -> dict:
    """Load and cache a language catalog; missing/broken catalogs read empty."""
    if lang in _catalog_cache:
        return _catalog_cache[lang]
    catalog: dict = {}
    path = CATALOG_DIR / f"{lang}.json"
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                catalog = loaded
        except (OSError, ValueError):
            catalog = {}
    _catalog_cache[lang] = catalog
    return catalog


def interpolate(template: str, args: tuple) -> str:
    """Replace ``{0} {1} ...`` with positional args; out-of-range left intact."""
    def repl(match: re.Match) -> str:
        idx = int(match.group(1))
        return args[idx] if idx < len(args) else match.group(0)
    return _PLACEHOLDER.sub(repl, template)


def t(key: str, *args: str) -> str:
    """Look up ``key`` for the active language.

    Fallback chain: active language -> en -> the key itself. Positional
    ``{0} {1}`` placeholders are filled from ``args``.
    """
    active = active_language()
    template = _load_catalog(active).get(key)
    if template is None and active != DEFAULT_LANGUAGE:
        template = _load_catalog(DEFAULT_LANGUAGE).get(key)
    if template is None:
        template = key
    return interpolate(template, args)


USAGE = """usage: spawnterm-i18n <command> [args...]

Commands:
  t <key> [args...]   print the localized string for <key> in the active
                      language (fallback: active -> en -> the key itself).
                      {0} {1} ... placeholders are filled by positional args.
  lang                 print the resolved active language (en or pt-BR)
  -h, --help           show this help

Active language comes from [settings] language in the shared config
($XDG_CONFIG_HOME/spawnterm/config.toml). Default en. Reads never create a file."""


def _err(msg: str) -> None:
    print(f"spawnterm-i18n: {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _err("missing command")
        print(USAGE, file=sys.stderr)
        return 2

    command = argv[0]
    rest = argv[1:]

    if command in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if command == "lang":
        print(active_language())
        return 0
    if command == "t":
        if not rest:
            _err("t requires a <key>")
            return 2
        print(t(rest[0], *rest[1:]))
        return 0

    _err(f"unknown command: {command}")
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
