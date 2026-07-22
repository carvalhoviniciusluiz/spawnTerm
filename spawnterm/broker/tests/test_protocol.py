#!/usr/bin/env python3
"""Tests for the broker wire protocol (#34): newline-delimited JSON framing and
round-trip serialization."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocol  # noqa: E402


class TestProtocol(unittest.TestCase):
    def test_encode_is_single_newline_terminated_line(self):
        blob = protocol.encode({"op": "ping"})
        self.assertTrue(blob.endswith(b"\n"))
        self.assertEqual(blob.count(b"\n"), 1)
        self.assertIsInstance(blob, bytes)

    def test_round_trip(self):
        original = {"op": "send", "to": "a1", "body": "hi", "n": 3, "nested": {"x": [1, 2]}}
        self.assertEqual(protocol.decode(protocol.encode(original)), original)

    def test_decode_accepts_str_and_bytes(self):
        self.assertEqual(protocol.decode('{"op":"ping"}'), {"op": "ping"})
        self.assertEqual(protocol.decode(b'{"op":"ping"}\n'), {"op": "ping"})

    def test_decode_tolerates_whitespace(self):
        self.assertEqual(protocol.decode(b'  {"op":"ping"}  \n'), {"op": "ping"})

    def test_encode_is_deterministic(self):
        a = protocol.encode({"b": 1, "a": 2})
        b = protocol.encode({"a": 2, "b": 1})
        self.assertEqual(a, b)  # sorted keys

    def test_decode_rejects_empty(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(b"\n")

    def test_decode_rejects_invalid_json(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(b"{not json}\n")

    def test_decode_rejects_non_object(self):
        for payload in (b"[1,2,3]\n", b'"a string"\n', b"42\n"):
            with self.assertRaises(protocol.ProtocolError):
                protocol.decode(payload)

    def test_encode_rejects_non_object(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode([1, 2, 3])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
