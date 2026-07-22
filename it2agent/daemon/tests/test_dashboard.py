#!/usr/bin/env python3
"""Unit tests for the pure status-bar dashboard formatter (it2agent #29).

No ``iterm2`` dependency — proves the formatting core runs in plain CI. Covers:
the display string for each status (busy/blocked/done/idle), the color/glyph
mapping against the #8 Okabe-Ito palette, task truncation and omission, and the
missing-/unknown-vars fallback. Also asserts importing ``dashboard`` never pulls
in ``iterm2``.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard  # noqa: E402
from dashboard import (  # noqa: E402
    PALETTE,
    format_component,
    resolve_status,
    style_for_status,
)

# The canonical #8 lifecycle palette (it2agent/emit/docs/colors.md). The
# dashboard MUST reuse these exact hex values.
EXPECTED_COLORS = {
    "busy": "0072B2",
    "blocked": "E69F00",
    "done": "009E73",
    "idle": "999999",
}


class TestPurity(unittest.TestCase):
    def test_import_does_not_pull_in_iterm2(self):
        # Importing dashboard (done at top of file) must not import iterm2.
        self.assertIn("dashboard", sys.modules)
        self.assertNotIn("iterm2", sys.modules)


class TestPalette(unittest.TestCase):
    def test_colors_match_okabe_ito_palette(self):
        for status, hex_color in EXPECTED_COLORS.items():
            self.assertEqual(PALETTE[status].color, hex_color)

    def test_glyphs_are_distinct_single_chars(self):
        glyphs = [style.glyph for style in PALETTE.values()]
        # Each status has exactly one glyph and they are all distinct so the
        # state is legible without color (CVD / grayscale safety).
        self.assertEqual(len(glyphs), len(set(glyphs)))
        for glyph in glyphs:
            self.assertEqual(len(glyph), 1)

    def test_style_for_status_maps_each_status(self):
        for status, hex_color in EXPECTED_COLORS.items():
            style = style_for_status(status)
            self.assertEqual(style.status, status)
            self.assertEqual(style.color, hex_color)
            self.assertEqual(style.glyph, PALETTE[status].glyph)


class TestFormatPerStatus(unittest.TestCase):
    def test_busy(self):
        out = format_component("backend", "busy", None)
        self.assertEqual(out, "▶ backend: busy")

    def test_blocked(self):
        out = format_component("frontend", "blocked", None)
        self.assertEqual(out, "⚠ frontend: blocked")

    def test_done(self):
        out = format_component("qa", "done", None)
        self.assertEqual(out, "✓ qa: done")

    def test_idle(self):
        out = format_component("backend", "idle", None)
        self.assertEqual(out, "○ backend: idle")

    def test_status_is_case_insensitive(self):
        self.assertEqual(format_component("backend", "BUSY", None), "▶ backend: busy")


class TestTask(unittest.TestCase):
    def test_task_appended_when_it_fits(self):
        out = format_component("be", "busy", "build #29")
        self.assertEqual(out, "▶ be: busy — build #29")

    def test_task_truncated_with_ellipsis_when_too_long(self):
        long_task = "refactor the entire authentication subsystem end to end"
        out = format_component("backend", "busy", long_task, max_length=40)
        self.assertLessEqual(len(out), 40)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(out.startswith("▶ backend: busy — "))

    def test_task_omitted_when_no_room(self):
        # A long role/status leaves no budget for even a sliver of task.
        out = format_component("a-very-long-role-name-here", "blocked", "task", max_length=20)
        self.assertEqual(out, "⚠ a-very-long-role-name-here: blocked")

    def test_empty_task_is_omitted(self):
        self.assertEqual(format_component("be", "done", ""), "✓ be: done")
        self.assertEqual(format_component("be", "done", "   "), "✓ be: done")


class TestFallback(unittest.TestCase):
    def test_missing_role_degrades_to_agent(self):
        self.assertEqual(format_component(None, "busy", None), "▶ agent: busy")
        self.assertEqual(format_component("", "busy", None), "▶ agent: busy")

    def test_missing_status_degrades_to_idle(self):
        self.assertEqual(format_component("backend", None, None), "○ backend: idle")
        self.assertEqual(format_component("backend", "", None), "○ backend: idle")

    def test_unknown_status_degrades_to_idle(self):
        self.assertEqual(resolve_status("thinking"), "idle")
        self.assertEqual(format_component("backend", "thinking", None), "○ backend: idle")

    def test_all_missing_degrades_fully(self):
        self.assertEqual(format_component(None, None, None), "○ agent: idle")

    def test_idle_flag_accepted_and_defaults_to_idle(self):
        # An explicit status still wins over the idle flag.
        self.assertEqual(resolve_status("busy", idle=True), "busy")
        # With no status, the idle flag is consistent with the idle fallback.
        self.assertEqual(resolve_status("", idle=True), "idle")
        self.assertEqual(format_component(None, None, None, idle=True), "○ agent: idle")


if __name__ == "__main__":
    unittest.main()
