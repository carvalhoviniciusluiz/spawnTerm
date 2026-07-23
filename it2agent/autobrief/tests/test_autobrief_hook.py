#!/usr/bin/env python3
"""Tests for the SessionStart autobrief hook (#113).

Pins the observer contract and the project-local install mechanism:
  * event path ALWAYS exits 0; stdout is the additionalContext JSON ONLY when the
    gate is open (flag ON / --no-gate), and EMPTY otherwise (flag OFF, bad stdin,
    unknown event),
  * flag OFF ⇒ no additionalContext; flag ON ⇒ the rendered brief is injected,
  * install/uninstall into a TEMP settings file (deep-merge preserves other keys,
    gitignore ensured, uninstall removes only ours), never touching a real
    ~/.claude.

Run: python3 it2agent/autobrief/tests/test_autobrief_hook.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_AUTOBRIEF_DIR = _HERE.parent
if str(_AUTOBRIEF_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOBRIEF_DIR))

import it2agent_autobrief_hook as hook  # noqa: E402

_STDIN = '{"source":"startup","cwd":"/tmp/x","hook_event_name":"SessionStart"}'


class _CaptureStdout:
    """Context manager capturing sys.stdout into a StringIO."""

    def __enter__(self):
        self._old = sys.stdout
        self.buf = io.StringIO()
        sys.stdout = self.buf
        return self

    def __exit__(self, *exc):
        sys.stdout = self._old
        return False


# --------------------------------------------------------------------------- #
# Event path: gate + always-exit-0 + stdout discipline.
# --------------------------------------------------------------------------- #


class TestEventPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.toml"
        self.env = mock.patch.dict(
            os.environ, {"IT2AGENT_CONFIG": str(self.cfg)}, clear=False
        )
        self.env.start()
        os.environ.pop("IT2AGENT_FORCE", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _enable(self):
        self.cfg.write_text('[features]\n"agent.autobrief" = true\n', encoding="utf-8")

    def test_flag_off_no_output_exit_0(self):
        # No config at all ⇒ flag OFF ⇒ silent no-op.
        with _CaptureStdout() as cap:
            rc = hook.run_event("session-start", _STDIN)
        self.assertEqual(rc, 0)
        self.assertEqual(cap.buf.getvalue(), "")

    def test_flag_explicit_false_no_output(self):
        self.cfg.write_text('[features]\n"agent.autobrief" = false\n', encoding="utf-8")
        with _CaptureStdout() as cap:
            rc = hook.run_event("session-start", _STDIN)
        self.assertEqual(rc, 0)
        self.assertEqual(cap.buf.getvalue(), "")

    def test_flag_on_emits_brief(self):
        self._enable()
        with _CaptureStdout() as cap:
            rc = hook.run_event("session-start", _STDIN)
        self.assertEqual(rc, 0)
        out = cap.buf.getvalue()
        self.assertTrue(out)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "SessionStart"
        )
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("it2agent", ctx)
        self.assertIn("it2agent help", ctx)

    def test_no_gate_bypasses_flag(self):
        # Flag OFF, but --no-gate forces emission (local testing path).
        with _CaptureStdout() as cap:
            rc = hook.run_event("session-start", _STDIN, no_gate=True)
        self.assertEqual(rc, 0)
        self.assertTrue(cap.buf.getvalue())

    def test_force_env_bypasses_flag(self):
        os.environ["IT2AGENT_FORCE"] = "1"
        try:
            with _CaptureStdout() as cap:
                rc = hook.run_event("session-start", _STDIN)
        finally:
            os.environ.pop("IT2AGENT_FORCE", None)
        self.assertEqual(rc, 0)
        self.assertTrue(cap.buf.getvalue())

    def test_unknown_event_silent_even_when_enabled(self):
        self._enable()
        with _CaptureStdout() as cap:
            rc = hook.run_event("PreToolUse", _STDIN)
        self.assertEqual(rc, 0)
        self.assertEqual(cap.buf.getvalue(), "")

    def test_empty_stdin_still_emits_when_enabled(self):
        # stdin fields are not required; empty stdin is fine, still exit 0 + emit.
        self._enable()
        with _CaptureStdout() as cap:
            rc = hook.run_event("session-start", "")
        self.assertEqual(rc, 0)
        self.assertTrue(cap.buf.getvalue())

    def test_malformed_stdin_still_emits_when_enabled(self):
        self._enable()
        with _CaptureStdout() as cap:
            rc = hook.run_event("session-start", "not json {{{")
        self.assertEqual(rc, 0)
        self.assertTrue(cap.buf.getvalue())

    def test_render_failure_is_silent_exit_0(self):
        self._enable()
        with mock.patch("it2agent_guide.render_brief", side_effect=RuntimeError("boom")):
            with _CaptureStdout() as cap:
                rc = hook.run_event("session-start", _STDIN)
        self.assertEqual(rc, 0)
        self.assertEqual(cap.buf.getvalue(), "")

    def test_event_name_alias_accepted(self):
        self._enable()
        with _CaptureStdout() as cap:
            rc = hook.run_event("SessionStart", _STDIN)
        self.assertEqual(rc, 0)
        self.assertTrue(cap.buf.getvalue())


class TestBuildOutput(unittest.TestCase):
    def test_shape(self):
        out = hook.build_output("hello")
        self.assertEqual(
            out,
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "hello",
                }
            },
        )


# --------------------------------------------------------------------------- #
# install / uninstall / status — user scope via override, and project scope in a
# TEMP git repo. Never touches a real ~/.claude.
# --------------------------------------------------------------------------- #


class TestInstallUserScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Path(self.tmp.name) / "settings.json"
        self.env = mock.patch.dict(
            os.environ, {"IT2AGENT_CLAUDE_SETTINGS": str(self.settings)}, clear=False
        )
        self.env.start()
        self._old = sys.stdout
        sys.stdout = io.StringIO()

    def tearDown(self):
        sys.stdout = self._old
        self.env.stop()
        self.tmp.cleanup()

    def _read(self):
        return json.loads(self.settings.read_text())

    def test_install_into_missing_file(self):
        self.assertEqual(hook.cmd_install(), 0)
        data = self._read()
        self.assertEqual(set(data["hooks"].keys()), {"SessionStart"})
        cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(cmd.endswith("it2agent-autobrief-hook session-start"))

    def test_install_preserves_unrelated_keys(self):
        pre = {
            "model": "opus",
            "hooks": {
                "PreToolUse": [{"hooks": [{"type": "command", "command": "/x guard"}]}],
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "/other/tool run"}]}
                ],
            },
        }
        self.settings.write_text(json.dumps(pre))
        hook.cmd_install()
        data = self._read()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["PreToolUse"], pre["hooks"]["PreToolUse"])
        cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertIn("/other/tool run", cmds)
        self.assertTrue(any(c.endswith("it2agent-autobrief-hook session-start") for c in cmds))

    def test_install_idempotent(self):
        hook.cmd_install()
        first = self._read()
        hook.cmd_install()
        self.assertEqual(first, self._read())

    def test_uninstall_removes_only_ours(self):
        pre = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "/other/tool run"}]}
                ]
            }
        }
        self.settings.write_text(json.dumps(pre))
        hook.cmd_install()
        hook.cmd_uninstall()
        data = self._read()
        cmds = [
            h["command"]
            for g in data.get("hooks", {}).get("SessionStart", [])
            for h in g["hooks"]
        ]
        self.assertEqual(cmds, ["/other/tool run"])

    def test_uninstall_clean_prunes_hooks_key(self):
        hook.cmd_install()
        hook.cmd_uninstall()
        self.assertNotIn("hooks", self._read())


class TestInstallProjectScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "proj"
        (self.root / "sub").mkdir(parents=True)
        (self.root / ".git").mkdir()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("IT2AGENT_CLAUDE_SETTINGS", None)
        self._old = sys.stdout
        sys.stdout = io.StringIO()

    def tearDown(self):
        sys.stdout = self._old
        self.env.stop()
        self.tmp.cleanup()

    @property
    def settings(self):
        return self.root / ".claude" / "settings.local.json"

    @property
    def gitignore(self):
        return self.root / ".gitignore"

    def test_install_creates_local_settings_and_gitignore(self):
        rc = hook.cmd_install("project", start_dir=self.root / "sub")
        self.assertEqual(rc, 0)
        self.assertTrue(self.settings.is_file())
        data = json.loads(self.settings.read_text())
        self.assertEqual(set(data["hooks"]), {"SessionStart"})
        self.assertTrue(self.gitignore.is_file())
        self.assertIn(
            ".claude/settings.local.json", self.gitignore.read_text().splitlines()
        )

    def test_gitignore_idempotent_and_preserves_existing(self):
        self.gitignore.write_text("node_modules\n", encoding="utf-8")
        hook.cmd_install("project", start_dir=self.root)
        hook.cmd_install("project", start_dir=self.root)
        lines = self.gitignore.read_text().splitlines()
        self.assertEqual(lines.count(".claude/settings.local.json"), 1)
        self.assertIn("node_modules", lines)

    def test_status_reflects_state(self):
        self.assertEqual(hook.cmd_status("project", start_dir=self.root), 1)
        hook.cmd_install("project", start_dir=self.root)
        self.assertEqual(hook.cmd_status("project", start_dir=self.root), 0)
        hook.cmd_uninstall("project", start_dir=self.root)
        self.assertEqual(hook.cmd_status("project", start_dir=self.root), 1)

    def test_non_repo_errors_nonzero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as outside:
            nonrepo = Path(outside)
            self.assertEqual(hook.cmd_install("project", start_dir=nonrepo), 2)
            self.assertEqual(hook.cmd_uninstall("project", start_dir=nonrepo), 2)
            self.assertEqual(hook.cmd_status("project", start_dir=nonrepo), 2)
            self.assertFalse((nonrepo / ".claude").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
