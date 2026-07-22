#!/usr/bin/env python3
"""Unit tests for the pure registry (spawnTerm daemon #26).

No ``iterm2`` dependency — proves the registry logic runs in plain CI. Covers:
add on new_session, remove on terminate_session, agent-var update, and the
idle-state transition.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry import Registry, SessionRecord  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_add_on_new_session(self):
        reg = Registry()
        rec = reg.add("s1", title="agent-a", cwd="/tmp/a", agent_role="backend")
        self.assertIsInstance(rec, SessionRecord)
        self.assertIn("s1", reg)
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg.get("s1").title, "agent-a")
        self.assertEqual(reg.get("s1").cwd, "/tmp/a")
        self.assertEqual(reg.get("s1").agent_role, "backend")
        self.assertFalse(reg.get("s1").idle)

    def test_add_strips_user_prefix_and_ignores_unknown_vars(self):
        reg = Registry()
        reg.add("s1", **{"user.agent_status": "busy", "bogus": "x"})
        self.assertEqual(reg.get("s1").agent_status, "busy")
        # Unknown var did not create an attribute / crash.
        self.assertEqual(reg.get("s1").agent_role, "")

    def test_readd_merges_without_clobbering(self):
        reg = Registry()
        reg.add("s1", title="a", cwd="/tmp/a", agent_role="backend")
        reg.add("s1", agent_status="busy")  # no title/cwd supplied
        rec = reg.get("s1")
        self.assertEqual(rec.title, "a")       # preserved
        self.assertEqual(rec.cwd, "/tmp/a")    # preserved
        self.assertEqual(rec.agent_role, "backend")
        self.assertEqual(rec.agent_status, "busy")
        self.assertEqual(len(reg), 1)

    def test_remove_on_terminate_session(self):
        reg = Registry()
        reg.add("s1")
        self.assertTrue(reg.remove("s1"))
        self.assertNotIn("s1", reg)
        self.assertEqual(len(reg), 0)
        # Removing an unknown session is a no-op, not an error.
        self.assertFalse(reg.remove("ghost"))

    def test_update_agent_vars(self):
        reg = Registry()
        reg.add("s1", agent_status="idle", agent_role="backend")
        rec = reg.update("s1", agent_status="busy", agent_task="build #26")
        self.assertEqual(rec.agent_status, "busy")
        self.assertEqual(rec.agent_task, "build #26")
        self.assertEqual(rec.agent_role, "backend")  # untouched
        # Update on unknown session returns None, does not raise.
        self.assertIsNone(reg.update("ghost", agent_status="busy"))

    def test_update_title_and_cwd(self):
        reg = Registry()
        reg.add("s1", title="old", cwd="/old")
        rec = reg.update("s1", title="new", cwd="/new")
        self.assertEqual(rec.title, "new")
        self.assertEqual(rec.cwd, "/new")

    def test_idle_transition(self):
        reg = Registry()
        reg.add("s1")
        self.assertFalse(reg.get("s1").idle)
        rec = reg.set_idle("s1", True)          # prompt fired -> awaiting input
        self.assertTrue(rec.idle)
        self.assertTrue(reg.get("s1").idle)
        rec2 = reg.set_idle("s1", False)        # became busy again
        self.assertFalse(rec2.idle)
        # Idempotent + unknown-session safe.
        same = reg.set_idle("s1", False)
        self.assertFalse(same.idle)
        self.assertIsNone(reg.set_idle("ghost", True))

    def test_query_helpers(self):
        reg = Registry()
        reg.add("s1")
        reg.add("s2")
        self.assertEqual(sorted(reg.ids()), ["s1", "s2"])
        self.assertEqual(len(reg.all()), 2)
        self.assertIsNone(reg.get("nope"))


if __name__ == "__main__":
    unittest.main()
