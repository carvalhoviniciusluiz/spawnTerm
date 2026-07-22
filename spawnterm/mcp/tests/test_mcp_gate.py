#!/usr/bin/env python3
"""Tests for the MCP server's feature-flag gate + purity (#18).

Mirrors the daemon/broker gate tests: the ``spawnterm.mcp`` flag defaults OFF,
``--no-gate`` / ``SPAWNTERM_FORCE=1`` bypass it, and when OFF the server refuses
to start (prints a message, exits 0) without ever importing iterm2 or opening a
socket. Also proves the pure modules import iterm2-free.
"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPurity(unittest.TestCase):
    def test_pure_modules_import_without_iterm2(self):
        import rpc  # noqa: F401
        import spawnterm_mcp  # noqa: F401
        import tools  # noqa: F401

        self.assertNotIn("iterm2", sys.modules)


class TestFlagGate(unittest.TestCase):
    def setUp(self):
        import spawnterm_mcp

        self.mcp = spawnterm_mcp
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = os.path.join(self._tmp.name, "config.toml")
        self._old = os.environ.get("SPAWNTERM_CONFIG")
        os.environ["SPAWNTERM_CONFIG"] = self._cfg
        self._old_force = os.environ.pop("SPAWNTERM_FORCE", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SPAWNTERM_CONFIG", None)
        else:
            os.environ["SPAWNTERM_CONFIG"] = self._old
        if self._old_force is not None:
            os.environ["SPAWNTERM_FORCE"] = self._old_force
        self._tmp.cleanup()

    def _write_flag(self, value: str) -> None:
        with open(self._cfg, "w", encoding="utf-8") as handle:
            handle.write('[features]\n"spawnterm.mcp" = %s\n' % value)

    def test_off_when_no_config(self):
        self.assertFalse(self.mcp.flag_enabled())
        self.assertFalse(self.mcp.gate_open(no_gate=False))

    def test_off_when_flag_false(self):
        self._write_flag("false")
        self.assertFalse(self.mcp.flag_enabled())

    def test_on_when_flag_true(self):
        self._write_flag("true")
        self.assertTrue(self.mcp.flag_enabled())
        self.assertTrue(self.mcp.gate_open(no_gate=False))

    def test_no_gate_bypasses(self):
        self.assertTrue(self.mcp.gate_open(no_gate=True))

    def test_force_env_bypasses(self):
        os.environ["SPAWNTERM_FORCE"] = "1"
        try:
            self.assertTrue(self.mcp.gate_open(no_gate=False))
        finally:
            os.environ.pop("SPAWNTERM_FORCE", None)

    def test_main_refuses_to_start_when_gated_off(self):
        # Flag OFF -> print + exit 0, never touch iterm2 or a socket.
        stderr = io.StringIO()
        old = sys.stderr
        sys.stderr = stderr
        try:
            rc = self.mcp.main([])
        finally:
            sys.stderr = old
        self.assertEqual(rc, 0)
        self.assertIn("refusing to start", stderr.getvalue())
        self.assertNotIn("iterm2", sys.modules)


class TestStdioLoop(unittest.TestCase):
    """The stdio loop is thin, but verify it frames responses correctly with a
    fake broker + in-memory stdin/stdout (no real sockets)."""

    def _deps(self):
        from tools import Deps

        class Broker:
            def request(self, op):
                return {"ok": True, "tools": [], "count": 0, **({"echo": op}) }

        return Deps(broker=Broker(), spawn=lambda a, c, p: {"launched": True})

    def test_serve_stdio_frames_one_response_per_request(self):
        import json as _json
        import logging

        import spawnterm_mcp

        requests = [
            _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            _json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            _json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
        stdin = io.StringIO("\n".join(requests) + "\n")
        stdout = io.StringIO()
        logger = logging.getLogger("test.mcp")
        logger.addHandler(logging.NullHandler())
        rc = spawnterm_mcp.serve_stdio(self._deps(), logger, stdin=stdin, stdout=stdout)
        self.assertEqual(rc, 0)
        lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
        # initialize + tools/list reply; the notification produced no line.
        self.assertEqual(len(lines), 2)
        self.assertEqual(_json.loads(lines[0])["id"], 1)
        self.assertEqual(_json.loads(lines[1])["id"], 2)
        self.assertEqual(len(_json.loads(lines[1])["result"]["tools"]), 6)


if __name__ == "__main__":
    unittest.main()
