#!/usr/bin/env python3
"""End-to-end socket test for the broker (#34).

Spins up a real ``BrokerServer`` on a unix socket in a tmpdir, driven by the
reusable synchronous ``BrokerClient`` over the actual wire. No sleeps: the
server signals a ``threading.Event`` the instant it is accepting connections,
and shutdown is a thread-safe stop + join. Fast and non-flaky."""

import asyncio
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocol  # noqa: E402
import schema  # noqa: E402
from client import BrokerClient  # noqa: E402
from server import BrokerServer  # noqa: E402


class TestServerE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sock = os.path.join(self._tmp.name, "broker.sock")
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.server = BrokerServer(self.sock, self.db)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        # Blocks only until the socket is bound (bounded wait, no polling loop).
        self.assertTrue(self.ready.wait(timeout=10), "server did not start")
        self.client = BrokerClient(sock_path=self.sock, timeout=10)

    def tearDown(self):
        self.server.request_stop()
        self.thread.join(timeout=10)
        self._tmp.cleanup()

    def _run(self):
        asyncio.run(self.server.serve(ready=self.ready))

    def test_ping(self):
        resp = self.client.ping()
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["pong"])

    def test_ping_echo(self):
        resp = self.client.ping(echo="hello")
        self.assertEqual(resp["echo"], "hello")

    def test_health(self):
        resp = self.client.health()
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["schema_version"], schema.SCHEMA_VERSION)
        self.assertEqual(resp["db"], self.db)
        self.assertEqual(resp["sock"], self.sock)

    def test_unknown_op_does_not_crash_server(self):
        bad = self.client.request({"op": "does_not_exist"})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "unknown_op")
        # Server survives: a subsequent good request still works.
        self.assertTrue(self.client.ping()["ok"])

    def test_malformed_line_gets_structured_error(self):
        import socket

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(self.sock)
            f = s.makefile("rwb")
            f.write(b"{not valid json}\n")
            f.flush()
            reply = protocol.decode(f.readline())
            f.close()
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "bad_request")
        # And the server is still alive afterwards.
        self.assertTrue(self.client.ping()["ok"])

    def test_multiple_requests_same_connection(self):
        # The framing supports many request lines over one connection.
        import socket

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(self.sock)
            f = s.makefile("rwb")
            f.write(protocol.encode({"op": "ping", "echo": 1}))
            f.write(protocol.encode({"op": "ping", "echo": 2}))
            f.flush()
            r1 = protocol.decode(f.readline())
            r2 = protocol.decode(f.readline())
            f.close()
        self.assertEqual(r1["echo"], 1)
        self.assertEqual(r2["echo"], 2)


if __name__ == "__main__":
    unittest.main()
