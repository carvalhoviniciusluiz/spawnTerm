#!/usr/bin/env bash
# Test suite for the spawnterm feature-flag helpers.
#
# Exercises: default-OFF with no config, enable->ON, disable->OFF, list,
# prefix normalization, path, unknown-key handling, and shell/Python parity
# (identical stdout + exit code for the same inputs). Uses a throwaway config
# via $SPAWNTERM_CONFIG so it never touches the real ~/.config.
#
# Usage: bash spawnterm/flags/tests/test_flags.sh
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SHELL_BIN="$HERE/spawnterm-flag"
PY_MOD="$HERE/spawnterm_flag.py"

WORK="$(mktemp -d)"
export SPAWNTERM_CONFIG="$WORK/config.toml"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

fail() {
  FAIL=$((FAIL + 1))
  printf 'FAIL: %s\n' "$1"
}
ok() {
  PASS=$((PASS + 1))
  printf 'ok  : %s\n' "$1"
}

# run <impl> <args...>  -> captures OUT and RC globals
run_shell() {
  OUT="$("$SHELL_BIN" "$@" 2>/dev/null)"; RC=$?
}
run_py() {
  OUT="$(python3 "$PY_MOD" "$@" 2>/dev/null)"; RC=$?
}

# assert_query <impl-fn> <label> <key> <want_out> <want_rc>
assert_query() {
  local fn="$1" label="$2" key="$3" wout="$4" wrc="$5"
  "$fn" "$key"
  if [ "$OUT" = "$wout" ] && [ "$RC" = "$wrc" ]; then
    ok "$label (out='$OUT' rc=$RC)"
  else
    fail "$label: got out='$OUT' rc=$RC, want out='$wout' rc=$wrc"
  fi
}

reset() { rm -f "$SPAWNTERM_CONFIG"; }

echo "== default OFF, no config file =="
reset
assert_query run_shell "shell: absent key -> OFF"  spawnterm.messaging 0 1
assert_query run_py    "py:    absent key -> OFF"  messaging           0 1
[ ! -f "$SPAWNTERM_CONFIG" ] && ok "read did not create a config file" || fail "read created a config file"

echo "== enable -> ON (shell writes, both read) =="
reset
run_shell enable messaging
[ "$RC" = "0" ] && ok "shell enable rc=0" || fail "shell enable rc=$RC"
[ -f "$SPAWNTERM_CONFIG" ] && ok "enable created config file" || fail "enable did not create config"
assert_query run_shell "shell: enabled -> ON"       spawnterm.messaging 1 0
assert_query run_py    "py:    enabled -> ON"       spawnterm.messaging 1 0

echo "== prefix normalization (with/without spawnterm.) =="
assert_query run_shell "shell: bare key reads ON"   messaging           1 0
assert_query run_py    "py:    prefixed key reads ON" spawnterm.messaging 1 0

echo "== other flags remain OFF after enabling one =="
assert_query run_shell "shell: janitor still OFF"   spawnterm.janitor   0 1
assert_query run_py    "py:    janitor still OFF"   janitor             0 1

echo "== disable -> OFF (python writes, both read) =="
run_py disable spawnterm.messaging
[ "$RC" = "0" ] && ok "py disable rc=0" || fail "py disable rc=$RC"
assert_query run_shell "shell: disabled -> OFF"     messaging           0 1
assert_query run_py    "py:    disabled -> OFF"     messaging           0 1

echo "== list parity =="
reset
"$SHELL_BIN" enable status_board >/dev/null
"$SHELL_BIN" enable mcp >/dev/null
SH_LIST="$("$SHELL_BIN" list)"
PY_LIST="$(python3 "$PY_MOD" list)"
if [ "$SH_LIST" = "$PY_LIST" ]; then
  ok "shell list == python list"
else
  fail "list parity mismatch"
  printf 'shell:\n%s\npython:\n%s\n' "$SH_LIST" "$PY_LIST"
fi
echo "$SH_LIST" | grep -q "^spawnterm.status_board *on$" && ok "list shows status_board on" || fail "list status_board"
echo "$SH_LIST" | grep -q "^spawnterm.janitor *off$" && ok "list shows janitor off" || fail "list janitor"

echo "== canonical file byte-for-byte parity (shell vs python writer) =="
reset
"$SHELL_BIN" enable messaging >/dev/null
SH_FILE="$(cat "$SPAWNTERM_CONFIG")"
reset
python3 "$PY_MOD" enable messaging >/dev/null
PY_FILE="$(cat "$SPAWNTERM_CONFIG")"
if [ "$SH_FILE" = "$PY_FILE" ]; then
  ok "shell and python produce identical config files"
else
  fail "config writer mismatch"
  printf 'shell:\n%s\npython:\n%s\n' "$SH_FILE" "$PY_FILE"
fi

echo "== path parity =="
run_shell path; SH_PATH="$OUT"
run_py path;    PY_PATH="$OUT"
[ "$SH_PATH" = "$PY_PATH" ] && [ "$SH_PATH" = "$SPAWNTERM_CONFIG" ] && ok "path parity ($SH_PATH)" || fail "path mismatch shell='$SH_PATH' py='$PY_PATH'"

echo "== unknown key: query treats as OFF, exit 1 =="
reset
assert_query run_shell "shell: unknown query -> OFF" spawnterm.nope 0 1
assert_query run_py    "py:    unknown query -> OFF" nope           0 1

echo "== unknown key: enable is a hard error (exit 2) =="
run_shell enable nope
[ "$RC" = "2" ] && ok "shell enable unknown rc=2" || fail "shell enable unknown rc=$RC"
run_py enable nope
[ "$RC" = "2" ] && ok "py enable unknown rc=2" || fail "py enable unknown rc=$RC"

echo "== no-args usage error (exit 2) =="
run_shell
[ "$RC" = "2" ] && ok "shell no-args rc=2" || fail "shell no-args rc=$RC"
run_py
[ "$RC" = "2" ] && ok "py no-args rc=2" || fail "py no-args rc=$RC"

echo "== importable is_enabled() for the daemon =="
reset
"$SHELL_BIN" enable cost_dashboard >/dev/null
IMP="$(cd "$HERE" && python3 -c 'import spawnterm_flag as f; print(f.is_enabled("cost_dashboard"), f.is_enabled("spawnterm.janitor"))')"
[ "$IMP" = "True False" ] && ok "is_enabled() import works ($IMP)" || fail "is_enabled import got '$IMP'"

echo
echo "==================================="
printf 'PASS=%d  FAIL=%d\n' "$PASS" "$FAIL"
echo "==================================="
[ "$FAIL" -eq 0 ]
