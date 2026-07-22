#!/usr/bin/env python3
"""Unit tests for the pure daemon↔broker bridge (spawnTerm Tier 2.4, #37).

No ``iterm2`` and no live broker socket. The broker is a recording fake
(:class:`FakeBroker`) and the two iTerm2 reads/writes are fake async callables
with scripted screen text, so every decision the bridge makes — mode selection,
envelope→op mapping, ingest (durable / in-memory / degraded), delivery polling,
ack-by-observation, and registry population — is exercised deterministically
with no sleeps.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge import (  # noqa: E402
    MODE_DURABLE,
    MODE_IN_MEMORY,
    MODE_OFF,
    Bridge,
    build_register_op,
    build_send_op,
    build_touch_op,
    format_durable_delivery,
    marker_for,
    recipient_keys,
    select_mode,
    synthetic_envelope,
    was_observed,
)
from envelope import parse_envelope  # noqa: E402
from registry import Registry  # noqa: E402


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


class FakeBroker:
    """Records every request; returns scripted responses. Optionally raises to
    simulate a down/unreachable broker (graceful-degradation tests)."""

    def __init__(self, *, poll_map=None, send_id=1, raise_ops=()):
        self.requests = []
        self.poll_map = dict(poll_map or {})
        self.send_id = send_id
        self.raise_ops = set(raise_ops)

    def request(self, message):
        self.requests.append(message)
        op = message.get("op")
        if op in self.raise_ops:
            raise OSError("connection refused")
        if op == "send":
            return {"ok": True, "id": self.send_id}
        if op in ("poll", "fetch"):
            msgs = self.poll_map.get(message.get("agent"), [])
            return {"ok": True, "messages": msgs, "count": len(msgs)}
        if op == "ack":
            return {"ok": True, "acked": 1, "cursor": message.get("msg_id")}
        if op in ("register", "touch"):
            return {"ok": True, "agent": {"session_id": message.get("session_id")}}
        return {"ok": True}

    def ops(self):
        return [r.get("op") for r in self.requests]


class RecordingIO:
    """Fake async iTerm2 I/O: records sent text and returns scripted screens."""

    def __init__(self, screens=None):
        self.sent = []  # (session_id, text)
        self.reads = []  # session_ids read
        # session_id -> screen text returned by read_screen (default "").
        self.screens = dict(screens or {})

    async def send_text(self, session_id, text):
        self.sent.append((session_id, text))

    async def read_screen(self, session_id):
        self.reads.append(session_id)
        return self.screens.get(session_id, "")


def _bridge(broker, reg, io, *, messaging=True, broker_on=True):
    return Bridge(
        broker,
        reg,
        send_text=io.send_text,
        read_screen=io.read_screen,
        messaging_enabled=lambda: messaging,
        broker_enabled=lambda: broker_on,
    )


# --------------------------------------------------------------------------- #
# Pure decision functions.
# --------------------------------------------------------------------------- #


class TestSelectMode(unittest.TestCase):
    def test_messaging_off_is_off(self):
        self.assertEqual(
            select_mode(messaging_enabled=False, broker_enabled=True, has_broker_client=True),
            MODE_OFF,
        )

    def test_both_on_with_client_is_durable(self):
        self.assertEqual(
            select_mode(messaging_enabled=True, broker_enabled=True, has_broker_client=True),
            MODE_DURABLE,
        )

    def test_broker_off_falls_back_to_in_memory(self):
        self.assertEqual(
            select_mode(messaging_enabled=True, broker_enabled=False, has_broker_client=True),
            MODE_IN_MEMORY,
        )

    def test_no_client_falls_back_to_in_memory(self):
        self.assertEqual(
            select_mode(messaging_enabled=True, broker_enabled=True, has_broker_client=False),
            MODE_IN_MEMORY,
        )


class TestBuildSendOp(unittest.TestCase):
    def test_valid_envelope(self):
        op, reason = build_send_op(_env(to="beta", **{"from": "alpha"}, body="hi"))
        self.assertEqual(reason, "")
        self.assertEqual(op, {"op": "send", "to": "beta", "from": "alpha", "body": "hi"})

    def test_no_destination(self):
        op, reason = build_send_op(_env(**{"from": "alpha"}, body="hi"))
        self.assertIsNone(op)
        self.assertEqual(reason, "no destination")

    def test_empty_body(self):
        op, reason = build_send_op(_env(to="beta", **{"from": "alpha"}, body="   "))
        self.assertIsNone(op)
        self.assertEqual(reason, "empty body")

    def test_object_body_serialized(self):
        op, _ = build_send_op(_env(to="beta", **{"from": "alpha"}, body={"k": 1}))
        self.assertEqual(op["body"], '{"k":1}')

    def test_missing_sender_defaults_unknown(self):
        op, _ = build_send_op(_env(to="beta", body="hi"))
        self.assertEqual(op["from"], "unknown")


class TestAckByObservation(unittest.TestCase):
    def test_marker_present_is_observed(self):
        text, marker = format_durable_delivery(7, "alpha", "hello")
        self.assertEqual(marker, marker_for(7))
        self.assertIn(marker, text)
        screen = "some prompt\n" + text + "$ "
        self.assertTrue(was_observed(screen, marker))

    def test_marker_absent_is_not_observed(self):
        _, marker = format_durable_delivery(7, "alpha", "hello")
        self.assertFalse(was_observed("nothing here for id 8", marker))

    def test_empty_screen_is_not_observed(self):
        self.assertFalse(was_observed("", marker_for(1)))
        self.assertFalse(was_observed(None, marker_for(1)))


class TestRecipientKeys(unittest.TestCase):
    def test_union_of_ids_and_roles_deduped(self):
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "backend"))
        self.assertEqual(recipient_keys(reg.all()), ["alpha", "backend", "beta"])

    def test_skips_empty(self):
        reg = _registry(("s1", "", ""))
        self.assertEqual(recipient_keys(reg.all()), [])


class TestRegistryOps(unittest.TestCase):
    def test_build_register_op_includes_role_task(self):
        reg = Registry()
        rec = reg.add("s1", agent_id="a1", agent_role="coder", agent_task="build")
        self.assertEqual(
            build_register_op(rec),
            {"op": "register", "session_id": "s1", "alive": True, "role": "coder", "task": "build"},
        )

    def test_build_register_op_bare(self):
        reg = Registry()
        rec = reg.add("s1")
        self.assertEqual(build_register_op(rec), {"op": "register", "session_id": "s1", "alive": True})

    def test_build_touch_op(self):
        self.assertEqual(
            build_touch_op("s1", alive=False),
            {"op": "touch", "session_id": "s1", "alive": False},
        )

    def test_synthetic_envelope_roundtrip(self):
        env = synthetic_envelope({"id": 3, "from": "a", "to": "b", "body": "hi"})
        self.assertEqual((env.to, env.sender, env.body), ("b", "a", "hi"))


# --------------------------------------------------------------------------- #
# Bridge orchestration (fake broker + fake iTerm2 I/O).
# --------------------------------------------------------------------------- #


class TestIngest(unittest.IsolatedAsyncioTestCase):
    async def test_durable_send_calls_broker_not_iterm2(self):
        broker = FakeBroker(send_id=42)
        io = RecordingIO()
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=True)

        outcome = await bridge.handle_ingest(_env(to="beta", **{"from": "alpha"}, body="hi"))

        self.assertEqual(outcome.mode, MODE_DURABLE)
        self.assertEqual(outcome.action, "durable_send")
        self.assertEqual(outcome.msg_id, 42)
        self.assertEqual(
            broker.requests, [{"op": "send", "to": "beta", "from": "alpha", "body": "hi"}]
        )
        self.assertEqual(io.sent, [])  # durable ingest does not inject immediately

    async def test_in_memory_when_broker_off_routes_and_injects(self):
        broker = FakeBroker()
        io = RecordingIO()
        reg = _registry(("s1", "alpha", "backend"), ("s2", "beta", "frontend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=False)

        outcome = await bridge.handle_ingest(_env(to="beta", **{"from": "alpha"}, body="hi"))

        self.assertEqual(outcome.mode, MODE_IN_MEMORY)
        self.assertEqual(outcome.action, "delivered")
        self.assertEqual(outcome.targets, ("s2",))
        self.assertEqual(broker.requests, [])  # broker untouched in in-memory mode
        self.assertEqual(len(io.sent), 1)
        self.assertEqual(io.sent[0][0], "s2")
        self.assertIn("hi", io.sent[0][1])

    async def test_degrades_to_in_memory_when_broker_raises(self):
        broker = FakeBroker(raise_ops={"send"})
        io = RecordingIO()
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=True)

        outcome = await bridge.handle_ingest(_env(to="beta", **{"from": "alpha"}, body="hi"))

        # Tried durable send (which raised), then fell back to in-memory route.
        self.assertEqual(broker.ops(), ["send"])
        self.assertEqual(outcome.action, "delivered")
        self.assertTrue(outcome.degraded)
        self.assertEqual(io.sent[0][0], "s2")

    async def test_messaging_off_drops(self):
        broker = FakeBroker()
        io = RecordingIO()
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=False, broker_on=True)

        outcome = await bridge.handle_ingest(_env(to="beta", **{"from": "alpha"}, body="hi"))

        self.assertEqual(outcome.mode, MODE_OFF)
        self.assertEqual(outcome.action, "dropped")
        self.assertEqual(broker.requests, [])
        self.assertEqual(io.sent, [])


class TestDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_polls_injects_and_acks_when_observed(self):
        message = {"id": 5, "from": "alpha", "to": "beta", "body": "ping"}
        broker = FakeBroker(poll_map={"beta": [message]})
        marker = marker_for(5)
        # The target echoes the injected line, so the marker is on screen.
        io = RecordingIO(screens={"s2": f"$ {marker} message from alpha: ping\n"})
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=True)

        outcome = await bridge.deliver_once()

        self.assertEqual(outcome.polled, 1)
        self.assertEqual(outcome.delivered, 1)
        self.assertEqual(outcome.acked, 1)
        self.assertEqual(io.sent[0][0], "s2")
        self.assertIn({"op": "ack", "agent": "beta", "msg_id": 5}, broker.requests)

    async def test_no_ack_when_not_observed(self):
        message = {"id": 5, "from": "alpha", "to": "beta", "body": "ping"}
        broker = FakeBroker(poll_map={"beta": [message]})
        # Screen does NOT contain the marker → not observed → must not ack.
        io = RecordingIO(screens={"s2": "$ (a busy agent, nothing echoed)\n"})
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=True)

        outcome = await bridge.deliver_once()

        self.assertEqual(outcome.delivered, 1)
        self.assertEqual(outcome.acked, 0)
        self.assertNotIn("ack", broker.ops())

    async def test_no_op_when_not_durable(self):
        broker = FakeBroker(poll_map={"beta": [{"id": 1, "from": "a", "to": "beta", "body": "x"}]})
        io = RecordingIO()
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=False)  # in-memory

        outcome = await bridge.deliver_once()

        self.assertEqual((outcome.polled, outcome.delivered, outcome.acked), (0, 0, 0))
        self.assertEqual(broker.requests, [])  # no poll when not durable

    async def test_poll_failure_degrades_without_crashing(self):
        broker = FakeBroker(poll_map={"beta": []}, raise_ops={"poll"})
        io = RecordingIO()
        reg = _registry(("s2", "beta", "backend"))
        bridge = _bridge(broker, reg, io, messaging=True, broker_on=True)

        outcome = await bridge.deliver_once()

        self.assertTrue(outcome.degraded)
        self.assertEqual(outcome.delivered, 0)


class TestRegistryPopulation(unittest.TestCase):
    def test_note_session_registers_when_broker_on(self):
        broker = FakeBroker()
        reg = Registry()
        rec = reg.add("s1", agent_id="a1", agent_role="coder", agent_task="build")
        bridge = _bridge(broker, reg, RecordingIO(), broker_on=True)

        self.assertTrue(bridge.note_session(rec))
        self.assertEqual(
            broker.requests,
            [{"op": "register", "session_id": "s1", "alive": True, "role": "coder", "task": "build"}],
        )

    def test_note_session_noop_when_broker_off(self):
        broker = FakeBroker()
        reg = Registry()
        rec = reg.add("s1", agent_role="coder")
        bridge = _bridge(broker, reg, RecordingIO(), broker_on=False)

        self.assertFalse(bridge.note_session(rec))
        self.assertEqual(broker.requests, [])

    def test_note_terminated_touches_alive_false(self):
        broker = FakeBroker()
        bridge = _bridge(broker, Registry(), RecordingIO(), broker_on=True)

        self.assertTrue(bridge.note_terminated("s1"))
        self.assertEqual(broker.requests, [{"op": "touch", "session_id": "s1", "alive": False}])

    def test_note_session_degrades_without_crashing(self):
        broker = FakeBroker(raise_ops={"register"})
        reg = Registry()
        rec = reg.add("s1", agent_role="coder")
        bridge = _bridge(broker, reg, RecordingIO(), broker_on=True)

        self.assertFalse(bridge.note_session(rec))  # broker raised → False, no crash


if __name__ == "__main__":
    unittest.main()
