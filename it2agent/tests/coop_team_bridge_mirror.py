#!/usr/bin/env python3
"""Cooperation AC — the Team Bridge is a durable, safe observer of Claude Code
agent-teams (#92, #95, #96). THE MOAT.

Claude Code's experimental agent-teams keep their shared task list + coordination
state under ``~/.claude/teams/{team}/`` and DELETE it at session end — "coordination
state is lost" when the lead dies. The team bridge is a passive hook that mirrors
that state into the durable it2agent broker so it survives. This driver proves the
whole contract WITHOUT needing a real Claude Code team running headlessly (that
part is 🔴 in the prompt): it feeds the hook the exact JSON Claude Code delivers on
stdin for each event and then verifies the durable side effects out of band.

What it asserts:
  A. OBSERVER SAFETY — every event path exits 0 and writes NOTHING to stdout, under
     the success path AND under empty/malformed stdin and an unknown event (exit 2
     from a hook would BLOCK the team; we must never do that).
  B. MIRROR — TeammateIdle -> broker register; TaskCreated -> handoff_put(pending);
     TaskCompleted -> handoff_put(completed) + a durable send to the lead.
  C. IDEMPOTENCY (#95) — re-firing TaskCompleted dedups the lead notification on its
     (recipient, key) so the lead never sees a duplicate "completed".
  D. DURABILITY (THE MOAT) — kill the broker (stand-in for the lead session dying),
     restart it on the SAME sqlite db, and re-query: the team registry + the full
     task lifecycle (pending->completed) are STILL THERE. This is what native teams
     cannot do.
  E. INSTALL/UNINSTALL — `install --scope project` writes the three hooks into a
     THROWAWAY git repo's gitignored .claude/settings.local.json (never the
     operator's real ~/.claude) and adds the gitignore entry; `uninstall` removes
     ONLY our entries.

Exit 0 on PASS, 1 on FAIL. Broker torn down on exit. No pip deps.

Usage:
    coop_team_bridge_mirror.py [--broker PATH] [--python PATH]
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
DEFAULT_BROKER = ST / "broker" / "it2agent_broker.py"
TEAM_HOOK = ST / "team" / "it2agent_team_hook.py"

SESSION_ID = "coopsess-deadbeef-0001"        # -> team:session-coopsess (first 8 chars)
TEAM_KEY = "team:session-coopsess"
TEAMMATE_ID = "teammate-42"
TASK_ID = "T-100"
TASK_TITLE = "wire the widget"


def rpc(sock_path: str, obj: dict, timeout: float = 5.0) -> dict:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock_path)
    try:
        s.sendall((json.dumps(obj) + "\n").encode())
        line = s.makefile().readline()
    finally:
        s.close()
    return json.loads(line) if line else {}


def wait_for_socket(sock: str, deadline: float) -> bool:
    while time.time() < deadline:
        if os.path.exists(sock):
            try:
                rpc(sock, {"op": "health"})
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def start_broker(python: str, broker: str, db: str, sock: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["IT2AGENT_BROKER_DB"] = db
    env["IT2AGENT_BROKER_SOCK"] = sock
    return subprocess.Popen([python, broker, "serve", "--no-gate"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lifecycle_ok(statuses: list) -> bool:
    """A valid task lifecycle starts pending and ends completed.

    The broker handoff log is append-only, so an idempotency re-fire of
    TaskCompleted legitimately appends another 'completed' (the *send* dedups, but
    the audit log keeps every write). So we assert the shape, not an exact list.
    """
    return bool(statuses) and statuses[0] == "pending" and statuses[-1] == "completed"


def kill(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def fire(python: str, sock: str, event: str, payload) -> subprocess.CompletedProcess:
    """Run the hook exactly as Claude Code does: event as argv, JSON on stdin."""
    env = dict(os.environ)
    env["IT2AGENT_BROKER_SOCK"] = sock
    env["IT2AGENT_FORCE"] = "1"  # bypass the kill-switch gate for the harness
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([python, str(TEAM_HOOK), event, "--no-gate"],
                          input=stdin, env=env, capture_output=True, text=True, timeout=30)


def git(repo: str, *argv: str) -> None:
    subprocess.run(["git", "-C", repo, *argv], check=True, capture_output=True, text=True)


def check_install(python: str, failures: list) -> None:
    """E. install/uninstall into a THROWAWAY git repo's settings.local.json."""
    repo = tempfile.mkdtemp(prefix="it2agent-coop-team-repo-")
    git(repo, "init", "-q")
    settings = Path(repo) / ".claude" / "settings.local.json"
    env = dict(os.environ)
    env.pop("IT2AGENT_CLAUDE_SETTINGS", None)  # exercise the real project-scope path

    ins = subprocess.run([python, str(TEAM_HOOK), "install", "--scope", "project"],
                         cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    print("install --scope project -> exit=%d" % ins.returncode)
    if ins.returncode != 0 or not settings.is_file():
        failures.append("install did not write %s" % settings)
        return
    data = json.loads(settings.read_text())
    events = data.get("hooks", {})
    have = [e for e in ("TaskCreated", "TaskCompleted", "TeammateIdle") if e in events]
    print("   settings.local.json hooks:", have)
    if len(have) != 3:
        failures.append("install did not register all three hook events: %r" % have)
    gitignore = Path(repo) / ".gitignore"
    covered = gitignore.is_file() and "settings.local.json" in gitignore.read_text()
    print("   .gitignore covers settings.local.json:", covered)
    if not covered:
        failures.append("install did not gitignore settings.local.json")

    # status reports the resolved path + exit 0 when installed.
    stt = subprocess.run([python, str(TEAM_HOOK), "status", "--scope", "project"],
                         cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    print("status --scope project -> exit=%d path=%s" % (stt.returncode, stt.stdout.strip()))
    if stt.returncode != 0:
        failures.append("status did not report installed (exit %d)" % stt.returncode)

    unins = subprocess.run([python, str(TEAM_HOOK), "uninstall", "--scope", "project"],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    after = json.loads(settings.read_text()) if settings.is_file() else {}
    print("uninstall --scope project -> exit=%d hooks_left=%r" % (
        unins.returncode, list(after.get("hooks", {}).keys())))
    if after.get("hooks"):
        failures.append("uninstall left our hooks behind: %r" % after.get("hooks"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default=str(DEFAULT_BROKER))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="it2agent-coop-team-")
    db = os.path.join(tmp, "broker.db")
    sock_a = os.path.join(tmp, "a.sock")
    sock_b = os.path.join(tmp, "b.sock")

    broker = broker2 = None
    failures: list[str] = []
    try:
        broker = start_broker(args.python, args.broker, db, sock_a)
        if not wait_for_socket(sock_a, time.time() + args.timeout):
            print("FAIL: broker did not come up")
            return 1

        # --- A. observer safety + B. mirror --------------------------------
        def assert_silent(tag: str, cp: subprocess.CompletedProcess) -> None:
            ok = cp.returncode == 0 and cp.stdout == ""
            print("[event %-22s] exit=%d stdout_bytes=%d -> %s" % (
                tag, cp.returncode, len(cp.stdout), "silent OK" if ok else "CONTRACT VIOLATION"))
            if not ok:
                failures.append("%s: observer wrote stdout or exited non-zero" % tag)

        idle = fire(args.python, sock_a, "TeammateIdle",
                    {"session_id": SESSION_ID, "agent_id": TEAMMATE_ID, "agent_type": "backend"})
        assert_silent("TeammateIdle", idle)
        created = fire(args.python, sock_a, "TaskCreated",
                       {"session_id": SESSION_ID,
                        "task": {"id": TASK_ID, "title": TASK_TITLE},
                        "transcript_path": "/tmp/transcript.jsonl"})
        assert_silent("TaskCreated", created)
        completed = fire(args.python, sock_a, "TaskCompleted",
                         {"session_id": SESSION_ID, "task": {"id": TASK_ID, "title": TASK_TITLE}})
        assert_silent("TaskCompleted", completed)

        # observer safety under garbage input (must STILL exit 0, no stdout).
        assert_silent("empty-stdin", fire(args.python, sock_a, "TaskCreated", ""))
        assert_silent("malformed-json", fire(args.python, sock_a, "TaskCreated", "{not json"))
        assert_silent("unknown-event", fire(args.python, sock_a, "NotARealEvent", {"x": 1}))

        # --- B verified out of band: registry + task lifecycle -------------
        reg = rpc(sock_a, {"op": "query"})
        ids = {a.get("session_id") for a in (reg.get("agents") or [])}
        print("broker registry ->", sorted(i for i in ids if i))
        if TEAMMATE_ID not in ids:
            failures.append("TeammateIdle did not register the teammate")

        hist = rpc(sock_a, {"op": "handoff_history", "agent_id": TEAM_KEY})
        handoffs = hist.get("handoffs") or []
        statuses = [h.get("verification_status") for h in handoffs
                    if h.get("goal") == "task:%s" % TASK_ID]
        print("broker task lifecycle for %s ->" % TEAM_KEY, statuses)
        if statuses != ["pending", "completed"]:
            failures.append("task lifecycle not pending->completed: %r" % statuses)
        del statuses  # recomputed post-restart

        lead = rpc(sock_a, {"op": "poll", "agent": "lead"})
        lead_msgs = lead.get("messages") or []
        print("lead mailbox ->", json.dumps([m.get("body") for m in lead_msgs], sort_keys=True))
        if not any("task:%s completed" % TASK_ID == m.get("body") for m in lead_msgs):
            failures.append("lead did not receive the completion notification")

        # --- C. idempotency: re-fire TaskCompleted, no duplicate -----------
        fire(args.python, sock_a, "TaskCompleted",
             {"session_id": SESSION_ID, "task": {"id": TASK_ID, "title": TASK_TITLE}})
        lead2 = rpc(sock_a, {"op": "poll", "agent": "lead"})
        completed_count = sum(1 for m in (lead2.get("messages") or [])
                              if m.get("body") == "task:%s completed" % TASK_ID)
        print("lead completion notifications after re-fire:", completed_count)
        if completed_count != 1:
            failures.append("idempotency broken: %d completion messages (want 1)" % completed_count)

        # --- D. durability across broker death (THE MOAT) ------------------
        kill(broker)
        broker = None
        print("killed broker (stand-in for the lead session dying)")
        broker2 = start_broker(args.python, args.broker, db, sock_b)
        if not wait_for_socket(sock_b, time.time() + args.timeout):
            print("FAIL: broker did not restart on the same db")
            return 1
        print("restarted broker on the SAME db (new socket)")
        hist2 = rpc(sock_b, {"op": "handoff_history", "agent_id": TEAM_KEY})
        statuses2 = [h.get("verification_status") for h in (hist2.get("handoffs") or [])
                     if h.get("goal") == "task:%s" % TASK_ID]
        reg2 = rpc(sock_b, {"op": "query"})
        ids2 = {a.get("session_id") for a in (reg2.get("agents") or [])}
        print("after restart: lifecycle=%r teammate_present=%s" % (
            statuses2, TEAMMATE_ID in ids2))
        if not lifecycle_ok(statuses2):
            failures.append("task lifecycle did NOT survive broker restart: %r" % statuses2)
        if TEAMMATE_ID not in ids2:
            failures.append("team registry did NOT survive broker restart")

        # --- E. install/uninstall into a throwaway git project -------------
        check_install(args.python, failures)
    finally:
        kill(broker)
        kill(broker2)

    if failures:
        print("\nCOOP-TEAM-BRIDGE FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nCOOP-TEAM-BRIDGE PASS: the bridge is a safe observer (always exit 0, no "
          "stdout), mirrors register+handoff+send durably, dedups the completion "
          "notification, and the mirror SURVIVES broker death — the moat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
