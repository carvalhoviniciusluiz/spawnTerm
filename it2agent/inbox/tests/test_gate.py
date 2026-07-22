#!/usr/bin/env python3
"""Tests for the feature-flag gate (#17) and module purity: the gate reads
agent.inbox (default OFF), honors --no-gate / IT2AGENT_FORCE, and no
inbox module imports iterm2."""

import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gate  # noqa: E402


class TestGate(unittest.TestCase):
    def setUp(self):
        # Point the flags helper at a throwaway config so the flag reads OFF.
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_cfg = os.environ.get("IT2AGENT_CONFIG")
        self._prev_force = os.environ.get("IT2AGENT_FORCE")
        os.environ["IT2AGENT_CONFIG"] = os.path.join(self._tmp.name, "config.toml")
        os.environ.pop("IT2AGENT_FORCE", None)

    def tearDown(self):
        if self._prev_cfg is None:
            os.environ.pop("IT2AGENT_CONFIG", None)
        else:
            os.environ["IT2AGENT_CONFIG"] = self._prev_cfg
        if self._prev_force is not None:
            os.environ["IT2AGENT_FORCE"] = self._prev_force
        self._tmp.cleanup()

    def test_default_off(self):
        self.assertFalse(gate.inbox_enabled())
        self.assertFalse(gate.gate_open())

    def test_no_gate_bypasses(self):
        self.assertTrue(gate.gate_open(no_gate=True))

    def test_force_env_bypasses(self):
        os.environ["IT2AGENT_FORCE"] = "1"
        self.assertTrue(gate.gate_open())

    def test_flag_on_opens_gate(self):
        # Write the flag ON via the shared flags helper and confirm the gate reads it.
        flags_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "flags",
        )
        sys.path.insert(0, flags_dir)
        it2agent_flag = importlib.import_module("it2agent_flag")
        it2agent_flag._set("inbox", True)  # writes IT2AGENT_CONFIG
        self.assertTrue(gate.inbox_enabled())
        self.assertTrue(gate.gate_open())


class TestPurity(unittest.TestCase):
    def test_no_iterm2_import(self):
        for mod in ("model", "policy", "config", "attention", "store", "gate", "inbox"):
            importlib.import_module(mod)
        self.assertNotIn("iterm2", sys.modules,
                         "inbox modules must not import iterm2")


if __name__ == "__main__":
    unittest.main()
