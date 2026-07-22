#!/usr/bin/env python3
"""Tests for the broker's purity guarantee and feature-flag gate (#34).

Proves the pure/gate path never pulls in a socket loop or iTerm2, and that the
server honors the default-OFF ``spawnterm.broker`` flag (mirrors the daemon)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPurity(unittest.TestCase):
    def test_pure_modules_import_without_iterm2(self):
        import dispatch  # noqa: F401
        import paths  # noqa: F401
        import protocol  # noqa: F401
        import schema  # noqa: F401
        import spawnterm_broker  # noqa: F401

        self.assertNotIn("iterm2", sys.modules)


class TestFlagGate(unittest.TestCase):
    def setUp(self):
        import spawnterm_broker

        self.broker = spawnterm_broker
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
            handle.write('[features]\n"spawnterm.broker" = %s\n' % value)

    def test_off_when_no_config(self):
        self.assertFalse(self.broker.flag_enabled())
        self.assertFalse(self.broker.gate_open(no_gate=False))

    def test_off_when_flag_false(self):
        self._write_flag("false")
        self.assertFalse(self.broker.flag_enabled())

    def test_on_when_flag_true(self):
        self._write_flag("true")
        self.assertTrue(self.broker.flag_enabled())
        self.assertTrue(self.broker.gate_open(no_gate=False))

    def test_no_gate_bypasses(self):
        self.assertTrue(self.broker.gate_open(no_gate=True))

    def test_force_env_bypasses(self):
        os.environ["SPAWNTERM_FORCE"] = "1"
        try:
            self.assertTrue(self.broker.gate_open(no_gate=False))
        finally:
            os.environ.pop("SPAWNTERM_FORCE", None)


if __name__ == "__main__":
    unittest.main()
