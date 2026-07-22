#!/usr/bin/env python3
"""AC4 driver — handoff/continuity durability across broker restart (#73).

Proves the acceptance criterion for AC4 *without* any iTerm2/tmux: a durable
handoff written by one process survives (a) the writer's death and (b) a full
broker restart on the SAME sqlite db. A fresh reader then resumes from the last
handoff (same id/goal/context/status) and the registry entry is still there.

Flow:
  1. start broker #1 (serve --no-gate) on a temp db + socket A
  2. a short-lived *writer* registers an agent and writes handoff_put, then dies
  3. kill broker #1
  4. start broker #2 on the SAME db but a NEW socket B
  5. a fresh reader does handoff_get + query and asserts equality with step 2

Exit 0 on PASS, 1 on FAIL. All broker processes are torn down on the way out.
No pip deps: raw unix-socket JSON-RPC (mirrors e2e_agent_shim.py).

Usage:
    ac4_handoff_continuity.py [--broker PATH] [--python PATH] [--timeout SECS]
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
DEFAULT_BROKER = HERE.parent / "broker" / "it2agent_broker.py"

AGENT_ID = "ac4-agent"
GOAL = "ship-feature-x"
CONTEXT_PTR = "notes/ac4.md"
VERIFICATION = "pending"


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


def start_broker(python: str, broker: str, db: str, sock: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["IT2AGENT_BROKER_DB"] = db
    env["IT2AGENT_BROKER_SOCK"] = sock
    return subprocess.Popen(
        [python, broker, "serve", "--no-gate"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default=str(DEFAULT_BROKER))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="it2agent-ac4-")
    db = os.path.join(tmp, "broker.db")
    sock_a = os.path.join(tmp, "a.sock")
    sock_b = os.path.join(tmp, "b.sock")

    broker1 = broker2 = None
    failures: list[str] = []
    try:
        # --- 1) broker #1 up ------------------------------------------------
        broker1 = start_broker(args.python, args.broker, db, sock_a)
        if not wait_for_socket(sock_a, time.time() + args.timeout):
            print("FAIL: broker #1 did not come up")
            return 1

        # --- 2) writer registers + writes handoff, then dies ---------------
        reg = rpc(sock_a, {
            "op": "register", "session_id": AGENT_ID,
            "role": "backend", "task": GOAL, "alive": True,
        })
        put = rpc(sock_a, {
            "op": "handoff_put", "agent_id": AGENT_ID, "goal": GOAL,
            "context_ptr": CONTEXT_PTR, "verification_status": VERIFICATION,
        })
        if not (reg.get("ok") and put.get("ok")):
            print("FAIL: register/handoff_put rejected:", reg, put)
            return 1
        written = put["handoff"]
        print("writer: registered + handoff_put id=%s goal=%r" % (written["id"], written["goal"]))

        # --- 3) kill broker #1 (simulate writer + broker death) ------------
        kill(broker1)
        broker1 = None
        print("killed broker #1 (writer process gone, socket A dead)")

        # --- 4) restart broker #2 on the SAME db, NEW socket ---------------
        broker2 = start_broker(args.python, args.broker, db, sock_b)
        if not wait_for_socket(sock_b, time.time() + args.timeout):
            print("FAIL: broker #2 did not come up on the same db")
            return 1
        print("restarted broker #2 on the SAME db (new socket B)")

        # --- 5) fresh reader resumes ---------------------------------------
        got = rpc(sock_b, {"op": "handoff_get", "agent_id": AGENT_ID, "goal": GOAL})
        q = rpc(sock_b, {"op": "query", "role": "backend"})
        h = got.get("handoff") or {}
        print("reader: handoff_get ->", json.dumps(h, sort_keys=True))
        print("reader: query role=backend ->", json.dumps(q, sort_keys=True))

        if h.get("id") != written["id"]:
            failures.append("handoff id changed across restart (%s != %s)" % (h.get("id"), written["id"]))
        if h.get("goal") != GOAL:
            failures.append("goal not preserved: %r" % h.get("goal"))
        if h.get("context_ptr") != CONTEXT_PTR:
            failures.append("context_ptr not preserved: %r" % h.get("context_ptr"))
        if h.get("verification_status") != VERIFICATION:
            failures.append("verification_status not preserved: %r" % h.get("verification_status"))
        agents = q.get("agents") or []
        if not any(a.get("session_id") == AGENT_ID for a in agents):
            failures.append("registry entry did not survive restart")
    finally:
        for p in (broker1, broker2):
            if p is not None:
                kill(p)

    if failures:
        print("AC4 FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("AC4 PASS: handoff + registry survived process death AND broker restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
