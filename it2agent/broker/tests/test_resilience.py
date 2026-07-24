#!/usr/bin/env python3
"""Resilience / clean-degradation tests for the broker (#133).

Headless and deterministic — no sleeps, no live vendor calls. Covers the three
failure modes the durable broker must degrade cleanly on:

* a **corrupt/unreadable** sqlite db on startup → clear, actionable error + a
  nonzero exit, no raw traceback, and (crucially) **no silent recreate** — plus
  the deliberate ``--reset`` recovery path;
* a **stale/orphan socket** (leftover file from a dead broker) → reclaimed and
  bind succeeds; a socket a **live** broker owns → clean "already running";
* a **write failure** (simulated with a read-only connection) → the op fails
  cleanly with a structured error, the process stays up, and the db still passes
  its integrity check (no partial write / corruption).
"""

import asyncio
import contextlib
import io
import logging
import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch  # noqa: E402
import schema  # noqa: E402
import server  # noqa: E402
from client import BrokerClient  # noqa: E402
from server import BrokerAlreadyRunningError, BrokerServer  # noqa: E402

_LOG = logging.getLogger("test.broker.resilience")


def _write_garbage_db(path: str) -> bytes:
    """Write bytes that are definitely not a valid sqlite file; return them."""
    blob = b"this is not a sqlite database -- corrupt header\n" * 4
    with open(path, "wb") as fh:
        fh.write(blob)
    return blob


class TestCorruptDatabase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sock = os.path.join(self._tmp.name, "b.sock")
        self.db = os.path.join(self._tmp.name, "broker.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_corrupt_db_raises_clear_error(self):
        _write_garbage_db(self.db)
        with self.assertRaises(schema.CorruptDatabaseError) as ctx:
            schema.init_db(self.db)
        msg = str(ctx.exception)
        self.assertIn("corrupt", msg.lower())
        self.assertIn(self.db, msg)
        self.assertIn("--reset", msg)  # actionable recovery pointer

    def test_run_reports_clean_error_and_nonzero_exit_no_traceback(self):
        blob = _write_garbage_db(self.db)
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                code = server.run(self.sock, self.db, _LOG)
        except Exception as exc:  # pragma: no cover - regression guard
            self.fail(f"run() leaked a raw exception: {exc!r}")
        self.assertEqual(code, 1)
        err = stderr.getvalue()
        self.assertIn("corrupt", err.lower())
        self.assertIn("it2agent-broker", err)
        # No raw traceback surfaced to the operator.
        self.assertNotIn("Traceback", err)

    def test_corrupt_db_is_not_silently_recreated(self):
        blob = _write_garbage_db(self.db)
        with contextlib.redirect_stderr(io.StringIO()):
            server.run(self.sock, self.db, _LOG)
        # The bytes on disk are untouched — no silent recreate / data loss.
        with open(self.db, "rb") as fh:
            self.assertEqual(fh.read(), blob)

    def test_reset_moves_corrupt_aside_and_starts_fresh(self):
        blob = _write_garbage_db(self.db)
        srv = BrokerServer(self.sock, self.db, _LOG, reset=True)
        ready = threading.Event()
        thread = threading.Thread(
            target=lambda: asyncio.run(srv.serve(ready=ready)), daemon=True
        )
        thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "server did not start after --reset")
            # The fresh db is now a valid, healthy sqlite file.
            client = BrokerClient(sock_path=self.sock, timeout=10)
            self.assertTrue(client.ping()["ok"])
        finally:
            srv.request_stop()
            thread.join(timeout=10)
        # The corrupt original was preserved as a backup, not destroyed.
        backups = [
            n for n in os.listdir(self._tmp.name)
            if n.startswith("broker.db.corrupt-")
        ]
        self.assertEqual(len(backups), 1, f"expected one backup, saw {backups}")
        with open(os.path.join(self._tmp.name, backups[0]), "rb") as fh:
            self.assertEqual(fh.read(), blob)


