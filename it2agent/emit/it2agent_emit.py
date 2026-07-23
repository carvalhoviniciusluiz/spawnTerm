#!/usr/bin/env python3
"""it2agent-emit — emit iTerm2 proprietary escape codes so an agent can signal
its own state (status/role/task/attention/mark/progress).

it2agent Tier 0.1 (#7). scope:external-tooling — runs *on* iTerm2's escape
codes; never modifies iTerm2 source. This is the byte-for-byte parity twin of
the shell `it2agent-emit`; the sequences the two write must be identical.

Framing (per iTerm2 "Proprietary Escape Codes" docs):
    OSC = ESC ]   (0x1b 0x5d)
    ST  = BEL     (0x07)   <- iTerm2-documented terminator

The `ccstatus` command cooperates with iTerm2's NATIVE tab-status channel
(OSC 21337 — the one feeding the native tab status + Cockpit / cc-status),
whose values are LITERAL (not base64) semicolon-delimited key=value pairs with
`\\;`/`\\\\` escaping and empty-clears-a-key semantics.

Gating: most commands gate on the feature flag `agent.status_board`; `ccstatus`
gates on the separate `agent.native_status`. Both are checked via the external
`it2agent-flag` helper (#11). If the flag is OFF/absent, or it2agent-flag is
not on PATH, emit nothing and exit 0 quietly (fail-safe: off by default).
Bypass with `--no-gate` or IT2AGENT_FORCE=1.

Secrets: the only bytes written to stdout are the escape sequence itself.
SetUserVar values are base64'd (as iTerm2 requires); nothing is logged.
"""
import base64
import os
import shutil
import subprocess
import sys

PROG = "it2agent-emit"
FLAG_KEY = "agent.status_board"
# ccstatus emits on the native OSC 21337 channel; it gates on its own flag.
FLAG_KEY_NATIVE = "agent.native_status"
DEFAULT_ATTENTION_MSG = "it2agent: agent needs attention"
# Default badge interpolates the user vars set by `role`/`task`. iTerm2 forbids
# `.` in a SetUserVar key (PTYSession.screenSetUserVar: rejects it) and prefixes
# `user.` itself, so `SetUserVar=agent_<k>` becomes `user.agent_<k>`. Middle dot
# separates role and task in the displayed badge.
DEFAULT_BADGE_FORMAT = r"\(user.agent_role) · \(user.agent_task)"

# Lifecycle palette: colorblind-safe (Okabe-Ito). Kept byte-identical with the
# shell `color_for`. Rationale + exact bytes: it2agent/emit/docs/colors.md.
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
  status <value>          Set user var agent_status (base64'd).
  role <value>            Set user var agent_role (base64'd).
  task <value>            Set user var agent_task (base64'd).
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
  ccstatus <status>       Set the NATIVE tab status (OSC 21337) shown in
    [--detail <text>]     iTerm2's tab status + Cockpit. <status> is a lifecycle
                          keyword (busy, blocked, done, idle) — capitalized and
                          given the matching lifecycle indicator color — or free
                          text. --detail adds the optional detail line.
  ccstatus clear          Clear the native tab status.

Options:
  --no-gate               Bypass the feature-flag gate (local testing).
  -h, --help              Show this help.

Environment:
  IT2AGENT_FORCE=1       Bypass the feature-flag gate (local testing).

Gating: emits only when the governing feature flag is ON (checked via the
it2agent-flag helper). Most commands gate on "{FLAG_KEY}"; ccstatus gates on
"{FLAG_KEY_NATIVE}". Otherwise emits nothing and exits 0.
"""
    )


def die(msg):
    # Usage/validation errors go to stderr. Never echoes emitted values.
    sys.stderr.write(f"{PROG}: {msg}\n")
    sys.exit(2)


def gate_open(no_gate, flag_key):
    """Return True if the capability is enabled (or gating is bypassed).

    ``flag_key`` is the feature-flag key this command gates on.
    """
    if os.environ.get("IT2AGENT_FORCE") == "1":
        return True
    if no_gate:
        return True
    # Fail-safe: if the flag helper isn't on PATH, treat the flag as OFF.
    if shutil.which("it2agent-flag") is None:
        return False
    # it2agent-flag prints 1/exit0 when ON, 0/exit1 when OFF. Swallow its
    # output so nothing but our escape sequence reaches our stdout.
    try:
        result = subprocess.run(
            ["it2agent-flag", flag_key],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def b64(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def esc21337(text):
    """Escape text for a native OSC 21337 value: `\\` -> `\\\\`, then `;` ->
    `\\;` (order matters). Matches VT100Terminal.m executeSetTabStatus."""
    return text.replace("\\", "\\\\").replace(";", "\\;")


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
        return osc(f"1337;SetUserVar=agent_{cmd}={b64(args[0])}")
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
    if cmd == "ccstatus":
        return build_ccstatus(args)
    die(f"unknown command: {cmd} (try --help)")


def build_ccstatus(args):
    """Build the native OSC 21337 tab-status sequence."""
    status_arg = None
    detail = None
    rest = list(args)
    while rest:
        a = rest.pop(0)
        if a == "--detail":
            if not rest:
                die("ccstatus --detail requires a <text> value")
            detail = rest.pop(0)
        elif status_arg is None:
            status_arg = a
        else:
            die("ccstatus takes exactly one <status> (or 'clear')")
    if status_arg is None:
        die("ccstatus requires a <status> (busy|blocked|done|idle|text) or 'clear'")
    if status_arg == "clear":
        if detail is not None:
            die("ccstatus clear takes no --detail")
        # Empty values clear each native key.
        return osc("21337;status=;indicator=;detail=")
    # Lifecycle keywords get a capitalized label + the matching lifecycle
    # indicator hex (reusing the palette in STATUS_COLORS); anything else is
    # free text with no indicator.
    if status_arg in STATUS_COLORS:
        label = status_arg.capitalize()
        indicator = f"#{STATUS_COLORS[status_arg]}"
    else:
        label = esc21337(status_arg)
        indicator = None
    body = f"21337;status={label}"
    if indicator is not None:
        body += f";indicator={indicator}"
    if detail is not None:
        body += f";detail={esc21337(detail)}"
    return osc(body)


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

    # ccstatus opts into the native OSC 21337 channel via its own flag.
    flag_key = FLAG_KEY_NATIVE if cmd == "ccstatus" else FLAG_KEY
    if not gate_open(no_gate, flag_key):
        return 0

    sys.stdout.write(seq)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
