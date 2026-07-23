#!/usr/bin/env python3
"""Unit tests for the it2agent team-bridge observer hook (#92).

No live broker, no Claude Code. The broker is a recording fake; the flag gate is
driven by ``IT2AGENT_FORCE`` / a temp config; the settings file is a temp path
via ``IT2AGENT_CLAUDE_SETTINGS`` (the real ~/.claude/settings.json is NEVER
touched). Mirrors the style of ``daemon/tests/test_bridge.py``.

Exercised:
  * pure event→op mapping for each event, with defensive task-field extraction
    (documented task:{id,title}, flat task_id/title, and missing task fields);
  * team-key derivation from session_id, including short/edge ids;
  * ALWAYS exit 0 + no stdout: flag OFF, broker connect raises, empty stdin,
    non-JSON stdin, unknown event;
  * install/uninstall against a TEMP settings file: empty file, pre-existing
    unrelated hooks preserved (deep-merge), uninstall removes only ours,
    idempotent.
  * per-project scope (#96): --scope project into a temp git repo creates the
    gitignored settings.local.json, ensures the .gitignore entry (idempotent,
    preserves existing), uninstall removes only ours, status reports state, and
    a non-repo cwd errors non-zero;
  * the #96 gate change: installed ⇒ runs by default; an EXPLICIT false flag is
    the only kill-switch; unset/absent/true all run; --no-gate / IT2AGENT_FORCE
    override; always exit 0.
"""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import it2agent_team_hook as hook  # noqa: E402
import gate  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures.
# --------------------------------------------------------------------------- #

SID = "abcdef1234567890"  # first 8 chars -> "abcdef12"
KEY = "team:session-abcdef12"

IDLE_PAYLOAD = {
    "session_id": SID,
    "transcript_path": "/tmp/t.jsonl",
    "cwd": "/repo",
    "hook_event_name": "TeammateIdle",
    "agent_id": "teammate-7",
    "agent_type": "backend",
}

# Documented nested task shape.
CREATED_NESTED = {
    "session_id": SID,
    "transcript_path": "/tmp/t.jsonl",
    "hook_event_name": "TaskCreated",
    "task": {"id": "T1", "title": "Wire the broker", "description": "do it"},
}

# Flat task_id / title (undocumented alternative spelling).
CREATED_FLAT = {
    "session_id": SID,
    "transcript_path": "/tmp/t.jsonl",
    "hook_event_name": "TaskCreated",
    "task_id": "T2",
    "title": "Flat titled task",
}

# Missing task fields entirely -> safe fallback.
CREATED_BARE = {
    "session_id": SID,
    "transcript_path": "/tmp/t.jsonl",
    "hook_event_name": "TaskCreated",
}

COMPLETED_NESTED = {
    "session_id": SID,
    "transcript_path": "/tmp/t.jsonl",
    "hook_event_name": "TaskCompleted",
    "task": {"id": "T1", "title": "Wire the broker"},
}


class FakeBroker:
    """Records every op; optionally raises to simulate a down/unreachable broker."""

    def __init__(self, *, raise_all=False):
        self.requests = []
        self.raise_all = raise_all

    def request(self, message):
        if self.raise_all:
            raise OSError("connection refused")
        self.requests.append(message)
        return {"ok": True}


class SendCapture:
    """Context manager: capture stdout, force the gate open, and stub the broker.

    Returns the FakeBroker so a test can assert the exact ops sent. Restores
    stdout and env afterwards.
    """

    def __init__(self, broker):
        self.broker = broker
        self._stdout = None
        self._patch = None

    def __enter__(self):
        self._stdout = io.StringIO()
        sys.stdout = self._stdout
        self._patch = mock.patch.object(
            hook, "_send_to_broker", side_effect=self._send
        )
        self._patch.start()
        return self.broker

    def _send(self, ops):
        for op in ops:
            self.broker.request(op)

    def __exit__(self, *exc):
        self._patch.stop()
        sys.stdout = sys.__stdout__
        return False

    @property
    def stdout(self):
        return self._stdout.getvalue()


# --------------------------------------------------------------------------- #
# Pure mapping + derivation.
# --------------------------------------------------------------------------- #


