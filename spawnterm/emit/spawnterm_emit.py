#!/usr/bin/env python3
"""spawnterm-emit — emit iTerm2 proprietary escape codes so an agent can signal
its own state (status/role/task/attention/mark/progress).

spawnTerm Tier 0.1 (#7). scope:external-tooling — runs *on* iTerm2's escape
codes; never modifies iTerm2 source. This is the byte-for-byte parity twin of
the shell `spawnterm-emit`; the sequences the two write must be identical.

Framing (per iTerm2 "Proprietary Escape Codes" docs):
    OSC = ESC ]   (0x1b 0x5d)
    ST  = BEL     (0x07)   <- iTerm2-documented terminator

Gating: gates on the feature flag `spawnterm.status_board` via the external
`spawnterm-flag` helper (#11). If the flag is OFF/absent, or spawnterm-flag is
not on PATH, emit nothing and exit 0 quietly (fail-safe: off by default).
Bypass with `--no-gate` or SPAWNTERM_FORCE=1.

Secrets: the only bytes written to stdout are the escape sequence itself.
SetUserVar values are base64'd (as iTerm2 requires); nothing is logged.
"""
import base64
import os
import shutil
import subprocess
import sys

PROG = "spawnterm-emit"
FLAG_KEY = "spawnterm.status_board"
DEFAULT_ATTENTION_MSG = "spawnterm: agent needs attention"
# Default badge interpolates the user vars set by `role`/`task` (iTerm2 exposes
# SetUserVar=agent.<k> as user.agent.<k>). Middle dot separates role and task.
DEFAULT_BADGE_FORMAT = r"\(user.agent.role) · \(user.agent.task)"

# Lifecycle palette: colorblind-safe (Okabe-Ito). Kept byte-identical with the
# shell `color_for`. Rationale + exact bytes: spawnterm/emit/docs/colors.md.
STATUS_COLORS = {
    "busy": "0072B2",
    "blocked": "E69F00",
    "done": "009E73",
    "idle": "999999",
}

ESC = "\033"
BEL = "\007"


def usage(stream):
    stream.write(
        f"""{PROG} — emit iTerm2 escape codes to signal agent state.

Usage:
  {PROG} [--no-gate] <command> [args]

Commands:
  status <value>          Set user var agent.status (base64'd).
  role <value>            Set user var agent.role (base64'd).
  task <value>            Set user var agent.task (base64'd).
  attention [message]     RequestAttention=yes + an OSC 9 notification.
                          Default message: "{DEFAULT_ATTENTION_MSG}"
  mark                    Emit SetMark.
  progress <state> <pct>  ConEmu progress. state in {{0,1,2,3,4}}, pct in 0..100.
                          0=remove 1=normal 2=error 3=indeterminate 4=paused
  color <role-or-status>  Set the tab color (SetColors=tab). Accepts a lifecycle
                          status (busy, blocked, done, idle) mapped to a
                          colorblind-safe hex, or a raw RGB/RRGGBB hex.
  badge [format]          Set the session badge (SetBadgeFormat, base64'd).
                          Default: {DEFAULT_BADGE_FORMAT}

Options:
  --no-gate               Bypass the feature-flag gate (local testing).
  -h, --help              Show this help.

Environment:
  SPAWNTERM_FORCE=1       Bypass the feature-flag gate (local testing).

Gating: emits only when the feature flag "{FLAG_KEY}" is ON (checked via the
spawnterm-flag helper). Otherwise emits nothing and exits 0.
"""
    )


def die(msg):
    # Usage/validation errors go to stderr. Never echoes emitted values.
    sys.stderr.write(f"{PROG}: {msg}\n")
    sys.exit(2)


def gate_open(no_gate):
    """Return True if the capability is enabled (or gating is bypassed)."""
    if os.environ.get("SPAWNTERM_FORCE") == "1":
        return True
    if no_gate:
        return True
    # Fail-safe: if the flag helper isn't on PATH, treat the flag as OFF.
    if shutil.which("spawnterm-flag") is None:
        return False
    # spawnterm-flag prints 1/exit0 when ON, 0/exit1 when OFF. Swallow its
    # output so nothing but our escape sequence reaches our stdout.
    try:
        result = subprocess.run(
            ["spawnterm-flag", FLAG_KEY],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def b64(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def osc(body):
    """Wrap a body in OSC ... BEL."""
    return f"{ESC}]{body}{BEL}"


def is_uint(text):
    return text.isdigit()


def is_hex(text):
    """True for a 3- or 6-digit hex color (case-insensitive), no leading '#'."""
    if len(text) not in (3, 6):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in text)


def color_for(name):
    """Map a lifecycle status/role to a colorblind-safe hex, or pass a raw hex
    through. Returns RRGGBB, or None for unknown input."""
    if name in STATUS_COLORS:
        return STATUS_COLORS[name]
    if is_hex(name):
        return name
    return None


def build_sequence(cmd, args):
    """Validate args and return the raw escape sequence. Bad input exits(2)."""
    if cmd in ("status", "role", "task"):
        if len(args) != 1:
            die(f"{cmd} requires exactly one <value> argument")
        return osc(f"1337;SetUserVar=agent.{cmd}={b64(args[0])}")
    if cmd == "attention":
        msg = " ".join(args) if args else DEFAULT_ATTENTION_MSG
        return osc("1337;RequestAttention=yes") + osc(f"9;{msg}")
    if cmd == "mark":
        if args:
            die("mark takes no arguments")
        return osc("1337;SetMark")
    if cmd == "progress":
        if len(args) != 2:
            die("progress requires <state> and <pct>")
        state, pct = args[0], args[1]
        if state not in ("0", "1", "2", "3", "4"):
            die(f"progress state must be one of 0,1,2,3,4 (got: {state})")
        if not is_uint(pct):
            die(f"progress pct must be an integer 0..100 (got: {pct})")
        if not (0 <= int(pct) <= 100):
            die(f"progress pct must be 0..100 (got: {pct})")
        return osc(f"9;4;{state};{pct}")
    if cmd == "color":
        if len(args) != 1:
            die("color requires exactly one <role-or-status> argument")
        hex_value = color_for(args[0])
        if hex_value is None:
            die(
                f"unknown status/role: {args[0]} "
                "(known: busy, blocked, done, idle; or pass a RGB/RRGGBB hex)"
            )
        return osc(f"1337;SetColors=tab={hex_value}")
    if cmd == "badge":
        fmt = " ".join(args) if args else DEFAULT_BADGE_FORMAT
        return osc(f"1337;SetBadgeFormat={b64(fmt)}")
    die(f"unknown command: {cmd} (try --help)")


def main(argv):
    args = list(argv)

    # Parse leading global options.
    no_gate = False
    while args:
        a = args[0]
        if a == "--no-gate":
            no_gate = True
            args.pop(0)
        elif a in ("-h", "--help"):
            usage(sys.stdout)
            return 0
        elif a == "--":
            args.pop(0)
            break
        elif a.startswith("-"):
            die(f"unknown option: {a}")
        else:
            break

    if not args:
        usage(sys.stderr)
        return 2

    cmd = args[0]
    rest = args[1:]

    # Validate + build BEFORE consulting the gate: bad input is a usage error
    # even when the capability is gated off.
    seq = build_sequence(cmd, rest)

    if not gate_open(no_gate):
        return 0

    sys.stdout.write(seq)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