class TestStaleAndLiveSocket(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sock = os.path.join(self._tmp.name, "b.sock")
        self.db = os.path.join(self._tmp.name, "broker.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _make_stale_socket(self) -> None:
        """Leave an orphan socket file with no listener (dead-owner simulation)."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.sock)  # creates the filesystem entry
        s.close()          # stops owning it; the file lingers, nothing listens
        self.assertTrue(os.path.exists(self.sock))

    def test_probe_reports_dead_owner_as_not_alive(self):
        self._make_stale_socket()
        srv = BrokerServer(self.sock, self.db, _LOG)
        self.assertFalse(srv._socket_owner_alive())

    def test_stale_socket_is_reclaimed_and_bind_succeeds(self):
        self._make_stale_socket()
        srv = BrokerServer(self.sock, self.db, _LOG)
        ready = threading.Event()
        thread = threading.Thread(
            target=lambda: asyncio.run(srv.serve(ready=ready)), daemon=True
        )
        thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "stale socket was not reclaimed")
            client = BrokerClient(sock_path=self.sock, timeout=10)
            self.assertTrue(client.ping()["ok"])
        finally:
            srv.request_stop()
            thread.join(timeout=10)

    def test_live_owner_refuses_second_instance(self):
        # First broker: real, listening.
        first = BrokerServer(self.sock, self.db, _LOG)
        ready = threading.Event()
        thread = threading.Thread(
            target=lambda: asyncio.run(first.serve(ready=ready)), daemon=True
        )
        thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "first broker did not start")
            self.assertTrue(first._socket_owner_alive())
            # Second broker on the same socket must refuse cleanly (exit 1),
            # not clobber the live one. run() returns promptly (no blocking).
            db2 = os.path.join(self._tmp.name, "broker2.db")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = server.run(self.sock, db2, _LOG)
            self.assertEqual(code, 1)
            err = stderr.getvalue()
            self.assertIn("already running", err.lower())
            self.assertNotIn("Traceback", err)
            # The live broker is unharmed.
            self.assertTrue(BrokerClient(sock_path=self.sock, timeout=10).ping()["ok"])
        finally:
            first.request_stop()
            thread.join(timeout=10)

    def test_already_running_error_is_raised_directly(self):
        first = BrokerServer(self.sock, self.db, _LOG)
        ready = threading.Event()
        thread = threading.Thread(
            target=lambda: asyncio.run(first.serve(ready=ready)), daemon=True
        )
        thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10))
            second = BrokerServer(self.sock, self.db, _LOG)
            with self.assertRaises(BrokerAlreadyRunningError):
                second._reclaim_socket_or_refuse()
        finally:
            first.request_stop()
            thread.join(timeout=10)


class TestWriteFailureDegradesCleanly(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_readonly_write_fails_cleanly_and_db_stays_intact(self):
        conn = schema.init_db(self.db)
        self.addCleanup(conn.close)
        # Simulate a write failure (disk-full-like): reject every write on this
        # connection. A real disk-full raises the same sqlite3.OperationalError
        # family, which dispatch maps to a `storage` error after rolling back.
        conn.execute("PRAGMA query_only = ON")
        ctx = dispatch.BrokerContext(conn=conn, db_path=self.db, sock_path=None)

        resp = dispatch.handle(
            {"op": "send", "to": "a1", "from": "boss", "body": "hi"}, ctx
        )
        # The op failed cleanly with a structured error — the process did not
        # crash and nothing leaked as a raw exception.
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "storage")

        # Lift the block; the db is still fully consistent (no partial write).
        conn.execute("PRAGMA query_only = OFF")
        self.assertEqual(schema.check_integrity(conn).lower(), "ok")
        # And a subsequent normal write now succeeds (connection not wedged).
        ok_resp = dispatch.handle(
            {"op": "send", "to": "a1", "from": "boss", "body": "later"}, ctx
        )
        self.assertTrue(ok_resp["ok"])


if __name__ == "__main__":
    unittest.main()
