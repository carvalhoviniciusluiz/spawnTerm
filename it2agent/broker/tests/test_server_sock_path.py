#!/usr/bin/env python3
"""Startup precheck for over-long unix socket paths (#76).

A unix socket path that overflows the platform's ``sockaddr_un.sun_path``
buffer used to blow up at ``bind()`` with a raw ``OSError: AF_UNIX path too
long`` traceback. The broker now prechecks the path length before binding and
fails with a clear, actionable message + nonzero exit. These tests pin that
behavior (long path → clean error, no raw OSError; short path → still works)
without opening a real socket for the failure case."""

import asyncio
import contextlib
import io
import logging
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from server import (  # noqa: E402
    BrokerServer,
    SockPathTooLongError,
    _check_sock_path_length,
    _SUN_PATH_MAX,
)


def _too_long_path(tmpdir: str) -> str:
    """A socket path guaranteed to exceed the platform sun_path limit."""
    return os.path.join(tmpdir, "x" * (_SUN_PATH_MAX + 20) + ".sock")


class TestSockPathLengthCheck(unittest.TestCase):
    def test_too_long_path_raises_clear_error(self):
        path = "/tmp/" + "a" * (_SUN_PATH_MAX + 10) + ".sock"
        with self.assertRaises(SockPathTooLongError) as ctx:
            _check_sock_path_length(server.Path(path))
        msg = str(ctx.exception)
        # Actionable: states the actual length, the platform limit, and a fix.
        self.assertIn(str(len(os.fsencode(path))), msg)
        self.assertIn(str(_SUN_PATH_MAX), msg)
        self.assertIn("too long", msg)
        self.assertIn("/tmp", msg)

    def test_short_path_passes_check(self):
        # A normal short path must not raise.
        _check_sock_path_length(server.Path("/tmp/it2a.sock"))

    def test_boundary_at_limit_is_rejected(self):
        # A path exactly at the buffer size leaves no room for the NUL.
        path = "/" + "a" * (_SUN_PATH_MAX - 1)
        self.assertEqual(len(os.fsencode(path)), _SUN_PATH_MAX)
        with self.assertRaises(SockPathTooLongError):
            _check_sock_path_length(server.Path(path))


class TestRunTooLongPath(unittest.TestCase):
    def test_run_reports_clean_error_and_nonzero_exit(self):
        logger = logging.getLogger("test.broker.sockpath")
        with tempfile.TemporaryDirectory() as tmp:
            sock = _too_long_path(tmp)
            db = os.path.join(tmp, "broker.db")
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    # Must NOT surface a raw OSError traceback.
                    code = server.run(sock, db, logger)
            except OSError as exc:  # pragma: no cover - regression guard
                self.fail(f"run() leaked a raw OSError: {exc!r}")
            self.assertEqual(code, 1)
            err = stderr.getvalue()
            self.assertIn("too long", err)
            self.assertIn(str(_SUN_PATH_MAX), err)
            self.assertIn("it2agent-broker", err)


class TestRunShortPathStillServes(unittest.TestCase):
    def test_short_path_binds_and_shuts_down_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Keep the tmpdir short; the socket path is well within the limit.
            sock = os.path.join(tmp, "b.sock")
            db = os.path.join(tmp, "b.db")
            srv = BrokerServer(sock, db)
            ready = threading.Event()

            def _run():
                asyncio.run(srv.serve(ready=ready))

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(timeout=10), "server did not start")
            self.assertTrue(os.path.exists(sock))
            srv.request_stop()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
