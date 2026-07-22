#!/usr/bin/env python3
"""E2E cross-tab agent shim for the it2agent broker.

Runs as a standalone agent process (typically inside a *real* iTerm2 tab opened
by `it2agent-spawn`). It registers with the durable broker, then polls its
mailbox: every message it receives is appended to a result file (so the
spawning tab can prove cross-tab delivery), acked up-to-cursor, and answered
with a reply back to the sender. This exercises the moat — durable agent-to-agent
messaging across separate tabs/processes with ack — end to end.

Usage:
    e2e_agent_shim.py --sock PATH --result FILE [--me b] [--peer a]
                      [--timeout 60] [--expect ping]

The broker socket path is passed explicitly because a tab opened via osascript
gets a fresh login shell that does NOT inherit the spawner's exported env.
"""
import argparse
import json
import socket
import sys
import time


def rpc(sock_path, obj):
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock_path)
    try:
        s.sendall((json.dumps(obj) + "\n").encode())
        line = s.makefile().readline()
    finally:
        s.close()
    return json.loads(line) if line else {}


def log(result_file, text):
    with open(result_file, "a") as fh:
        fh.write(text + "\n")
        fh.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--me", default="b")
    ap.add_argument("--peer", default="a")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--expect", default="ping")
    args = ap.parse_args()

    # Announce presence in the durable registry (best-effort).
    reg = rpc(args.sock, {
        "op": "register", "session_id": "tab_" + args.me,
        "role": "backend", "task": "e2e-cross-tab", "alive": True,
    })
    log(args.result, "registered ok=%s me=%s" % (reg.get("ok"), args.me))

    deadline = time.time() + args.timeout
    seen_expected = False
    while time.time() < deadline:
        polled = rpc(args.sock, {"op": "poll", "agent": args.me})
        messages = polled.get("messages") or []
        if messages:
            for m in messages:
                body = m.get("body", "")
                sender = m.get("from", args.peer)
                log(args.result, "recv id=%s from=%s body=%r" % (m.get("id"), sender, body))
                rpc(args.sock, {
                    "op": "send", "to": sender, "from": args.me,
                    "body": "pong: " + body,
                })
                if args.expect and args.expect in body:
                    seen_expected = True
            top = max(int(m["id"]) for m in messages)
            acked = rpc(args.sock, {"op": "ack", "agent": args.me, "msg_id": top})
            log(args.result, "acked up_to=%s acked=%s" % (top, acked.get("acked")))
            if seen_expected:
                log(args.result, "done: got expected message, replied, acked")
                return 0
        time.sleep(0.3)

    log(args.result, "timeout: no expected message within %.0fs" % args.timeout)
    return 1


if __name__ == "__main__":
    sys.exit(main())
