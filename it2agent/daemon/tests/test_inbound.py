#!/usr/bin/env python3
"""Unit tests for the pure inbound native-state reader (it2agent #115).

No ``iterm2`` dependency — proves the session-record -> registry-op mapping and
its application to the registry run headless with fixtures. Covers: present /
absent user-vars, the ``user.`` prefix, native cc-status states and their
translation, explicit-status precedence, the no-session-id no-op, and the
API-off empty-batch no-op.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inbound import (  # noqa: E402
    CC_STATUS_TO_AGENT_STATUS,
    RegistryOp,
    apply_op,
    map_native_session,
    normalize_agent_status,
    reflect_native_sessions,
)
from registry import Registry  # noqa: E402


class TestNormalizeStatus(unittest.TestCase):
    def test_explicit_agent_status_wins(self):
        self.assertEqual(
            normalize_agent_status(agent_status="done", cc_status="working"), "done"
        )

    def test_native_working_maps_to_busy(self):
        self.assertEqual(normalize_agent_status(cc_status="working"), "busy")

    def test_native_waiting_maps_to_blocked(self):
        self.assertEqual(normalize_agent_status(cc_status="waiting"), "blocked")

    def test_native_idle_stays_idle(self):
        self.assertEqual(normalize_agent_status(cc_status="idle"), "idle")

    def test_native_case_insensitive(self):
        self.assertEqual(normalize_agent_status(cc_status="  WORKING "), "busy")

    def test_unrecognized_native_is_empty(self):
        self.assertEqual(normalize_agent_status(cc_status="compiling"), "")

    def test_nothing_is_empty(self):
        self.assertEqual(normalize_agent_status(), "")

    def test_translation_table_covers_our_vocab(self):
        # Our own lifecycle values pass through unchanged.
        for value in ("busy", "blocked", "done", "idle"):
            self.assertEqual(CC_STATUS_TO_AGENT_STATUS.get(value), value)


class TestMapNativeSession(unittest.TestCase):
    def test_present_agent_vars(self):
        op = map_native_session(
            {
                "session_id": "s1",
                "name": "agent-a",
                "cwd": "/tmp/a",
                "agent_role": "backend",
                "agent_task": "build #115",
                "agent_id": "a1",
                "agent_status": "busy",
            }
        )
        self.assertEqual(
            op,
            RegistryOp(
                session_id="s1",
                title="agent-a",
                cwd="/tmp/a",
                agent_vars={
                    "agent_role": "backend",
                    "agent_task": "build #115",
                    "agent_id": "a1",
                    "agent_status": "busy",
                },
            ),
        )

    def test_user_prefixed_keys_are_stripped(self):
        op = map_native_session(
            {"session_id": "s1", "user.agent_role": "frontend", "user.agent_status": "idle"}
        )
        self.assertEqual(op.agent_vars, {"agent_role": "frontend", "agent_status": "idle"})

    def test_absent_uservars_uses_native_cc_status(self):
        # A purely-native session: no agent_* vars, only a cc-status tab-status.
        op = map_native_session({"session_id": "s2", "name": "shell", "cc_status": "working"})
        self.assertEqual(op.agent_vars, {"agent_status": "busy"})
        self.assertEqual(op.title, "shell")

    def test_native_waiting_state(self):
        op = map_native_session({"session_id": "s3", "cc_status": "waiting"})
        self.assertEqual(op.agent_vars, {"agent_status": "blocked"})

    def test_explicit_status_beats_native(self):
        op = map_native_session(
            {"session_id": "s4", "agent_status": "done", "cc_status": "working"}
        )
        self.assertEqual(op.agent_vars["agent_status"], "done")

    def test_unrecognized_native_yields_no_status(self):
        op = map_native_session({"session_id": "s5", "agent_role": "ops", "cc_status": "linting"})
        self.assertEqual(op.agent_vars, {"agent_role": "ops"})
        self.assertNotIn("agent_status", op.agent_vars)

    def test_unknown_and_empty_vars_ignored(self):
        op = map_native_session(
            {"session_id": "s6", "bogus": "x", "agent_role": "", "agent_task": "   "}
        )
        self.assertEqual(op.agent_vars, {})

    def test_path_and_title_aliases(self):
        op = map_native_session({"session_id": "s7", "title": "t", "path": "/p"})
        self.assertEqual((op.title, op.cwd), ("t", "/p"))

    def test_missing_session_id_is_noop(self):
        self.assertIsNone(map_native_session({"name": "orphan", "agent_role": "x"}))
        self.assertIsNone(map_native_session({"session_id": "   "}))
        self.assertIsNone(map_native_session({}))


class TestApplyToRegistry(unittest.TestCase):
    def test_apply_op_upserts(self):
        reg = Registry()
        op = map_native_session({"session_id": "s1", "name": "a", "cc_status": "working"})
        rec = apply_op(reg, op)
        self.assertEqual(rec.session_id, "s1")
        self.assertEqual(rec.title, "a")
        self.assertEqual(rec.agent_status, "busy")
        self.assertIn("s1", reg)

    def test_reflect_batch_merges_without_clobbering(self):
        reg = Registry()
        reg.add("s1", title="kept", agent_role="backend")  # pre-existing state
        applied = reflect_native_sessions(
            reg,
            [
                {"session_id": "s1", "cc_status": "waiting"},  # merges status onto s1
                {"session_id": "s2", "name": "native", "cc_status": "idle"},  # new
                {"name": "no-id"},  # skipped (no session_id)
            ],
        )
        self.assertEqual(len(applied), 2)
        self.assertEqual(reg.get("s1").title, "kept")          # not clobbered
        self.assertEqual(reg.get("s1").agent_role, "backend")  # preserved
        self.assertEqual(reg.get("s1").agent_status, "blocked")  # from native waiting
        self.assertEqual(reg.get("s2").agent_status, "idle")
        self.assertEqual(len(reg), 2)

    def test_empty_batch_is_noop(self):
        # The shape the adapter produces when the Python API is off / unreachable.
        reg = Registry()
        self.assertEqual(reflect_native_sessions(reg, []), [])
        self.assertEqual(len(reg), 0)


if __name__ == "__main__":
    unittest.main()