class TestTeamKey(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(hook.team_key(SID), KEY)

    def test_short_id(self):
        self.assertEqual(hook.team_key("abc"), "team:session-abc")

    def test_empty(self):
        self.assertEqual(hook.team_key(""), "team:session-")

    def test_non_string(self):
        self.assertEqual(hook.team_key(None), "team:session-")
        self.assertEqual(hook.team_key(12345678), "team:session-")

    def test_exactly_eight(self):
        self.assertEqual(hook.team_key("abcdefgh"), "team:session-abcdefgh")


class TestNormalizeEvent(unittest.TestCase):
    def test_short_verbs(self):
        self.assertEqual(hook.normalize_event("created"), "created")
        self.assertEqual(hook.normalize_event("completed"), "completed")
        self.assertEqual(hook.normalize_event("idle"), "idle")

    def test_hook_event_names(self):
        self.assertEqual(hook.normalize_event("TaskCreated"), "created")
        self.assertEqual(hook.normalize_event("TaskCompleted"), "completed")
        self.assertEqual(hook.normalize_event("TeammateIdle"), "idle")

    def test_case_insensitive(self):
        self.assertEqual(hook.normalize_event("taskCREATED"), "created")

    def test_unknown(self):
        self.assertIsNone(hook.normalize_event("Nope"))
        self.assertIsNone(hook.normalize_event(""))


class TestExtractTask(unittest.TestCase):
    def test_nested(self):
        t = hook.extract_task(CREATED_NESTED)
        self.assertEqual(t, {"id": "T1", "title": "Wire the broker", "description": "do it"})

    def test_flat(self):
        t = hook.extract_task(CREATED_FLAT)
        self.assertEqual(t, {"id": "T2", "title": "Flat titled task", "description": None})

    def test_bare_fallback(self):
        t = hook.extract_task(CREATED_BARE)
        self.assertEqual(t, {"id": "unknown", "title": None, "description": None})

    def test_top_level_id(self):
        t = hook.extract_task({"id": "T9"})
        self.assertEqual(t["id"], "T9")


class TestBuildOps(unittest.TestCase):
    def test_idle_register(self):
        ops = hook.build_ops("idle", IDLE_PAYLOAD)
        self.assertEqual(
            ops,
            [
                {
                    "op": "register",
                    "session_id": "teammate-7",
                    "alive": True,
                    "capabilities": ["claude-code-teammate", KEY],
                    "role": "backend",
                }
            ],
        )

    def test_idle_missing_agent_id_uses_team_key(self):
        payload = {"session_id": SID, "agent_type": "frontend"}
        ops = hook.build_ops("TeammateIdle", payload)
        self.assertEqual(ops[0]["session_id"], KEY)
        self.assertEqual(ops[0]["role"], "frontend")

    def test_idle_missing_role_omitted(self):
        ops = hook.build_ops("idle", {"session_id": SID, "agent_id": "a1"})
        self.assertNotIn("role", ops[0])

    def test_created_nested(self):
        ops = hook.build_ops("TaskCreated", CREATED_NESTED)
        self.assertEqual(
            ops,
            [
                {
                    "op": "handoff_put",
                    "agent_id": KEY,
                    "goal": "task:T1",
                    "verification_status": "pending",
                    "context_ptr": "/tmp/t.jsonl",
                    "owned_files": ["Wire the broker"],
                }
            ],
        )

    def test_created_flat(self):
        ops = hook.build_ops("created", CREATED_FLAT)
        self.assertEqual(ops[0]["goal"], "task:T2")
        self.assertEqual(ops[0]["owned_files"], ["Flat titled task"])
        self.assertEqual(ops[0]["verification_status"], "pending")

    def test_created_bare_fallback(self):
        ops = hook.build_ops("created", CREATED_BARE)
        self.assertEqual(ops[0]["goal"], "task:unknown")
        self.assertNotIn("owned_files", ops[0])  # no title -> omitted

    def test_completed_emits_handoff_and_send(self):
        ops = hook.build_ops("TaskCompleted", COMPLETED_NESTED)
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0]["op"], "handoff_put")
        self.assertEqual(ops[0]["verification_status"], "completed")
        self.assertEqual(ops[0]["goal"], "task:T1")
        self.assertEqual(
            ops[1],
            {"op": "send", "to": "lead", "from": KEY, "body": "task:T1 completed"},
        )

    def test_unknown_event_no_ops(self):
        self.assertEqual(hook.build_ops("Nope", CREATED_NESTED), [])


