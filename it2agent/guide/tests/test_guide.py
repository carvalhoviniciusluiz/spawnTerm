#!/usr/bin/env python3
"""Tests for the generated capability guide + brief (#113).

Pins the single-source-of-truth contract: the guide is GENERATED from the flag
schema (KNOWN_FLAGS) and the MCP tool registry, so
  * every flag and every MCP tool appears in the rendered guide,
  * adding or removing a flag/tool CHANGES the output (no stale doc),
  * the committed AGENT_GUIDE.md matches a fresh render (drift guard),
  * the brief reflects live flag state.

Run: python3 it2agent/guide/tests/test_guide.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_GUIDE_DIR = _HERE.parent
_FLAGS_DIR = _GUIDE_DIR.parent / "flags"
for _d in (_GUIDE_DIR, _FLAGS_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import it2agent_guide as guide  # noqa: E402
import it2agent_flag  # noqa: E402


class TestRenderGuide(unittest.TestCase):
    def test_title_and_generated_note(self):
        text = guide.render_guide()
        self.assertTrue(text.startswith(guide.TITLE))
        self.assertIn("GENERATED", text)
        self.assertIn("it2agent-flag", text)

    def test_every_flag_appears(self):
        text = guide.render_guide()
        for cap, desc in it2agent_flag.KNOWN_FLAGS.items():
            self.assertIn(f"agent.{cap}", text, f"flag {cap} missing from guide")
            self.assertIn(desc, text, f"description for {cap} missing from guide")

    def test_every_mcp_tool_appears(self):
        tools = guide._load_tools()
        self.assertTrue(tools, "MCP tool registry should be importable in-repo")
        text = guide.render_guide()
        for spec in tools:
            self.assertIn(f"`{spec.name}`", text, f"tool {spec.name} missing from guide")

    def test_adding_a_flag_changes_output(self):
        before = guide.render_guide()
        patched = dict(it2agent_flag.KNOWN_FLAGS)
        patched["brand_new_capability"] = "A brand new made-up capability for the test."
        with mock.patch.object(it2agent_flag, "KNOWN_FLAGS", patched):
            after = guide.render_guide()
        self.assertNotEqual(before, after)
        self.assertIn("agent.brand_new_capability", after)
        self.assertNotIn("agent.brand_new_capability", before)

    def test_removing_a_flag_changes_output(self):
        before = guide.render_guide()
        patched = dict(it2agent_flag.KNOWN_FLAGS)
        removed = next(iter(patched))
        del patched[removed]
        with mock.patch.object(it2agent_flag, "KNOWN_FLAGS", patched):
            after = guide.render_guide()
        self.assertNotEqual(before, after)

    def test_adding_a_tool_changes_output(self):
        before = guide.render_guide()

        class _FakeSpec:
            name = "made_up_tool"
            description = "A made up tool for the test."
            input_schema = {"required": ["foo"]}

        with mock.patch.object(guide, "_load_tools", lambda: [_FakeSpec()]):
            after = guide.render_guide()
        self.assertNotEqual(before, after)
        self.assertIn("`made_up_tool`", after)


class TestNoDrift(unittest.TestCase):
    def test_committed_guide_matches_render(self):
        # The committed AGENT_GUIDE.md MUST equal a fresh render; otherwise the
        # schema/tools changed without regenerating. Fix: `it2agent guide`.
        committed = guide.GUIDE_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            guide.render_guide(),
            "AGENT_GUIDE.md is stale — run `it2agent guide` to regenerate.",
        )

    def test_guide_check_command_passes(self):
        self.assertEqual(guide.main(["guide", "--check"]), 0)


class TestRenderBrief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.toml"
        self.env = mock.patch.dict(
            os.environ, {"IT2AGENT_CONFIG": str(self.cfg)}, clear=False
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_brief_none_enabled(self):
        brief = guide.render_brief()
        self.assertIn("none enabled", brief)
        self.assertIn("it2agent help", brief)
        # Even with nothing on, it points at the MCP tools + how to enable.
        self.assertIn("agent.mcp", brief)

    def test_brief_reflects_enabled_flags(self):
        self.cfg.write_text(
            '[features]\n"agent.mcp" = true\n"agent.broker" = true\n', encoding="utf-8"
        )
        brief = guide.render_brief()
        self.assertIn("2 enabled", brief)
        self.assertIn("agent.mcp", brief)
        self.assertIn("agent.broker", brief)
        # A flag that is OFF must not be listed as active.
        self.assertNotIn("agent.janitor", brief)
        # Points at the full guide and lists MCP tool names.
        self.assertIn("it2agent help", brief)
        self.assertIn("spawn", brief)


if __name__ == "__main__":
    unittest.main(verbosity=2)
