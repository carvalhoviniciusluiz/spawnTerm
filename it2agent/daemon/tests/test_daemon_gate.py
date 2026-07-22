#!/usr/bin/env python3
"""Tests for the daemon's purity guarantee and feature-flag gate (#26).

Proves the pure/gate path never pulls in ``iterm2`` and that the daemon honors
the default-OFF ``agent.daemon`` flag.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPurity(unittest.TestCase):
    def test_pure_modules_import_without_iterm2(self):
        # Import the pure modules and the entry/adapter modules; none of them
        # may import iterm2 at module load time.
        import adapter  # noqa: F401
        import bridge  # noqa: F401
        import envelope  # noqa: F401
        import registry  # noqa: F401
        import it2agent_daemon  # noqa: F401

        self.assertNotIn("iterm2", sys.modules)


class TestFlagGate(unittest.TestCase):
    def setUp(self):
        import it2agent_daemon

        self.daemon = it2agent_daemon
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = os.path.join(self._tmp.name, "config.toml")
        self._old = os.environ.get("IT2AGENT_CONFIG")
        os.environ["IT2AGENT_CONFIG"] = self._cfg
        self._old_force = os.environ.pop("IT2AGENT_FORCE", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("IT2AGENT_CONFIG", None)
        else:
            os.environ["IT2AGENT_CONFIG"] = self._old
        if self._old_force is not None:
            os.environ["IT2AGENT_FORCE"] = self._old_force
        self._tmp.cleanup()

    def _write_flag(self, value: str) -> None:
        with open(self._cfg, "w", encoding="utf-8") as handle:
            handle.write('[features]\n"agent.daemon" = %s\n' % value)

    def test_off_when_no_config(self):
        self.assertFalse(self.daemon.flag_enabled())
        self.assertFalse(self.daemon.gate_open(no_gate=False))

    def test_off_when_flag_false(self):
        self._write_flag("false")
        self.assertFalse(self.daemon.flag_enabled())

    def test_on_when_flag_true(self):
        self._write_flag("true")
        self.assertTrue(self.daemon.flag_enabled())
        self.assertTrue(self.daemon.gate_open(no_gate=False))

    def test_no_gate_bypasses(self):
        self.assertTrue(self.daemon.gate_open(no_gate=True))

    def test_force_env_bypasses(self):
        os.environ["IT2AGENT_FORCE"] = "1"
        try:
            self.assertTrue(self.daemon.gate_open(no_gate=False))
        finally:
            os.environ.pop("IT2AGENT_FORCE", None)

    def test_main_exits_zero_when_gated_off(self):
        # Flag OFF -> refuse to start, exit 0 (never touches iterm2).
        rc = self.daemon.main([])
        self.assertEqual(rc, 0)
        self.assertNotIn("iterm2", sys.modules)


if __name__ == "__main__":
    unittest.main()
