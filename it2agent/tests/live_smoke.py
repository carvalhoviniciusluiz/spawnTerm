#!/usr/bin/env python3
"""it2agent live smoke harness — ONE command to re-validate the iTerm2-API /
AppleScript layer where the live-only bugs hid (#74/#81/#85/#76). Issue #124,
part of the go-live checklist (#1).

WHY THIS EXISTS
---------------
Four of seven production bugs only appeared LIVE, in the layer that talks to the
running iTerm2 (Python API / AppleScript) — a layer CI never exercises because it
needs a real iTerm2 with the Python API on. This harness is the #1 confidence
lever from the production-readiness review: a single, idempotent, self-cleaning
entrypoint that drives that live layer and ASSERTS PASS/FAIL per surface, so the
operator can re-validate in one shot instead of running a prompt by hand.

It reuses the existing drivers (it never re-implements them):
  * daemon spawn      -> it2agent/daemon/it2agent_daemon.py  (the #81/#85 surface)
  * tmux -CC probe    -> it2agent/tmux/validate_api_over_tmux.py (surfaces 2/4/5)
  * MCP orchestration -> it2agent/mcp/it2agent_mcp.py + broker (launched=true)
  * native OSC 21337  -> it2agent/emit/it2agent_emit.py (ccstatus bytes)

THE SURFACES (each prints PASS / FAIL / SKIP + evidence)
--------------------------------------------------------
  spawn    daemon `spawn --dir <temp git repo>` opens a real tab; assert the
           tab's REAL cwd via `lsof -p <pid> -d cwd` == the repo (the #85 bug:
           a command override defeated the profile's custom dir) AND the identity
           user-vars (user.agent_role / user.agent_status) are set (read back
           over the Python API). The harness closes ONLY that tab afterwards.
  tmux     spawn a `tmux -CC` session in a temp repo, then run
           validate_api_over_tmux.py against it (correct --session matcher,
           post-#82) and assert surfaces 2 (custom escape seq), 4 (screen read),
           5 (set/get user var) PASS. Kills the tmux session afterwards.
  mcp      drive the MCP `spawn` tool with the API on and assert `launch.launched
           == true` (a REAL tab opened, not just the registry side effect the
           headless coop_mcp_orchestrate.py proves). Closes that tab.
  ccstatus emit `ccstatus busy --detail x` (and `clear`) and assert the EXACT
           OSC 21337 bytes. This surface needs NO live API — it is the one green
           check even headless. The Cockpit *visual* is out of scope (operator's
           eye, marked with the eye glyph in the docs).

RUN IT (the ONE command — operator, on the Mac, in an iTerm2 3.7.dev tab)
-------------------------------------------------------------------------
  python3 it2agent/tests/live_smoke.py

Scope to one surface:            python3 it2agent/tests/live_smoke.py --only spawn
Machine-readable (future CI):    python3 it2agent/tests/live_smoke.py --json
Tune the tmux name matcher:      python3 it2agent/tests/live_smoke.py --only tmux --tmux-matcher <substr>

PREFLIGHT — it fails EARLY and CLEAR, and never fakes
-----------------------------------------------------
Before the live surfaces it checks the same four things as
COOPERATION_VALIDATION_PROMPT.md §0.5: build is 3.7.dev (TERM_PROGRAM_VERSION),
`import iterm2` works, the API server is on (`defaults read com.googlecode.iterm2
EnableAPIServer` == 1), and (informational) the API socket exists. If any live
prerequisite is missing, the live surfaces are marked SKIP with a clear reason
and the harness exits NONZERO — it does NOT fabricate a live PASS. The ccstatus
surface still runs (it needs none of that). The live surfaces are therefore RED
until this is run on a real iTerm2 3.7.dev with the Python API enabled; do not
paste a live PASS you did not obtain from a real run.

ISOLATION + CLEANUP
-------------------
Everything transient lives under an isolated IT2AGENT_CONFIG and a SHORT
`/tmp/it2s.<pid>` dir (the unix socket path limit is ~104 bytes, so the macOS
mktemp under /var/folders overflows). The harness NEVER touches the real
~/.config/it2agent or ~/.claude. On exit — including on failure or interrupt via
try/finally — it: closes ONLY the tabs it opened (tracked by unique agent_id;
never the operator's tab), kills any tmux session / broker it started, and
removes the temp git repos/worktrees + isolated config it created. Idempotent.

WHAT IS UNIT-TESTED (headless) vs. LIVE
---------------------------------------
The decision logic — preflight gating, --only selection, command construction,
the tmux-output parser, the --json shape, exit-code logic, and the cleanup
routine (it really creates + removes a temp repo/worktree) — is covered by
it2agent/tests/test_live_smoke.py and runs with no iTerm2. The live surfaces
themselves are intentionally not unit-tested: they need the app.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ST = HERE.parent

DAEMON_CLI = ST / "daemon" / "it2agent_daemon.py"
EMIT_CLI = ST / "emit" / "it2agent_emit.py"
VALIDATE_CLI = ST / "tmux" / "validate_api_over_tmux.py"
TMUX_CLI = ST / "tmux" / "it2agent-tmux"
BROKER_CLI = ST / "broker" / "it2agent_broker.py"
MCP_CLI = ST / "mcp" / "it2agent_mcp.py"

# The API user-var iTerm2 exposes for a session's child pid, and the dot-free
# identity vars the spawn plan stamps (see daemon/spawn.py + registry).
PID_VAR = "pid"
AGENT_ID_VAR = "user.agent_id"
AGENT_ROLE_VAR = "user.agent_role"
AGENT_STATUS_VAR = "user.agent_status"

# Golden OSC 21337 bytes (native tab status channel). These are the byte-for-byte
# sequences documented in COOPERATION_VALIDATION_PROMPT.md AC1 and produced by
# emit/it2agent_emit.py build_ccstatus. Hard-coding the golden makes this surface
# a real regression guard on the emitter, not a tautology.
ESC = "\033"
BEL = "\007"
GOLDEN_CCSTATUS_BUSY = f"{ESC}]21337;status=Busy;indicator=#0072B2;detail=x{BEL}"
GOLDEN_CCSTATUS_CLEAR = f"{ESC}]21337;status=;indicator=;detail={BEL}"

# The iTerm2 API unix-domain socket (informational preflight signal only; the
# authoritative check is a real connect inside each live surface).
API_SOCKET_PATH = os.path.expanduser(
    "~/Library/Application Support/iTerm2/private/socket"
)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# Surface order is the run order. `requires_live` gates on the preflight; only
# ccstatus runs with no live iTerm2.
SURFACE_ORDER = ["spawn", "tmux", "mcp", "ccstatus"]
SURFACE_META = {
    "spawn": {"requires_live": True, "desc": "daemon spawn: real cwd (lsof) + identity user-vars (#81/#85)"},
    "tmux": {"requires_live": True, "desc": "tmux -CC: Python API surfaces 2/4/5 still work"},
    "mcp": {"requires_live": True, "desc": "MCP spawn tool: launched=true (a real tab opened)"},
    "ccstatus": {"requires_live": False, "desc": "native OSC 21337 ccstatus bytes (exact)"},
}


# --------------------------------------------------------------------------- #
# Results + JSON shape (pure)
# --------------------------------------------------------------------------- #
@dataclass
class SurfaceResult:
    name: str
    status: str  # PASS / FAIL / SKIP
    evidence: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "surface": self.name,
            "status": self.status,
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


@dataclass
class Preflight:
    version: str
    is_37: bool
    iterm2_importable: bool
    api_enabled: bool
    socket_present: bool

    @property
    def live_ok(self) -> bool:
        """True iff the live surfaces can run: 3.7.dev build + iterm2 module +
        the API server enabled. The socket is informational only."""
        return self.is_37 and self.iterm2_importable and self.api_enabled

    def missing(self) -> list[str]:
        gaps = []
        if not self.is_37:
            gaps.append(f"build is not 3.7.dev (TERM_PROGRAM_VERSION={self.version!r})")
        if not self.iterm2_importable:
            gaps.append("the 'iterm2' Python package is not importable (pip3 install iterm2)")
        if not self.api_enabled:
            gaps.append("the Python API server is OFF (Settings > General > Magic > "
                        "Enable Python API; defaults read com.googlecode.iterm2 "
                        "EnableAPIServer must be 1)")
        return gaps

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "is_37": self.is_37,
            "iterm2_importable": self.iterm2_importable,
            "api_enabled": self.api_enabled,
            "socket_present": self.socket_present,
            "live_ok": self.live_ok,
            "missing": self.missing(),
        }


def detect_preflight(
    *,
    env: dict | None = None,
    iterm2_importable=None,
    api_reader=None,
    socket_present=None,
) -> Preflight:
    """Compute the preflight gate. PURE + injectable for headless tests.

    Every source of live state is injected so the tests can drive each missing
    permutation without a real iTerm2:
      * ``env``               -> reads TERM_PROGRAM_VERSION.
      * ``iterm2_importable`` -> bool or a 0-arg callable; default: try import.
      * ``api_reader``        -> 0-arg callable returning the EnableAPIServer
                                 string; default: shells out to `defaults`.
      * ``socket_present``    -> bool or 0-arg callable; default: os.path.exists.
    """
    env = os.environ if env is None else env
    version = env.get("TERM_PROGRAM_VERSION", "")
    is_37 = version.startswith("3.7")

    if iterm2_importable is None:
        iterm2_importable = _default_iterm2_importable
    importable = iterm2_importable() if callable(iterm2_importable) else bool(iterm2_importable)

    if api_reader is None:
        api_reader = _default_api_reader
    api_value = (api_reader() or "").strip()
    api_enabled = api_value == "1"

    if socket_present is None:
        socket_present = lambda: os.path.exists(API_SOCKET_PATH)  # noqa: E731
    present = socket_present() if callable(socket_present) else bool(socket_present)

    return Preflight(
        version=version or "?",
        is_37=is_37,
        iterm2_importable=importable,
        api_enabled=api_enabled,
        socket_present=present,
    )


def _default_iterm2_importable() -> bool:
    try:
        import iterm2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _default_api_reader() -> str:
    try:
        out = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def select_surfaces(only: str | None) -> list[str]:
    """Resolve the --only selection to an ordered surface list. PURE.

    ``only`` may name one surface, or be a comma-separated list; ``None`` (or
    "all") selects every surface in run order. Unknown names raise ValueError.
    """
    if not only or only == "all":
        return list(SURFACE_ORDER)
    chosen = [s.strip() for s in only.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in SURFACE_META]
    if unknown:
        raise ValueError(
            f"unknown surface(s): {', '.join(unknown)} "
            f"(known: {', '.join(SURFACE_ORDER)})"
        )
    # Preserve canonical run order regardless of the order given.
    return [s for s in SURFACE_ORDER if s in chosen]


# --------------------------------------------------------------------------- #
# Command construction (pure — unit-tested)
# --------------------------------------------------------------------------- #
def build_daemon_spawn_cmd(
    python: str, repo: str, agent_id: str, command: str,
    *, role: str = "backend", task: str = "smoke", status: str = "busy",
) -> list[str]:
    """The argv to open a tagged agent tab via the daemon spawn CLI.

    ``--no-gate`` forces identity tagging on (bypasses agent.status_board) so the
    identity user-vars are always stamped for the assertion.
    """
    return [
        python, str(DAEMON_CLI), "spawn", "--no-gate",
        "--role", role, "--task", task, "--id", agent_id,
        "--status", status, "--dir", repo, "--", command,
    ]


def build_ccstatus_cmd(python: str, *args: str) -> list[str]:
    """The argv to emit a ccstatus sequence, gate bypassed via IT2AGENT_FORCE=1
    (set by the caller in the child env), through the pure emitter module."""
    return [python, str(EMIT_CLI), "ccstatus", *args]


def build_validate_tmux_cmd(python: str, matcher: str) -> list[str]:
    return [python, str(VALIDATE_CLI), "--session", matcher]


# --------------------------------------------------------------------------- #
# lsof / tmux-output parsing (pure — unit-tested)
# --------------------------------------------------------------------------- #
def parse_lsof_cwd(output: str) -> str | None:
    """Extract the cwd path from `lsof -a -p <pid> -d cwd -Fn` output.

    The -F machine format emits one field per line; the path is the line that
    starts with 'n'. Returns None if not found. PURE.
    """
    for line in output.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def parse_tmux_validate_results(output: str) -> dict[str, str]:
    """Parse validate_api_over_tmux.py's measured-results table into
    {key: 'PASS'|'FAIL...'} pairs. PURE.

    Each result row looks like ``  <key padded> : <PASS|FAIL ...>``. Lines that
    are not ``key : value`` rows (headers, prose) are ignored.
    """
    results: dict[str, str] = {}
    for line in output.splitlines():
        if " : " not in line:
            continue
        key, _, value = line.partition(" : ")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        if value.startswith(PASS) or value.startswith(FAIL):
            results[key] = value
    return results


def tmux_surfaces_pass(parsed: dict[str, str]) -> tuple[bool, list[str]]:
    """Given the parsed validate table, decide whether surfaces 2/4/5 PASS.

    Surface 5 = async_set/get_variable, 4 = async_get_screen_contents,
    2 = custom_escape_sequence (raw OR tmux-passthrough is enough). PURE.
    Returns (ok, evidence_lines).
    """
    evidence: list[str] = []
    ok = True

    def passed(key: str) -> bool:
        return parsed.get(key, "").startswith(PASS)

    var_ok = passed("async_set/get_variable")
    screen_ok = passed("async_get_screen_contents")
    raw_ok = passed("custom_escape_sequence (raw)")
    wrapped_ok = passed("custom_escape_sequence (tmux-passthrough)")
    esc_ok = raw_ok or wrapped_ok

    evidence.append(f"surface 5 async_set/get_variable: {parsed.get('async_set/get_variable', '<absent>')}")
    evidence.append(f"surface 4 async_get_screen_contents: {parsed.get('async_get_screen_contents', '<absent>')}")
    evidence.append(
        "surface 2 custom_escape_sequence: raw="
        f"{parsed.get('custom_escape_sequence (raw)', '<absent>')} | "
        f"passthrough={parsed.get('custom_escape_sequence (tmux-passthrough)', '<absent>')}"
    )
    if not var_ok:
        ok = False
    if not screen_ok:
        ok = False
    if not esc_ok:
        ok = False
    if not parsed:
        ok = False
        evidence.append("no result rows parsed from validate output")
    return ok, evidence


def build_json_summary(preflight: Preflight, results: list[SurfaceResult], exit_code: int) -> dict:
    """The machine-readable summary (for future CI). PURE."""
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "preflight": preflight.to_dict(),
        "surfaces": [r.to_dict() for r in results],
    }


def overall_exit_code(results: list[SurfaceResult]) -> int:
    """0 iff every selected surface PASSED. A SKIP (a live prerequisite was
    missing) or a FAIL both yield nonzero — we never call an un-run surface a
    pass. PURE."""
    if not results:
        return 1
    return 0 if all(r.status == PASS for r in results) else 1


# --------------------------------------------------------------------------- #
# Temp git repo + worktree (used live; the create/remove path is unit-tested)
# --------------------------------------------------------------------------- #
def make_temp_git_repo(prefix: str = "it2smoke") -> str:
    """Create a throwaway git repo with one commit; return its path.

    Placed under the system temp dir with a short, recognizable basename so it
    surfaces in a tmux/iTerm2 session name. The caller is responsible for
    removing it (Harness.cleanup does)."""
    repo = tempfile.mkdtemp(prefix=f"{prefix}-")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "smoke@it2agent.test")
    _git(repo, "config", "user.name", "it2agent-smoke")
    (Path(repo) / "README.md").write_text("smoke\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def make_temp_worktree(repo: str, name: str) -> str:
    """Add a git worktree of ``repo`` at a sibling temp path; return its path."""
    parent = tempfile.mkdtemp(prefix="it2smoke-wt-")
    wt = os.path.join(parent, name)
    _git(repo, "worktree", "add", "-q", "-b", f"it2smoke/{name}", wt)
    return wt


def remove_temp_worktree(repo: str, wt: str) -> None:
    """Remove a worktree created by make_temp_worktree (branch + dir), tolerant
    of partial state so cleanup is idempotent."""
    try:
        _git(repo, "worktree", "remove", "--force", wt)
    except subprocess.CalledProcessError:
        pass
    try:
        _git(repo, "worktree", "prune")
    except subprocess.CalledProcessError:
        pass
    shutil.rmtree(os.path.dirname(wt), ignore_errors=True)


def _git(repo: str, *argv: str) -> None:
    subprocess.run(["git", "-C", repo, *argv], check=True, capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# The harness (owns resources + cleanup)
# --------------------------------------------------------------------------- #
class Harness:
    def __init__(self, *, tmux_matcher: str | None = None, no_cleanup: bool = False,
                 python: str | None = None):
        self.python = python or sys.executable
        self.no_cleanup = no_cleanup
        self.tmux_matcher = tmux_matcher

        # Short /tmp dir for the unix socket + isolated config (the ~104-byte
        # socket path limit; macOS mktemp under /var/folders overflows it).
        self.workdir = f"/tmp/it2s.{os.getpid()}"
        os.makedirs(self.workdir, exist_ok=True)
        self.config = os.path.join(self.workdir, "config.toml")

        # Tracked resources for cleanup.
        self._temp_repos: list[str] = []
        self._worktrees: list[tuple[str, str]] = []  # (repo, wt)
        self._spawned_agent_ids: list[str] = []       # tabs to close via the API
        self._procs: list[subprocess.Popen] = []       # brokers etc.
        self._tmux_sessions: list[str] = []
        self._cleaned = False

        self._token = f"{os.getpid()}-{random.randint(1000, 9999)}"

    # -- child env ---------------------------------------------------------
    def child_env(self, **extra: str) -> dict:
        env = dict(os.environ)
        env["IT2AGENT_CONFIG"] = self.config
        env.update(extra)
        return env

    def agent_id(self, tag: str) -> str:
        aid = f"smoke-{tag}-{self._token}"
        self._spawned_agent_ids.append(aid)
        return aid

    def temp_repo(self) -> str:
        repo = make_temp_git_repo()
        self._temp_repos.append(repo)
        return repo

    # -- surfaces ----------------------------------------------------------
    def run_surface(self, name: str, preflight: Preflight) -> SurfaceResult:
        meta = SURFACE_META[name]
        if meta["requires_live"] and not preflight.live_ok:
            reason = "requires a live iTerm2 3.7.dev + Python API; " + \
                     "; ".join(preflight.missing()) + " (not faking a live PASS)"
            return SurfaceResult(name, SKIP, reason=reason)
        try:
            return getattr(self, f"_surface_{name}")()
        except Exception as exc:  # noqa: BLE001 - a surface bug is a FAIL, not a crash
            return SurfaceResult(name, FAIL, reason=f"unexpected error: {exc!r}")

    # ---- ccstatus (headless-capable) ------------------------------------
    def _surface_ccstatus(self) -> SurfaceResult:
        env = self.child_env(IT2AGENT_FORCE="1")
        ev: list[str] = []
        checks = [
            (["busy", "--detail", "x"], GOLDEN_CCSTATUS_BUSY, "busy --detail x"),
            (["clear"], GOLDEN_CCSTATUS_CLEAR, "clear"),
        ]
        ok = True
        for args, golden, label in checks:
            out = subprocess.run(
                build_ccstatus_cmd(self.python, *args),
                env=env, capture_output=True, text=True, timeout=15,
            )
            got = out.stdout
            match = got == golden
            ev.append(f"{label}: bytes={_visible(got)} {'==' if match else '!='} golden")
            if not match:
                ok = False
        return SurfaceResult("ccstatus", PASS if ok else FAIL, evidence=ev,
                             reason="" if ok else "emitted OSC 21337 bytes drifted from golden")

    # ---- spawn (live) ----------------------------------------------------
    def _surface_spawn(self) -> SurfaceResult:
        import iterm2  # noqa: F401 - preflight guaranteed importable

        repo = self.temp_repo()
        repo_real = os.path.realpath(repo)
        aid = self.agent_id("spawn")
        shell = os.environ.get("SHELL", "/bin/sh")

        spawned = subprocess.run(
            build_daemon_spawn_cmd(self.python, repo, aid, shell),
            env=self.child_env(IT2AGENT_FORCE="1"),
            capture_output=True, text=True, timeout=45,
        )
        if spawned.returncode != 0:
            return SurfaceResult(
                "spawn", FAIL,
                reason=f"daemon spawn exited {spawned.returncode}: "
                       f"{(spawned.stderr or '').strip()[:400]}",
            )

        return _run_live(self._locate_and_probe_spawn, aid, repo_real)

    async def _locate_and_probe_spawn(self, connection, aid: str, repo_real: str) -> SurfaceResult:  # pragma: no cover - live
        import iterm2

        app = await iterm2.async_get_app(connection)
        session = await _find_session_by_agent_id(app, aid)
        if session is None:
            return SurfaceResult("spawn", FAIL,
                                 reason=f"could not find the spawned session (user.agent_id={aid})")
        ev: list[str] = []
        role = await session.async_get_variable(AGENT_ROLE_VAR)
        status = await session.async_get_variable(AGENT_STATUS_VAR)
        ev.append(f"identity: {AGENT_ROLE_VAR}={role!r} {AGENT_STATUS_VAR}={status!r}")
        identity_ok = bool(role) and bool(status)

        pid = await session.async_get_variable(PID_VAR)
        cwd_ok = False
        if pid:
            observed, cwd_ok = _wait_for_cwd(int(pid), repo_real)
            ev.append(f"lsof cwd(pid={pid})={observed!r} expected={repo_real!r} -> {'==' if cwd_ok else '!='}")
        else:
            ev.append("session exposed no pid var; cannot lsof its cwd")

        ok = identity_ok and cwd_ok
        reason = ""
        if not identity_ok:
            reason = "identity user-vars not set on the spawned session"
        elif not cwd_ok:
            reason = "spawned tab's real cwd (lsof) != the --dir repo (the #85 regression)"
        return SurfaceResult("spawn", PASS if ok else FAIL, evidence=ev, reason=reason)

    # ---- tmux (live) -----------------------------------------------------
    def _surface_tmux(self) -> SurfaceResult:
        if shutil.which("tmux") is None:
            return SurfaceResult("tmux", SKIP, reason="tmux binary not found on PATH")

        repo = self.temp_repo()
        matcher = self.tmux_matcher or os.path.basename(repo)
        role, task = "probe", "smoke"
        tmux_name = self._tmux_session_name(role, task)
        if tmux_name:
            self._tmux_sessions.append(tmux_name)

        spawn = subprocess.run(
            [str(TMUX_CLI), "spawn", "--no-gate", "--role", role, "--task", task,
             "--dir", repo, "--", os.environ.get("SHELL", "/bin/sh")],
            env=self.child_env(IT2AGENT_FORCE="1"),
            capture_output=True, text=True, timeout=45,
        )
        if spawn.returncode != 0:
            return SurfaceResult(
                "tmux", FAIL,
                reason=f"it2agent-tmux spawn exited {spawn.returncode}: "
                       f"{(spawn.stderr or '').strip()[:400]}",
            )
        # tmux -CC attaches the iTerm2 session ASYNCHRONOUSLY, a beat AFTER the
        # tmux session exists. The validator needs the iTerm2-side session
        # (tty=None) named after the cwd — so waiting on `tmux has-session` (the
        # tmux side) alone is not enough and caused a false FAIL (#AC14). Wait for
        # the tmux side to come up, then poll the iTerm2 app for the integrated
        # session and validate against its REAL name (unambiguous match).
        _wait_for_tmux_session(tmux_name, timeout=8.0)
        observed = _wait_for_iterm_tmux_session(matcher, timeout=30.0)
        if observed:
            matcher = observed

        validate = subprocess.run(
            build_validate_tmux_cmd(self.python, matcher),
            env=self.child_env(), capture_output=True, text=True, timeout=90,
        )
        parsed = parse_tmux_validate_results(validate.stdout)
        if not parsed and validate.returncode in (3, 4):
            # 3 = no live iTerm2/API; 4 = the --session matcher hit nothing (#82).
            hint = (validate.stderr or "").strip()[:500]
            return SurfaceResult(
                "tmux", FAIL,
                reason=f"validate_api_over_tmux.py exited {validate.returncode} "
                       f"(matcher={matcher!r}); try --tmux-matcher. {hint}",
            )
        ok, ev = tmux_surfaces_pass(parsed)
        ev.insert(0, f"matcher={matcher!r} validate_exit={validate.returncode}")
        return SurfaceResult("tmux", PASS if ok else FAIL, evidence=ev,
                             reason="" if ok else "one of tmux surfaces 2/4/5 did not PASS")

    def _tmux_session_name(self, role: str, task: str) -> str:
        try:
            out = subprocess.run(
                [str(TMUX_CLI), "name", "--role", role, "--task", task],
                capture_output=True, text=True, timeout=15,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    # ---- mcp (live) ------------------------------------------------------
    def _surface_mcp(self) -> SurfaceResult:
        import iterm2  # noqa: F401

        db = os.path.join(self.workdir, "broker.db")
        sock = os.path.join(self.workdir, "broker.sock")
        env = self.child_env(IT2AGENT_BROKER_DB=db, IT2AGENT_BROKER_SOCK=sock,
                              IT2AGENT_FORCE="1")

        broker = subprocess.Popen(
            [self.python, str(BROKER_CLI), "serve", "--no-gate"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._procs.append(broker)
        if not _wait_for_socket(sock, time.time() + 10.0):
            return SurfaceResult("mcp", FAIL, reason="broker did not come up")

        aid = self.agent_id("mcp")
        repo = self.temp_repo()
        shell = os.environ.get("SHELL", "/bin/sh")
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "spawn", "arguments": {
                            "command": shell, "id": aid, "role": "backend",
                            "task": "smoke", "cwd": repo}}}),
        ]
        proc = subprocess.run(
            [self.python, str(MCP_CLI), "--no-gate"],
            input="\n".join(lines) + "\n", env=env,
            capture_output=True, text=True, timeout=60,
        )
        launched, detail = _mcp_launched(proc.stdout)
        ev = [f"MCP spawn structured result: {detail}"]
        if launched:
            ev.append("launched=true -> a real iTerm2 tab was opened via the API")
        return SurfaceResult(
            "mcp", PASS if launched else FAIL, evidence=ev,
            reason="" if launched else "MCP spawn did not report launched=true "
                                       "(the live tab did not open)",
        )

    # -- cleanup -----------------------------------------------------------
    def cleanup(self) -> list[str]:
        """Best-effort, idempotent teardown. Closes ONLY tracked tabs, kills the
        tmux sessions + procs it started, removes temp repos/worktrees + the
        isolated config dir. Each step is independent so one failure never blocks
        the rest."""
        log: list[str] = []
        if self._cleaned:
            return log
        self._cleaned = True
        if self.no_cleanup:
            log.append("cleanup skipped (--no-cleanup); tracked resources left in place:")
            log.append(f"  workdir={self.workdir} repos={self._temp_repos} "
                       f"tmux={self._tmux_sessions} agent_ids={self._spawned_agent_ids}")
            return log

        # 1) Close ONLY the tabs we opened (matched by our unique agent_ids).
        if self._spawned_agent_ids:
            try:
                closed = _run_live(_close_tabs_by_agent_ids, list(self._spawned_agent_ids))
                log.append(f"closed spawned tabs: {closed}")
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - run_until_complete
                # calls sys.exit(1) (a SystemExit, not Exception) when it cannot
                # reach iTerm2; cleanup must survive that and never crash.
                log.append(f"tab close skipped ({exc!r}); ids={self._spawned_agent_ids}")

        # 2) Kill any tmux sessions we started.
        for name in self._tmux_sessions:
            try:
                subprocess.run(["tmux", "kill-session", "-t", name],
                               capture_output=True, text=True, timeout=10)
                log.append(f"killed tmux session {name}")
            except (OSError, subprocess.SubprocessError):
                pass

        # 3) Terminate broker/other procs.
        for proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self._procs:
            log.append(f"terminated {len(self._procs)} started process(es)")

        # 4) Remove worktrees, then temp repos, then the isolated workdir.
        for repo, wt in self._worktrees:
            remove_temp_worktree(repo, wt)
        for repo in self._temp_repos:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(self.workdir, ignore_errors=True)
        log.append(f"removed {len(self._temp_repos)} temp repo(s) + isolated config")
        return log


# --------------------------------------------------------------------------- #
# Live helpers (iterm2; not unit-tested — need the app)
# --------------------------------------------------------------------------- #
def _run_live(coro_fn, *args):  # pragma: no cover - live only
    """Run an async fn(connection, *args) under iterm2.run_until_complete and
    return its result."""
    import iterm2

    holder: dict = {}

    async def _main(connection):
        holder["result"] = await coro_fn(connection, *args)

    iterm2.run_until_complete(_main)
    return holder.get("result")


async def _find_session_by_agent_id(app, agent_id: str):  # pragma: no cover - live
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                try:
                    val = await session.async_get_variable(AGENT_ID_VAR)
                except Exception:  # noqa: BLE001
                    val = None
                if val == agent_id:
                    return session
    return None


async def _close_tabs_by_agent_ids(connection, agent_ids: list[str]) -> list[str]:  # pragma: no cover - live
    import iterm2

    app = await iterm2.async_get_app(connection)
    closed: list[str] = []
    wanted = set(agent_ids)
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                try:
                    val = await session.async_get_variable(AGENT_ID_VAR)
                except Exception:  # noqa: BLE001
                    continue
                if val in wanted:
                    try:
                        await tab.async_close(force=True)
                        closed.append(val)
                    except Exception:  # noqa: BLE001
                        pass
    return closed


def _wait_for_cwd(pid: int, expected_real: str, timeout: float = 6.0) -> tuple[str | None, bool]:  # pragma: no cover - live
    """Poll `lsof` until the pid's cwd equals expected_real, or timeout. This is
    a bounded wait for an external process to finish its `cd`, not a concurrency
    hack: the spawned shell runs `cd <repo> && exec <cmd>` and lsof can observe
    the old cwd for a beat right after launch."""
    deadline = time.time() + timeout
    observed: str | None = None
    while time.time() < deadline:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True,
        )
        observed = parse_lsof_cwd(out.stdout)
        if observed and os.path.realpath(observed) == expected_real:
            return observed, True
        time.sleep(0.2)
    return observed, bool(observed and os.path.realpath(observed) == expected_real)


def _wait_for_tmux_session(name: str, timeout: float = 6.0) -> bool:  # pragma: no cover - live
    if not name:
        time.sleep(1.0)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(["tmux", "has-session", "-t", name],
                             capture_output=True, text=True)
        if out.returncode == 0:
            return True
        time.sleep(0.25)
    return False


async def _await_tmux_iterm_session(connection, matcher, timeout):  # pragma: no cover - live
    """Poll the iTerm2 app (single connection) until the integrated tmux -CC
    session registers: a session whose `name` contains `matcher` AND whose `tty`
    is None (the tell-tale of a tmux-CC-backed iTerm2 session). Returns the real
    iTerm2 session name, or None on timeout."""
    import asyncio
    import iterm2

    app = await iterm2.async_get_app(connection)
    deadline = time.time() + timeout
    ml = (matcher or "").lower()
    while time.time() < deadline:
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    try:
                        name = await session.async_get_variable("name") or ""
                        tty = await session.async_get_variable("tty")
                    except Exception:  # noqa: BLE001
                        continue
                    if ml in name.lower() and tty in (None, "None", ""):
                        return name
        await asyncio.sleep(0.5)
    return None


def _wait_for_iterm_tmux_session(matcher: str, timeout: float = 30.0) -> str | None:  # pragma: no cover - live
    """Wait for the iTerm2-side integrated tmux -CC session to register (#AC14).

    tmux -CC integration attaches the iTerm2 session ASYNCHRONOUSLY, a beat after
    the tmux session itself exists — so `tmux has-session` succeeding does NOT
    mean the validator can see the session yet. Waiting on the tmux side alone
    caused a false FAIL (the validator ran before the iTerm2 session existed and,
    correctly, refused to fall back to the current session). This polls the
    iTerm2 app for the real integrated session and returns its name."""
    return _run_live(_await_tmux_iterm_session, matcher, timeout)


def _wait_for_socket(sock: str, deadline: float) -> bool:
    """Wait until the broker unix socket answers a health ping."""
    import socket as _socket

    while time.time() < deadline:
        if os.path.exists(sock):
            try:
                s = _socket.socket(_socket.AF_UNIX)
                s.settimeout(2.0)
                s.connect(sock)
                s.sendall((json.dumps({"op": "health"}) + "\n").encode())
                s.makefile().readline()
                s.close()
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def _mcp_launched(stdout: str) -> tuple[bool, str]:
    """Extract launch.launched from the MCP spawn tool response. PURE."""
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if obj.get("id") != 2:
            continue
        result = obj.get("result") or {}
        structured = result.get("structuredContent") or {}
        launch = structured.get("launch") or {}
        return bool(launch.get("launched")), json.dumps(structured, sort_keys=True)
    return False, "<no spawn response parsed>"


def _visible(text: str) -> str:
    """Render control bytes visibly for evidence lines."""
    return text.replace(ESC, "\\e").replace(BEL, "\\a")


# --------------------------------------------------------------------------- #
# Reporting + entrypoint
# --------------------------------------------------------------------------- #
def _print_human(preflight: Preflight, selected: list[str],
                 results: list[SurfaceResult], cleanup_log: list[str], exit_code: int) -> None:
    print("=== it2agent live smoke harness (#124) ===")
    pf = preflight
    print(f"preflight: build={pf.version} is_37={pf.is_37} iterm2={pf.iterm2_importable} "
          f"api_enabled={pf.api_enabled} socket={pf.socket_present} -> live_ok={pf.live_ok}")
    if not pf.live_ok:
        for gap in pf.missing():
            print(f"  ! {gap}")
        print("  -> live surfaces are SKIPPED (not faked). Enable the API and re-run.")
    print(f"selected surfaces: {', '.join(selected)}")
    print("-" * 60)
    for r in results:
        line = f"[{r.status}] {r.name} — {SURFACE_META[r.name]['desc']}"
        print(line)
        for e in r.evidence:
            print(f"    · {e}")
        if r.reason:
            print(f"    reason: {r.reason}")
    print("-" * 60)
    if cleanup_log:
        print("cleanup:")
        for entry in cleanup_log:
            print(f"  {entry}")
    summary = ", ".join(f"{r.name}={r.status}" for r in results)
    print(f"RESULT: {summary}  (exit {exit_code})")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="live_smoke.py",
        description="One-command live smoke harness for the iTerm2-API/AppleScript layer (#124).",
    )
    parser.add_argument("--only", default=None,
                        help="run one surface (or a comma list): " + ", ".join(SURFACE_ORDER))
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary object.")
    parser.add_argument("--tmux-matcher", default=None,
                        help="substring of the iTerm2 session name to probe for the tmux surface "
                             "(default: the temp repo basename). Tune this if the tmux surface "
                             "reports a no-match (#82).")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="leave tracked resources in place (debugging).")
    args = parser.parse_args([] if argv is None else argv)

    try:
        selected = select_surfaces(args.only)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    preflight = detect_preflight()

    harness = Harness(tmux_matcher=args.tmux_matcher, no_cleanup=args.no_cleanup)
    results: list[SurfaceResult] = []
    cleanup_log: list[str] = []
    try:
        for name in selected:
            results.append(harness.run_surface(name, preflight))
    finally:
        cleanup_log = harness.cleanup()

    exit_code = overall_exit_code(results)
    if args.json:
        summary = build_json_summary(preflight, results, exit_code)
        summary["cleanup"] = cleanup_log
        summary["selected"] = selected
        print(json.dumps(summary, indent=2))
    else:
        _print_human(preflight, selected, results, cleanup_log, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
