#!/usr/bin/env python3
"""Unit tests for the pure best-effort router (it2agent daemon #28).

No ``iterm2`` dependency. Covers id/role resolution and precedence, the
undeliverable family (no match / self / empty body / no destination), the
``agent.messaging`` gate (default OFF), and the never-raise contract.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envelope import parse_envelope  # noqa: E402
from registry import Registry  # noqa: E402
from router import (  # noqa: E402
    DELIVERY_PREFIX,
    MATCH_BY_ID,
    MATCH_BY_ROLE,
    messaging_enabled,
    route,
    route_if_enabled,
)


def _env(**kwargs):
    """Build a parsed Envelope from wire fields (``from`` becomes ``sender``)."""
    import json

    payload = {"v": 1, "type": "msg"}
    payload.update(kwargs)
    result = parse_envelope(json.dumps(payload))
    assert result.ok, result.error
    return result.envelope


def _registry(*specs) -> Registry:
    """Populate a Registry from (session_id, agent_id, agent_role) tuples."""
    reg = Registry()
    for session_id, agent_id, agent_role in specs:
        reg.add(session_id, agent_id=agent_id, agent_role=agent_role)
    return reg


class TestRoute(unittest.TestCase):
    def test_route_by_agent_id(self):
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "frontend"))
        decision = route(_env(to="beta", **{"from": "alpha"}, body="hi"), reg)
        self.assertTrue(decision.deliverable)
        self.assertEqual(decision.target_session_ids, ("s2",))
        self.assertEqual(decision.matched_by, MATCH_BY_ID)
        self.assertTrue(decision.text.startswith(DELIVERY_PREFIX))
        self.assertIn("alpha", decision.text)  # sender is carried
        self.assertIn("hi", decision.text)

    def test_route_by_role_fans_out_to_all(self):
        reg = _registry(
            ("s1", "alpha", "backend"),
            ("s2", "beta", "backend"),
            ("s3", "gamma", "frontend"),
        )
        decision = route(_env(to="backend", **{"from": "gamma"}, body="deploy"), reg)
        self.assertTrue(decision.deliverable)
        self.assertEqual(decision.matched_by, MATCH_BY_ROLE)
        self.assertEqual(set(decision.target_session_ids), {"s1", "s2"})

    def test_id_takes_precedence_over_role(self):
        # "backend" is both an id (s9) and a role (s1); id wins, role ignored.
        reg = _registry(("s1", "alpha", "backend"), ("s9", "backend", "ops"))
        decision = route(_env(to="backend", **{"from": "alpha"}, body="hi"), reg)
        self.assertTrue(decision.deliverable)
        self.assertEqual(decision.matched_by, MATCH_BY_ID)
        self.assertEqual(decision.target_session_ids, ("s9",))

    def test_no_match_is_undeliverable(self):
        reg = _registry(("s1", "alpha", "backend"))
        decision = route(_env(to="nobody", **{"from": "alpha"}, body="hi"), reg)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "no match")
        self.assertEqual(decision.target_session_ids, ())

    def test_self_send_guarded(self):
        # Sending to your own id must not echo back into your session.
        reg = _registry(("s1", "alpha", "backend"))
        decision = route(_env(to="alpha", **{"from": "alpha"}, body="hi"), reg)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "self")

    def test_self_send_by_role_excludes_sender_delivers_to_others(self):
        # Role fan-out drops the sender's own session but reaches peers.
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "backend"))
        decision = route(_env(to="backend", **{"from": "alpha"}, body="hi"), reg)
        self.assertTrue(decision.deliverable)
        self.assertEqual(decision.target_session_ids, ("s2",))

    def test_self_only_role_is_undeliverable(self):
        reg = _registry(("s1", "alpha", "backend"))
        decision = route(_env(to="backend", **{"from": "alpha"}, body="hi"), reg)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "self")

    def test_empty_body_is_undeliverable(self):
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "frontend"))
        for body in ("", "   "):
            decision = route(_env(to="beta", **{"from": "alpha"}, body=body), reg)
            self.assertFalse(decision.deliverable)
            self.assertEqual(decision.reason, "empty body")

    def test_missing_destination_is_undeliverable(self):
        reg = _registry(("s1", "alpha", "backend"))
        decision = route(_env(**{"from": "alpha"}, body="hi"), reg)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "no destination")

    def test_object_body_is_serialized(self):
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "frontend"))
        decision = route(_env(to="beta", **{"from": "alpha"}, body={"k": 1}), reg)
        self.assertTrue(decision.deliverable)
        self.assertIn('{"k":1}', decision.text)

    def test_never_raises_on_bad_input(self):
        # None envelope / None registry must not raise.
        self.assertFalse(route(None, _registry()).deliverable)
        self.assertFalse(route(_env(to="x", body="y"), None).deliverable)


class TestGate(unittest.TestCase):
    def test_gate_off_produces_no_routing_decision(self):
        # Even a perfectly deliverable envelope yields nothing when disabled.
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "frontend"))
        env = _env(to="beta", **{"from": "alpha"}, body="hi")
        decision = route_if_enabled(env, reg, enabled=False)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "messaging disabled")
        self.assertEqual(decision.target_session_ids, ())

    def test_gate_on_routes(self):
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "frontend"))
        env = _env(to="beta", **{"from": "alpha"}, body="hi")
        decision = route_if_enabled(env, reg, enabled=True)
        self.assertTrue(decision.deliverable)
        self.assertEqual(decision.target_session_ids, ("s2",))


class TestMessagingFlag(unittest.TestCase):
    """messaging_enabled() reads the shared #11 flag; default OFF."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = os.path.join(self._tmp.name, "config.toml")
        self._old = os.environ.get("IT2AGENT_CONFIG")
        os.environ["IT2AGENT_CONFIG"] = self._cfg

    def tearDown(self):
        if self._old is None:
            os.environ.pop("IT2AGENT_CONFIG", None)
        else:
            os.environ["IT2AGENT_CONFIG"] = self._old
        self._tmp.cleanup()

    def _write_flag(self, value: str) -> None:
        with open(self._cfg, "w", encoding="utf-8") as handle:
            handle.write('[features]\n"agent.messaging" = %s\n' % value)

    def test_off_when_no_config(self):
        self.assertFalse(messaging_enabled())

    def test_off_when_flag_false(self):
        self._write_flag("false")
        self.assertFalse(messaging_enabled())

    def test_on_when_flag_true(self):
        self._write_flag("true")
        self.assertTrue(messaging_enabled())


if __name__ == "__main__":
    unittest.main()
