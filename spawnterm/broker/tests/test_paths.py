#!/usr/bin/env python3
"""Tests for broker path resolution (#34): overrides + XDG + fallbacks."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths  # noqa: E402

_VARS = ("SPAWNTERM_BROKER_DB", "SPAWNTERM_BROKER_SOCK", "XDG_STATE_HOME", "XDG_RUNTIME_DIR")


class TestPaths(unittest.TestCase):
    def setUp(self):
        self._saved = {v: os.environ.pop(v, None) for v in _VARS}

    def tearDown(self):
        for v, val in self._saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val

    def test_db_explicit_override_wins(self):
        os.environ["SPAWNTERM_BROKER_DB"] = "/tmp/x/custom.db"
        os.environ["XDG_STATE_HOME"] = "/tmp/state"
        self.assertEqual(paths.broker_db_path(), Path("/tmp/x/custom.db"))

    def test_db_uses_xdg_state_home(self):
        os.environ["XDG_STATE_HOME"] = "/tmp/state"
        self.assertEqual(paths.broker_db_path(), Path("/tmp/state/spawnterm/broker.db"))

    def test_db_fallback_to_local_state(self):
        self.assertEqual(
            paths.broker_db_path(), Path.home() / ".local/state/spawnterm/broker.db"
        )

    def test_sock_explicit_override_wins(self):
        os.environ["SPAWNTERM_BROKER_SOCK"] = "/tmp/x/custom.sock"
        os.environ["XDG_RUNTIME_DIR"] = "/tmp/run"
        self.assertEqual(paths.broker_sock_path(), Path("/tmp/x/custom.sock"))

    def test_sock_uses_xdg_runtime_dir(self):
        os.environ["XDG_RUNTIME_DIR"] = "/tmp/run"
        self.assertEqual(paths.broker_sock_path(), Path("/tmp/run/spawnterm/broker.sock"))

    def test_sock_fallback_to_local_state(self):
        self.assertEqual(
            paths.broker_sock_path(), Path.home() / ".local/state/spawnterm/broker.sock"
        )


if __name__ == "__main__":
    unittest.main()
