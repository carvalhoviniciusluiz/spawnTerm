#!/usr/bin/env python3
"""End-to-end socket test for the #35 mailbox ops.

Proves the mailbox ops are wired into the real server (which registers them by
importing ``mailbox``) and work over the actual unix-socket wire via the
reusable ``BrokerClient``. No sleeps: the server signals readiness the instant
it is accepting connections. Fast and non-flaky."""

import asyncio
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client import BrokerClient  # noqa: E402
from server import BrokerServer  # noqa: E402


class TestMailboxE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sock = os.path.join(self._tmp.name, "broker.sock")
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.server = BrokerServer(self.sock, self.db)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.assertTrue(self.ready.wait(timeout=10), "server did not start")
        self.client = BrokerClient(sock_path=self.sock, timeout=10)

    def tearDown(self):
        self.server.request_stop()
        self.thread.join(timeout=10)
        self._tmp.cleanup()

    def _run(self):
        asyncio.run(self.server.serve(ready=self.ready))

    def test_health_reports_mailbox_ops(self):
        ops = self.client.health()["ops"]
        for op in ("send", "poll", "fetch", "ack"):
            self.assertIn(op, ops)

    def test_send_poll_ack_over_the_wire(self):
        sent = self.client.request(
            {"op": "send", "to": "agent1", "from": "boss", "body": "ship-it"}
        )
        self.assertTrue(sent["ok"])
        mid = sent["id"]
        polled = self.client.request({"op": "poll", "agent": "agent1"})
        self.assertTrue(polled["ok"])
        self.assertEqual([m["id"] for m in polled["messages"]], [mid])
        # Replay before ack.
        replay = self.client.request({"op": "poll", "agent": "agent1"})
        self.assertEqual([m["id"] for m in replay["messages"]], [mid])
        # Ack, then it is gone.
        acked = self.client.request({"op": "ack", "agent": "agent1", "msg_id": mid})
        self.assertTrue(acked["ok"])
        self.assertEqual(acked["acked"], 1)
        gone = self.client.request({"op": "poll", "agent": "agent1"})
        self.assertEqual(gone["count"], 0)

    def test_bad_send_does_not_crash_server(self):
        bad = self.client.request({"op": "send", "to": "a1"})  # missing from/body
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "bad_request")
        # Server survives.
        self.assertTrue(self.client.ping()["ok"])


if __name__ == "__main__":
    unittest.main()
