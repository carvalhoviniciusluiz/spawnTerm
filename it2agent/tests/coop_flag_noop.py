#!/usr/bin/env python3
"""Cooperation AC — every NEW capability flag is inert when OFF (#88/#92/#110/#112).

Extends ac7_flag_noop.py to the five flags added by the cooperation/repositioning
work. Default-OFF is the whole safety story: with a flag OFF the capability emits
NO bytes, exports NO vars, and writes NOTHING durable; turning it ON restores it.

  * agent.native_status  (it2agent-emit ccstatus): OFF => 0 stdout bytes/exit 0;
    ON => OSC 21337 bytes emitted. (round-trip)
  * agent.canonical_port (worktree create --dry-run): OFF => no canonical_port_*
    line; ON => canonical_port_web=... printed. (worktree_isolation kept ON so we
    isolate the canonical flag, not the create gate.)
  * agent.isolate_docker (worktree create --dry-run --isolate docker): OFF => no
    env_COMPOSE_PROJECT_NAME; ON => it is exported.
  * agent.isolate_db     (worktree create --dry-run --isolate db): OFF => no
    env_IT2AGENT_DB_SCHEMA; ON => it is exported.
  * agent.team_bridge    (it2agent-team-hook event, INVERTED gate): the event path
    ALWAYS exits 0 with no stdout. "OFF" here means an EXPLICIT `= false`
    kill-switch => NO durable broker write; absent/true => the write lands. Proven
    against a live broker by checking the handoff history after the event.

Uses an isolated IT2AGENT_CONFIG (never the operator's real flags) and leaves
every flag OFF on exit.

Exit 0 on PASS, 1 on FAIL. No pip deps.

Usage:
    coop_flag_noop.py [--python PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ST = HERE.parent
FLAG = str(ST / "flags" / "it2agent-flag")
EMIT = str(ST / "emit" / "it2agent-emit")
WORKTREE = str(ST / "spawn" / "it2agent-worktree")
BROKER = str(ST / "broker" / "it2agent_broker.py")
TEAM_HOOK = str(ST / "team" / "it2agent_team_hook.py")
REPO = str(ST.parent)  # the git root that contains it2agent/


def run(env: dict, argv: list, stdin_text=None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, input=stdin_text, capture_output=True, text=True, timeout=30)


def flag(env: dict, *args: str) -> subprocess.CompletedProcess:
    return run(env, [FLAG, *args])


def create_dry(env: dict, *extra: str) -> subprocess.CompletedProcess:
    return run(env, [WORKTREE, "create", "--repo", REPO, "--id", "coop-flag",
                     "--role", "backend", "--dry-run", *extra])


def rpc(sock: str, obj: dict, timeout: float = 5.0) -> dict:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock)
    try:
        s.sendall((json.dumps(obj) + "\n").encode())
        line = s.makefile().readline()
    finally:
        s.close()
    return json.loads(line) if line else {}


def check_off_on(tag: str, off: subprocess.CompletedProcess, on: subprocess.CompletedProcess,
                 needle: str, failures: list) -> None:
    """OFF must not contain needle (and exit 0); ON must contain it (and exit 0)."""
    off_inert = off.returncode == 0 and needle not in off.stdout
    on_active = on.returncode == 0 and needle in on.stdout
    print("[%s] OFF exit=%d has(%r)=%s -> %s | ON exit=%d has(%r)=%s -> %s" % (
        tag, off.returncode, needle, needle in off.stdout,
        "inert OK" if off_inert else "LEAK",
        on.returncode, needle, needle in on.stdout,
        "active OK" if on_active else "DEAD"))
    if not off_inert:
        failures.append("%s: not inert when OFF" % tag)
    if not on_active:
        failures.append("%s: did not activate when ON" % tag)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    py = args.python

    env = dict(os.environ)
    env["IT2AGENT_CONFIG"] = os.path.join(
        tempfile.mkdtemp(prefix="it2agent-coop-flag-"), "config.toml")
    env.pop("IT2AGENT_FORCE", None)

    failures: list[str] = []

    # 1) native_status: ccstatus emits OSC 21337 only when ON.
    flag(env, "disable", "agent.native_status")
    ns_off = run(env, [EMIT, "ccstatus", "busy", "--detail", "x"])
    flag(env, "enable", "agent.native_status")
    ns_on = run(env, [EMIT, "ccstatus", "busy", "--detail", "x"])
    off_ok = ns_off.returncode == 0 and len(ns_off.stdout) == 0
    on_ok = ns_on.returncode == 0 and "21337" in ns_on.stdout
    print("[agent.native_status] OFF exit=%d bytes=%d -> %s | ON exit=%d bytes=%d has21337=%s -> %s" % (
        ns_off.returncode, len(ns_off.stdout), "inert OK" if off_ok else "LEAK",
        ns_on.returncode, len(ns_on.stdout), "21337" in ns_on.stdout, "active OK" if on_ok else "DEAD"))
    if not off_ok:
        failures.append("agent.native_status: emitted bytes when OFF")
    if not on_ok:
        failures.append("agent.native_status: no OSC 21337 when ON")
    flag(env, "disable", "agent.native_status")

    # worktree create --dry-run gates on worktree_isolation; keep it ON so we are
    # isolating the canonical/isolate flags themselves.
    flag(env, "enable", "agent.worktree_isolation")

    # 2) canonical_port: canonical_port_* only printed when ON.
    flag(env, "disable", "agent.canonical_port")
    c_off = create_dry(env)
    flag(env, "enable", "agent.canonical_port")
    c_on = create_dry(env)
    check_off_on("agent.canonical_port", c_off, c_on, "canonical_port_", failures)
    flag(env, "disable", "agent.canonical_port")

    # 3) isolate_docker: COMPOSE_PROJECT_NAME only exported when ON.
    flag(env, "disable", "agent.isolate_docker")
    d_off = create_dry(env, "--isolate", "docker")
    flag(env, "enable", "agent.isolate_docker")
    d_on = create_dry(env, "--isolate", "docker")
    check_off_on("agent.isolate_docker", d_off, d_on, "env_COMPOSE_PROJECT_NAME=", failures)
    flag(env, "disable", "agent.isolate_docker")

    # 4) isolate_db: IT2AGENT_DB_SCHEMA only exported when ON.
    flag(env, "disable", "agent.isolate_db")
    b_off = create_dry(env, "--isolate", "db")
    flag(env, "enable", "agent.isolate_db")
    b_on = create_dry(env, "--isolate", "db")
    check_off_on("agent.isolate_db", b_off, b_on, "env_IT2AGENT_DB_SCHEMA=", failures)
    flag(env, "disable", "agent.isolate_db")

    flag(env, "disable", "agent.worktree_isolation")

    # 5) team_bridge (INVERTED kill-switch): always exit 0 / no stdout, but an
    # EXPLICIT false suppresses the durable broker write; absent lets it through.
    tmp = tempfile.mkdtemp(prefix="it2agent-coop-flag-broker-")
    db = os.path.join(tmp, "b.db")
    sock = os.path.join(tmp, "b.sock")
    benv = dict(env)
    benv["IT2AGENT_BROKER_DB"] = db
    benv["IT2AGENT_BROKER_SOCK"] = sock
    benv.pop("IT2AGENT_FORCE", None)
    broker = subprocess.Popen([py, BROKER, "serve", "--no-gate"], env=benv,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if os.path.exists(sock):
                try:
                    rpc(sock, {"op": "health"}); break
                except OSError:
                    pass
            time.sleep(0.1)

        def fire_created(session: str) -> subprocess.CompletedProcess:
            # NOTE: no --no-gate / no IT2AGENT_FORCE, so the config gate decides.
            payload = json.dumps({"session_id": session, "task": {"id": "TX", "title": "t"}})
            return run(benv, [py, TEAM_HOOK, "TaskCreated"], stdin_text=payload)

        # explicit false => kill-switched => no write.
        flag(benv, "disable", "agent.team_bridge")  # writes `= false`
        off = fire_created("kill-sess-000000")
        off_hist = rpc(sock, {"op": "handoff_history", "agent_id": "team:session-kill-ses"})
        off_writes = len(off_hist.get("handoffs") or [])
        off_ok = off.returncode == 0 and off.stdout == "" and off_writes == 0
        print("[agent.team_bridge] EXPLICIT-false: exit=%d stdout=%d broker_writes=%d -> %s" % (
            off.returncode, len(off.stdout), off_writes, "inert OK" if off_ok else "LEAK"))
        if not off_ok:
            failures.append("agent.team_bridge: wrote/emitted when kill-switched OFF")

        # remove the key entirely (absent) => runs => write lands, still silent.
        flag(benv, "enable", "agent.team_bridge")  # not-false => runs
        on = fire_created("run-sess-000000")
        on_hist = rpc(sock, {"op": "handoff_history", "agent_id": "team:session-run-sess"})
        on_writes = len(on_hist.get("handoffs") or [])
        on_ok = on.returncode == 0 and on.stdout == "" and on_writes >= 1
        print("[agent.team_bridge] not-false:      exit=%d stdout=%d broker_writes=%d -> %s" % (
            on.returncode, len(on.stdout), on_writes, "active OK" if on_ok else "DEAD"))
        if not on_ok:
            failures.append("agent.team_bridge: did not write when enabled (not-false)")
        flag(benv, "disable", "agent.team_bridge")
    finally:
        if broker.poll() is None:
            broker.terminate()
            try:
                broker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                broker.kill()

    if failures:
        print("\nCOOP-FLAG FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nCOOP-FLAG PASS: native_status/canonical_port/isolate_docker/isolate_db "
          "are inert when OFF and restore when ON; team_bridge always exits 0 with "
          "no stdout and only writes when not kill-switched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
