#!/usr/bin/env bash
# Parity + behavior tests for spawnterm-emit (shell) and spawnterm_emit.py.
#
# - Verifies the two emitters produce BYTE-IDENTICAL escape sequences.
# - Visualizes the exact bytes with `cat -v` and `od -An -tx1`.
# - Exercises the feature-flag gate: bypass (SPAWNTERM_FORCE=1 / --no-gate)
#   AND the fail-safe OFF path (spawnterm-flag absent -> no output, exit 0).
# - Checks input validation (bad args -> stderr, exit 2).
#
# Run from anywhere: bash spawnterm/emit/tests/test_emit.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
EMIT_DIR="$(dirname "$HERE")"
SH="$EMIT_DIR/spawnterm-emit"
PY="$EMIT_DIR/spawnterm_emit.py"

pass=0
fail=0
green() { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
red() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

hexof() { od -An -tx1 | tr -s ' ' | sed 's/^ //;s/ $//'; }

# Run both emitters with the SAME args (bypassing the gate), assert the raw
# bytes match, and show them visualized.
parity() {
	local label="$1"; shift
	local sh_out py_out sh_hex py_hex
	sh_out="$(SPAWNTERM_FORCE=1 sh "$SH" "$@")"
	py_out="$(SPAWNTERM_FORCE=1 python3 "$PY" "$@")"
	sh_hex="$(printf '%s' "$sh_out" | hexof)"
	py_hex="$(printf '%s' "$py_out" | hexof)"
	printf '\n[%s]  args: %s\n' "$label" "$*"
	printf '  cat -v : %s\n' "$(printf '%s' "$sh_out" | cat -v)"
	printf '  bytes  : %s\n' "$sh_hex"
	if [ "$sh_hex" = "$py_hex" ]; then
		green "$label shell/python byte-identical"
	else
		red "$label MISMATCH"
		printf '    shell: %s\n    py   : %s\n' "$sh_hex" "$py_hex"
	fi
}

expect_exit() {
	local label="$1" want="$2"; shift 2
	local got
	"$@" >/dev/null 2>&1
	got=$?
	if [ "$got" = "$want" ]; then
		green "$label (exit $got)"
	else
		red "$label (want exit $want, got $got)"
	fi
}

expect_empty() {
	local label="$1"; shift
	local out
	out="$("$@" 2>/dev/null)"
	if [ -z "$out" ]; then
		green "$label produced no output"
	else
		red "$label leaked output: $(printf '%s' "$out" | cat -v)"
	fi
}

echo "=== spawnterm-emit parity + behavior tests ==="
echo "shell : $SH"
echo "python: $PY"

echo
echo "--- 1. escape-sequence parity (gate bypassed via SPAWNTERM_FORCE=1) ---"
parity "status"    status "running: build #42"
parity "role"      role "tech-lead"
parity "task"      task "implement #7 emit helper"
parity "attention-default" attention
parity "attention-msg"     attention "agent blocked on review"
parity "mark"      mark
parity "progress-normal"   progress 1 37
parity "progress-error"    progress 2 100
# Values containing shell/printf metacharacters must survive intact.
parity "status-metachars" status '100% done; path=$HOME "quoted" `cmd`'
# Tier 0.2 (#8): tab color per lifecycle status + session badge.
parity "color-busy"        color busy
parity "color-blocked"     color blocked
parity "color-done"        color done
parity "color-idle"        color idle
parity "color-rawhex"      color a1b2c3
parity "color-rgbshort"    color fff
parity "badge-default"     badge
parity "badge-custom"      badge '\(user.agent_role): \(user.agent_status)'

echo
echo "--- 1b. color palette + badge base64 correctness ---"
# Each lifecycle status maps to its documented colorblind-safe (Okabe-Ito) hex.
check_color() {
	local status="$1" want_hex="$2" out
	out="$(SPAWNTERM_FORCE=1 sh "$SH" color "$status")"
	if printf '%s' "$out" | grep -q "SetColors=tab=${want_hex}"; then
		green "color $status -> SetColors=tab=$want_hex"
	else
		red "color $status wrong hex: $(printf '%s' "$out" | cat -v)"
	fi
}
check_color busy    0072B2
check_color blocked E69F00
check_color done    009E73
check_color idle    999999

# The badge payload must be base64 that decodes back to the exact format string.
badge_b64="$(SPAWNTERM_FORCE=1 sh "$SH" badge | sed -e 's/.*SetBadgeFormat=//' -e 's/'"$(printf '\007')"'$//')"
badge_decoded="$(printf '%s' "$badge_b64" | base64 --decode 2>/dev/null || printf '%s' "$badge_b64" | base64 -D)"
want_badge='\(user.agent_role) · \(user.agent_task)'
if [ "$badge_decoded" = "$want_badge" ]; then
	green "badge base64 round-trips to the default format"
else
	red "badge base64 mismatch: got [$(printf '%s' "$badge_decoded" | cat -v)]"
fi

echo
echo "--- 2. feature-flag gating ---"
# We keep the real PATH (so sh/python3/base64/tr resolve) and control only
# whether a `spawnterm-flag` shim is visible, by prepending a temp dir.
TMP_BASE="$(mktemp -d)"
FLAG_OFF="$TMP_BASE/off"; mkdir -p "$FLAG_OFF"
FLAG_ON="$TMP_BASE/on"; mkdir -p "$FLAG_ON"
printf '#!/bin/sh\necho 0\nexit 1\n' > "$FLAG_OFF/spawnterm-flag"
printf '#!/bin/sh\necho 1\nexit 0\n' > "$FLAG_ON/spawnterm-flag"
chmod +x "$FLAG_OFF/spawnterm-flag" "$FLAG_ON/spawnterm-flag"

if command -v spawnterm-flag >/dev/null 2>&1; then
	printf '  \033[33mNOTE\033[0m a real spawnterm-flag is on PATH; "absent" test may be affected\n'
fi

# Fail-safe: spawnterm-flag not on PATH and no force => no output, exit 0.
gate_env() { env -u SPAWNTERM_FORCE "$@"; }
expect_empty "shell:  flag absent -> gated off"  gate_env sh "$SH" status "x"
expect_empty "python: flag absent -> gated off"  gate_env python3 "$PY" status "x"
expect_exit  "shell:  flag absent exits 0"  0  gate_env sh "$SH" status "x"
expect_exit  "python: flag absent exits 0"  0  gate_env python3 "$PY" status "x"

# Flag helper present but reports OFF (prints 0, exit 1) => no output.
expect_empty "shell:  flag OFF -> no output"   gate_env env PATH="$FLAG_OFF:$PATH" sh "$SH" status "x"
expect_empty "python: flag OFF -> no output"   gate_env env PATH="$FLAG_OFF:$PATH" python3 "$PY" status "x"
# The new color/badge subcommands gate on the SAME flag (no forked gate).
expect_empty "shell:  color flag OFF -> no output"  gate_env env PATH="$FLAG_OFF:$PATH" sh "$SH" color busy
expect_empty "python: color flag OFF -> no output"  gate_env env PATH="$FLAG_OFF:$PATH" python3 "$PY" color busy
expect_empty "shell:  badge flag OFF -> no output"  gate_env env PATH="$FLAG_OFF:$PATH" sh "$SH" badge
expect_empty "shell:  color flag absent -> no output"  gate_env sh "$SH" color busy

# Flag helper reports ON (prints 1, exit 0) => both emit, byte-identical.
on_sh="$(gate_env env PATH="$FLAG_ON:$PATH" sh "$SH" mark | hexof)"
on_py="$(gate_env env PATH="$FLAG_ON:$PATH" python3 "$PY" mark | hexof)"
if [ -n "$on_sh" ] && [ "$on_sh" = "$on_py" ]; then
	green "flag ON -> both emit, byte-identical ($on_sh)"
else
	red "flag ON emit mismatch (sh=$on_sh py=$on_py)"
fi
# --no-gate must bypass even when the flag reports OFF.
expect_exit "shell:  --no-gate bypasses OFF (emits, exit 0)" 0 \
	gate_env env PATH="$FLAG_OFF:$PATH" sh "$SH" --no-gate mark
nogate_out="$(gate_env env PATH="$FLAG_OFF:$PATH" sh "$SH" --no-gate mark | hexof)"
if [ -n "$nogate_out" ]; then
	green "shell:  --no-gate actually emits ($nogate_out)"
else
	red "shell:  --no-gate produced no output"
fi

echo
echo "--- 3. input validation (bad args -> exit 2) ---"
expect_exit "shell:  progress bad state"  2  env SPAWNTERM_FORCE=1 sh "$SH" progress 9 50
expect_exit "python: progress bad state"  2  env SPAWNTERM_FORCE=1 python3 "$PY" progress 9 50
expect_exit "shell:  progress pct>100"    2  env SPAWNTERM_FORCE=1 sh "$SH" progress 1 101
expect_exit "python: progress pct>100"    2  env SPAWNTERM_FORCE=1 python3 "$PY" progress 1 101
expect_exit "shell:  progress non-int"    2  env SPAWNTERM_FORCE=1 sh "$SH" progress 1 abc
expect_exit "python: progress non-int"    2  env SPAWNTERM_FORCE=1 python3 "$PY" progress 1 abc
expect_exit "shell:  status missing arg"  2  env SPAWNTERM_FORCE=1 sh "$SH" status
expect_exit "python: status missing arg"  2  env SPAWNTERM_FORCE=1 python3 "$PY" status
expect_exit "shell:  unknown command"     2  env SPAWNTERM_FORCE=1 sh "$SH" frobnicate
expect_exit "python: unknown command"     2  env SPAWNTERM_FORCE=1 python3 "$PY" frobnicate
expect_exit "shell:  color unknown status" 2  env SPAWNTERM_FORCE=1 sh "$SH" color nope
expect_exit "python: color unknown status" 2  env SPAWNTERM_FORCE=1 python3 "$PY" color nope
expect_exit "shell:  color bad hex"        2  env SPAWNTERM_FORCE=1 sh "$SH" color gggggg
expect_exit "python: color bad hex"        2  env SPAWNTERM_FORCE=1 python3 "$PY" color gggggg
expect_exit "shell:  color missing arg"    2  env SPAWNTERM_FORCE=1 sh "$SH" color
expect_exit "python: color missing arg"    2  env SPAWNTERM_FORCE=1 python3 "$PY" color
expect_exit "shell:  color too many args"  2  env SPAWNTERM_FORCE=1 sh "$SH" color busy idle
expect_exit "python: color too many args"  2  env SPAWNTERM_FORCE=1 python3 "$PY" color busy idle

echo
echo "--- 4. --help exits 0 on both ---"
expect_exit "shell:  --help"  0  sh "$SH" --help
expect_exit "python: --help"  0  python3 "$PY" --help

rm -rf "$TMP_BASE"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]
