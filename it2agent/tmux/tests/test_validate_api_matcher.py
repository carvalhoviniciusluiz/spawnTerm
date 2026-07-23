#!/usr/bin/env python3
"""Unit tests for validate_api_over_tmux.py's session-matcher (it2agent Tier 3).

These are PURE tests: they exercise the matching/decision logic that picks which
iTerm2 session the harness probes. They do NOT import `iterm2`, do NOT connect to
iTerm2, and do NOT run the live validation — that path is intentionally untestable
headless (see the harness docstring).

The regression under test is #82: when --session is passed EXPLICITLY and matches
no session, the harness must FAIL LOUDLY (raise) rather than silently fall back to
the current session and report a FALSE PASS. When --session is NOT passed at all,
the current-session fallback (return None) is still allowed.

Run from anywhere:  python3 it2agent/tmux/tests/test_validate_api_matcher.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(os.path.dirname(HERE), "validate_api_over_tmux.py")

# Load the harness as a module by path. Its `iterm2` imports are lazy (inside
# functions), so importing the module here does not require the iterm2 package.
_spec = importlib.util.spec_from_file_location("validate_api_over_tmux", HARNESS)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

pass_count = 0
fail_count = 0


def check(label: str, condition: bool) -> None:
    global pass_count, fail_count
    if condition:
        print(f"  \033[32mPASS\033[0m {label}")
        pass_count += 1
    else:
        print(f"  \033[31mFAIL\033[0m {label}")
        fail_count += 1


NAMES = ["-/probe-ac6-afd7e7", "login shell", "st-other"]


def main() -> int:
    print("=== validate_api_over_tmux.py session-matcher tests (pure) ===")

    # --- _find_matching_session_index ---
    check(
        "substring matches iTerm2 name (case-insensitive)",
        mod._find_matching_session_index(NAMES, "AC6-AFD") == 0,
    )
    check(
        "returns first match index",
        mod._find_matching_session_index(NAMES, "shell") == 1,
    )
    check(
        "no match returns None",
        mod._find_matching_session_index(NAMES, "st-ac6") is None,
    )
    check(
        "empty candidate list returns None",
        mod._find_matching_session_index([], "st-ac6") is None,
    )

    # --- resolve_session_choice: the #82 regression ---
    # Explicit request + a match => that session's index (no fallback).
    check(
        "explicit + match -> matched index",
        mod.resolve_session_choice(NAMES, "ac6-afd", True) == 0,
    )

    # Explicit request + NO match => RAISE (do NOT fall back). This is the bug.
    raised = False
    err = None
    try:
        mod.resolve_session_choice(NAMES, "st-ac6", True)
    except mod.NoSessionMatchError as exc:
        raised = True
        err = exc
    check(
        "explicit + no match -> raises NoSessionMatchError (no silent fallback)",
        raised,
    )
    check(
        "error carries the requested substring",
        err is not None and err.substring == "st-ac6",
    )
    check(
        "error carries the available names (for a clear message)",
        err is not None and err.available == NAMES,
    )

    # Explicit + no match + EMPTY session set => still raises (never falls back).
    raised_empty = False
    try:
        mod.resolve_session_choice([], "st-ac6", True)
    except mod.NoSessionMatchError:
        raised_empty = True
    check(
        "explicit + no match + empty set -> raises (never falls back)",
        raised_empty,
    )

    # No flag at all (explicit=False) + no match => None == "use current session".
    check(
        "no --session + no match -> None (current-session fallback allowed)",
        mod.resolve_session_choice(NAMES, "st-nomatch", False) is None,
    )
    # No flag at all + a match => still returns the match (opportunistic).
    check(
        "no --session but a match exists -> returns match index",
        mod.resolve_session_choice(["st-worker"], "st-", False) == 0,
    )

    print(f"=== summary: {pass_count} passed, {fail_count} failed ===")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
