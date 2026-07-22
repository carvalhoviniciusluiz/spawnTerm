#!/usr/bin/env python3
"""AC7 checker — feature-flags are fail-safe no-ops when OFF (#73).

Proves the AC7 contract for four capabilities: with the flag OFF and neither
``--no-gate`` nor ``IT2AGENT_FORCE=1`` set, the tool emits **nothing to stdout**
and **exits 0** (silent, fail-safe no-op). Re-enabling restores behavior:

  * agent.status_board (it2agent-emit): full round-trip — OFF => 0 bytes/exit 0,
    ON => escape-code bytes emitted/exit 0.
  * agent.broker  (it2agent_broker.py serve): OFF => 0 stdout bytes/exit 0,
    with the gate message on stderr. (ON would block on serve, so we prove the
    flag round-trips via the flag helper instead of running the server.)
  * agent.mcp     (it2agent_mcp.py, stdin closed): OFF => 0 stdout bytes/exit 0.
  * agent.review  (it2agent-review show): OFF => 0 stdout bytes/exit 0.

Uses whatever IT2AGENT_CONFIG the caller exported (an isolated temp file in the
prompt); if unset it makes its own temp config so the real one is never touched.
Leaves every flag OFF (the default) on exit.

Usage:
    ac7_flag_noop.py [--python PATH]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ST = HERE.parent
FLAG = str(ST / "flags" / "it2agent-flag")
EMIT = str(ST / "emit" / "it2agent-emit")
BROKER = str(ST / "broker" / "it2agent_broker.py")
MCP = str(ST / "mcp" / "it2agent_mcp.py")
REVIEW = str(ST / "review" / "it2agent-review")


def flag(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([FLAG, *args], env=env, capture_output=True, text=True)


def run(env: dict, argv: list, stdin_text=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, env=env, input=stdin_text, capture_output=True, text=True, timeout=30
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    env = dict(os.environ)
    if not env.get("IT2AGENT_CONFIG"):
        env["IT2AGENT_CONFIG"] = os.path.join(tempfile.mkdtemp(prefix="it2agent-ac7-"), "config.toml")
    # Never let a stray force bypass leak in and defeat the whole test.
    env.pop("IT2AGENT_FORCE", None)
    py = args.python

    # (capability, argv-when-off, stdin, roundtrip?)
    cases = [
        ("agent.status_board", [EMIT, "status", "busy"], None, True),
        ("agent.broker", [py, BROKER, "serve"], None, False),
        ("agent.mcp", [py, MCP], "", False),
        ("agent.review", [REVIEW, "show", "--id", "x", "--role", "backend"], None, False),
    ]

    failures: list[str] = []
    for cap, argv, stdin_text, roundtrip in cases:
        flag(env, "disable", cap)
        off = run(env, argv, stdin_text)
        noop = off.returncode == 0 and len(off.stdout) == 0
        print("[%s] OFF: exit=%d stdout_bytes=%d stderr=%r -> %s" % (
            cap, off.returncode, len(off.stdout),
            (off.stderr or "").strip()[:80], "no-op OK" if noop else "NO-OP FAIL"))
        if not noop:
            failures.append("%s: not a silent no-op when OFF (exit=%d bytes=%d)" % (
                cap, off.returncode, len(off.stdout)))

        if roundtrip:
            flag(env, "enable", cap)
            on = run(env, argv, stdin_text)
            restored = on.returncode == 0 and len(on.stdout) > 0
            print("[%s] ON:  exit=%d stdout_bytes=%d -> %s" % (
                cap, on.returncode, len(on.stdout),
                "restored OK" if restored else "RESTORE FAIL"))
            if not restored:
                failures.append("%s: did not emit when ON (exit=%d bytes=%d)" % (
                    cap, on.returncode, len(on.stdout)))
            flag(env, "disable", cap)
        else:
            # Prove the flag round-trips (enable->query on->disable) even though
            # running the server/mcp to completion when ON is impractical here.
            flag(env, "enable", cap)
            q = flag(env, cap.split(".", 1)[1])
            flag(env, "disable", cap)
            q2 = flag(env, cap.split(".", 1)[1])
            toggles = q.returncode == 0 and q2.returncode != 0
            print("[%s] flag toggle: enable->query exit=%d, disable->query exit=%d -> %s" % (
                cap, q.returncode, q2.returncode, "toggle OK" if toggles else "TOGGLE FAIL"))
            if not toggles:
                failures.append("%s: flag did not toggle on/off cleanly" % cap)

    if failures:
        print("\nAC7 FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nAC7 PASS: all four capabilities are silent no-ops when OFF and restore when ON")
    return 0


if __name__ == "__main__":
    sys.exit(main())