# --------------------------------------------------------------------------- #
# run_event: gate open, broker receives the exact ops, exit 0, no stdout.
# --------------------------------------------------------------------------- #


class TestRunEventForced(unittest.TestCase):
    def test_idle_forced_sends_register(self):
        broker = FakeBroker()
        with SendCapture(broker) as b:
            rc = hook.run_event("idle", json.dumps(IDLE_PAYLOAD), no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(b.requests[0]["op"], "register")

    def test_completed_forced_sends_two_ops(self):
        broker = FakeBroker()
        with SendCapture(broker) as b:
            rc = hook.run_event("completed", json.dumps(COMPLETED_NESTED), no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual([r["op"] for r in b.requests], ["handoff_put", "send"])


# --------------------------------------------------------------------------- #
# ALWAYS exit 0 + no stdout, under every failure condition.
# --------------------------------------------------------------------------- #


class TestAlwaysExitZero(unittest.TestCase):
    def _run(self, event, raw, *, no_gate, env=None):
        """Run run_event capturing stdout; return (rc, stdout)."""
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        env_patch = mock.patch.dict(os.environ, env or {}, clear=False)
        env_patch.start()
        try:
            rc = hook.run_event(event, raw, no_gate=no_gate)
        finally:
            sys.stdout = old
            env_patch.stop()
        return rc, buf.getvalue()

    def test_broker_connect_raises_exit0(self):
        # Force gate open; _send_to_broker uses the real BrokerClient path but
        # we stub it to raise, simulating a down broker.
        with mock.patch.object(hook, "_send_to_broker", side_effect=OSError("down")):
            rc, out = self._run("idle", json.dumps(IDLE_PAYLOAD), no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_empty_stdin_exit0(self):
        with mock.patch.object(hook, "_send_to_broker") as send:
            rc, out = self._run("created", "", no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        send.assert_not_called()

    def test_non_json_stdin_exit0(self):
        with mock.patch.object(hook, "_send_to_broker") as send:
            rc, out = self._run("created", "this is not json {", no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        send.assert_not_called()

    def test_json_not_object_exit0(self):
        with mock.patch.object(hook, "_send_to_broker") as send:
            rc, out = self._run("created", "[1,2,3]", no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        send.assert_not_called()

    def test_unknown_event_exit0(self):
        with mock.patch.object(hook, "_send_to_broker") as send:
            rc, out = self._run("Nope", json.dumps(IDLE_PAYLOAD), no_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        send.assert_not_called()

    def test_main_no_args_exit0_no_stdout(self):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = hook.main([])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")  # usage goes to stderr, not stdout


# --------------------------------------------------------------------------- #
# install / uninstall against a TEMP settings file.
# --------------------------------------------------------------------------- #


class TestInstallUninstall(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Path(self.tmp.name) / "settings.json"
        self.env = mock.patch.dict(
            os.environ, {"IT2AGENT_CLAUDE_SETTINGS": str(self.settings)}, clear=False
        )
        self.env.start()
        self._stdout = io.StringIO()
        self._old = sys.stdout
        sys.stdout = self._stdout

    def tearDown(self):
        sys.stdout = self._old
        self.env.stop()
        self.tmp.cleanup()

    def _read(self):
        return json.loads(self.settings.read_text())

    def test_install_into_missing_file(self):
        rc = hook.cmd_install()
        self.assertEqual(rc, 0)
        data = self._read()
        self.assertEqual(set(data["hooks"].keys()), {"TaskCreated", "TaskCompleted", "TeammateIdle"})
        cmd = data["hooks"]["TaskCreated"][0]["hooks"][0]["command"]
        self.assertTrue(cmd.endswith("it2agent-team-hook created"))

    def test_install_does_not_set_experimental_env(self):
        hook.cmd_install()
        data = self._read()
        # We must NOT flip CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS for the operator.
        self.assertNotIn("env", data)

    def test_install_preserves_unrelated_hooks(self):
        pre = {
            "model": "opus",
            "hooks": {
                "TaskCreated": [
                    {"hooks": [{"type": "command", "command": "/other/tool run"}]}
                ],
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "/some/guard"}]}
                ],
            },
        }
        self.settings.write_text(json.dumps(pre))
        hook.cmd_install()
        data = self._read()
        # Unrelated top-level key preserved.
        self.assertEqual(data["model"], "opus")
        # Unrelated event preserved untouched.
        self.assertEqual(data["hooks"]["PreToolUse"], pre["hooks"]["PreToolUse"])
        # Pre-existing TaskCreated group preserved AND ours appended.
        cmds = [
            h["command"]
            for g in data["hooks"]["TaskCreated"]
            for h in g["hooks"]
        ]
        self.assertIn("/other/tool run", cmds)
        self.assertTrue(any(c.endswith("it2agent-team-hook created") for c in cmds))

    def test_install_idempotent(self):
        hook.cmd_install()
        first = self._read()
        hook.cmd_install()
        second = self._read()
        self.assertEqual(first, second)  # no duplicate entries on re-install

    def test_uninstall_removes_only_ours(self):
        pre = {
            "hooks": {
                "TaskCreated": [
                    {"hooks": [{"type": "command", "command": "/other/tool run"}]}
                ],
            }
        }
        self.settings.write_text(json.dumps(pre))
        hook.cmd_install()
        hook.cmd_uninstall()
        data = self._read()
        # Our three additions gone; the unrelated /other/tool survives.
        cmds = [
            h["command"]
            for g in data.get("hooks", {}).get("TaskCreated", [])
            for h in g["hooks"]
        ]
        self.assertEqual(cmds, ["/other/tool run"])
        self.assertNotIn("TaskCompleted", data.get("hooks", {}))
        self.assertNotIn("TeammateIdle", data.get("hooks", {}))

    def test_uninstall_from_clean_file_leaves_no_hooks_key(self):
        hook.cmd_install()
        hook.cmd_uninstall()
        data = self._read()
        # Nothing else was in the file, so hooks is pruned entirely.
        self.assertNotIn("hooks", data)

    def test_uninstall_idempotent(self):
        hook.cmd_install()
        hook.cmd_uninstall()
        first = self._read()
        hook.cmd_uninstall()
        second = self._read()
        self.assertEqual(first, second)


# --------------------------------------------------------------------------- #
# Per-project scope (#96): install/uninstall/status into a TEMP git repo.
# --------------------------------------------------------------------------- #


class TestScopeProject(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        # A fake git repo: a bare `.git` dir is enough for the root walk (no git
        # binary needed). `sub/` exercises resolution from a nested cwd.
        self.root = Path(self.tmp.name) / "proj"
        (self.root / "sub").mkdir(parents=True)
        (self.root / ".git").mkdir()
        # The escape hatch must be UNSET so real git-root resolution runs.
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("IT2AGENT_CLAUDE_SETTINGS", None)
        self._stdout = io.StringIO()
        self._old = sys.stdout
        sys.stdout = self._stdout

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
        # Resolve from a nested subdir to prove the root walk works.
        rc = hook.cmd_install("project", start_dir=self.root / "sub")
        self.assertEqual(rc, 0)
        self.assertTrue(self.settings.is_file())
        data = json.loads(self.settings.read_text())
        self.assertEqual(
            set(data["hooks"]), {"TaskCreated", "TaskCompleted", "TeammateIdle"}
        )
        # settings.local.json is now gitignored so it is never committed.
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
        self.assertIn("node_modules", lines)  # pre-existing content preserved

    def test_gitignore_not_appended_when_already_covered(self):
        # A broader pattern already ignores it -> we must not add a redundant line.
        self.gitignore.write_text(".claude/\n", encoding="utf-8")
        hook.cmd_install("project", start_dir=self.root)
        self.assertNotIn(
            ".claude/settings.local.json", self.gitignore.read_text().splitlines()
        )

    def test_uninstall_removes_only_ours(self):
        pre = {
            "hooks": {
                "TaskCreated": [
                    {"hooks": [{"type": "command", "command": "/other/tool run"}]}
                ]
            }
        }
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(pre))
        hook.cmd_install("project", start_dir=self.root)
        hook.cmd_uninstall("project", start_dir=self.root)
        data = json.loads(self.settings.read_text())
        cmds = [
            h["command"]
            for g in data.get("hooks", {}).get("TaskCreated", [])
            for h in g["hooks"]
        ]
        self.assertEqual(cmds, ["/other/tool run"])
        self.assertNotIn("TaskCompleted", data.get("hooks", {}))

    def test_status_reflects_install_state(self):
        self.assertEqual(hook.cmd_status("project", start_dir=self.root), 1)  # absent
        hook.cmd_install("project", start_dir=self.root)
        self.assertEqual(hook.cmd_status("project", start_dir=self.root), 0)  # present
        hook.cmd_uninstall("project", start_dir=self.root)
        self.assertEqual(hook.cmd_status("project", start_dir=self.root), 1)  # gone

    def test_status_prints_resolved_path(self):
        # Compare against settings_path()'s own resolution to be symlink-safe
        # (macOS temp dirs live under /var -> /private/var).
        expected = str(hook.settings_path("project", start_dir=self.root))
        self._stdout.truncate(0)
        self._stdout.seek(0)
        hook.cmd_status("project", start_dir=self.root)
        self.assertIn(expected, self._stdout.getvalue().strip().splitlines())

    def test_non_repo_errors_nonzero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as outside:
            nonrepo = Path(outside)
            self.assertEqual(hook.cmd_install("project", start_dir=nonrepo), 2)
            self.assertEqual(hook.cmd_uninstall("project", start_dir=nonrepo), 2)
            self.assertEqual(hook.cmd_status("project", start_dir=nonrepo), 2)
            # And nothing was written.
            self.assertFalse((nonrepo / ".claude").exists())

    def test_user_scope_still_uses_override(self):
        # Backward-compat: default scope is user, and the override still wins so
        # existing callers are unaffected and never touch a real ~/.claude file.
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "settings.json"
            with mock.patch.dict(
                os.environ, {"IT2AGENT_CLAUDE_SETTINGS": str(target)}, clear=False
            ):
                self.assertEqual(hook.cmd_install(), 0)  # default scope == user
                self.assertTrue(target.is_file())
                # No .gitignore side effect for user scope.
                self.assertFalse((Path(d) / ".gitignore").exists())


# --------------------------------------------------------------------------- #
# Gate change (#96): installed ⇒ runs by default; EXPLICIT false is the only
# kill-switch; unset/absent/true run; overrides work; always exit 0.
# --------------------------------------------------------------------------- #


class TestGateSemantics(unittest.TestCase):
    def setUp(self):
        import tempfile

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

    def _write_flag(self, value):
        self.cfg.write_text(
            f'[features]\n"agent.team_bridge" = {value}\n', encoding="utf-8"
        )

    def test_absent_config_runs(self):
        self.assertFalse(self.cfg.exists())
        self.assertFalse(gate.team_bridge_kill_switched())
        self.assertTrue(gate.gate_open())

    def test_key_absent_from_table_runs(self):
        self.cfg.write_text('[features]\n"agent.messaging" = true\n', encoding="utf-8")
        self.assertFalse(gate.team_bridge_kill_switched())
        self.assertTrue(gate.gate_open())

    def test_explicit_true_runs(self):
        self._write_flag("true")
        self.assertFalse(gate.team_bridge_kill_switched())
        self.assertTrue(gate.gate_open())

    def test_explicit_false_is_kill_switch(self):
        self._write_flag("false")
        self.assertTrue(gate.team_bridge_kill_switched())
        self.assertFalse(gate.gate_open())

    def test_force_env_overrides_kill_switch(self):
        self._write_flag("false")
        with mock.patch.dict(os.environ, {"IT2AGENT_FORCE": "1"}, clear=False):
            self.assertTrue(gate.gate_open())

    def test_no_gate_overrides_kill_switch(self):
        self._write_flag("false")
        self.assertTrue(gate.gate_open(no_gate=True))

    def _run_capturing(self, event, raw):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = hook.run_event(event, raw, no_gate=False)
        finally:
            sys.stdout = old
        return rc, buf.getvalue()

    def test_run_event_runs_when_unset(self):
        with mock.patch.object(hook, "_send_to_broker") as send:
            rc, out = self._run_capturing("idle", json.dumps(IDLE_PAYLOAD))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")  # observer never writes stdout
        send.assert_called_once()

    def test_run_event_no_op_when_kill_switched(self):
        self._write_flag("false")
        with mock.patch.object(hook, "_send_to_broker") as send:
            rc, out = self._run_capturing("idle", json.dumps(IDLE_PAYLOAD))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
