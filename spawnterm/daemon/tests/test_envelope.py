#!/usr/bin/env python3
"""Unit tests for the pure envelope parser (spawnTerm daemon #26).

No ``iterm2`` dependency. Covers valid parse and the full family of malformed
inputs the daemon must survive without crashing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envelope import ENVELOPE_VERSION, parse_envelope  # noqa: E402


class TestEnvelope(unittest.TestCase):
    def test_valid_full_envelope(self):
        payload = '{"v":1,"type":"msg","to":"agent-b","from":"agent-a","body":"hi"}'
        result = parse_envelope(payload)
        self.assertTrue(result.ok)
        self.assertIsNone(result.error)
        env = result.envelope
        self.assertEqual(env.v, ENVELOPE_VERSION)
        self.assertEqual(env.type, "msg")
        self.assertEqual(env.to, "agent-b")
        self.assertEqual(env.sender, "agent-a")  # wire key "from"
        self.assertEqual(env.body, "hi")
        self.assertTrue(env.known_type)

    def test_valid_minimal_envelope(self):
        result = parse_envelope('{"v":1,"type":"ping"}')
        self.assertTrue(result.ok)
        self.assertEqual(result.envelope.type, "ping")
        self.assertIsNone(result.envelope.to)
        self.assertIsNone(result.envelope.sender)
        self.assertIsNone(result.envelope.body)

    def test_unknown_type_still_parses(self):
        result = parse_envelope('{"v":1,"type":"handoff"}')
        self.assertTrue(result.ok)
        self.assertEqual(result.envelope.type, "handoff")
        self.assertFalse(result.envelope.known_type)  # flagged, not rejected

    def test_body_can_be_object(self):
        result = parse_envelope('{"v":1,"type":"msg","body":{"k":[1,2]}}')
        self.assertTrue(result.ok)
        self.assertEqual(result.envelope.body, {"k": [1, 2]})

    def test_whitespace_is_tolerated(self):
        result = parse_envelope('   {"v":1,"type":"msg"}   ')
        self.assertTrue(result.ok)

    # -- malformed inputs: must fail cleanly (ok=False), never raise --------

    def test_empty_payload(self):
        for bad in ("", "   ", None):
            result = parse_envelope(bad)
            self.assertFalse(result.ok)
            self.assertIsNone(result.envelope)
            self.assertTrue(result.error)

    def test_invalid_json(self):
        result = parse_envelope("{not json")
        self.assertFalse(result.ok)
        self.assertIn("invalid JSON", result.error)

    def test_non_object_json(self):
        for bad in ("[1,2,3]", '"a string"', "42"):
            result = parse_envelope(bad)
            self.assertFalse(result.ok)
            self.assertIn("JSON object", result.error)

    def test_missing_version(self):
        result = parse_envelope('{"type":"msg"}')
        self.assertFalse(result.ok)
        self.assertIn("v", result.error)

    def test_non_integer_version(self):
        result = parse_envelope('{"v":"1","type":"msg"}')
        self.assertFalse(result.ok)
        # Booleans must not sneak through as ints either.
        result_bool = parse_envelope('{"v":true,"type":"msg"}')
        self.assertFalse(result_bool.ok)

    def test_unsupported_version(self):
        result = parse_envelope('{"v":2,"type":"msg"}')
        self.assertFalse(result.ok)
        self.assertIn("version", result.error)

    def test_missing_or_empty_type(self):
        self.assertFalse(parse_envelope('{"v":1}').ok)
        self.assertFalse(parse_envelope('{"v":1,"type":""}').ok)
        self.assertFalse(parse_envelope('{"v":1,"type":"   "}').ok)
        self.assertFalse(parse_envelope('{"v":1,"type":123}').ok)


if __name__ == "__main__":
    unittest.main()
