#!/usr/bin/env bash
# Behavior tests for spawnterm-help + the spawnterm umbrella (#56).
#
# Asserts:
#   - spawnterm-help prints AGENT_GUIDE.md verbatim (single source of truth, no
#     duplication) followed by a live "Currently enabled" flags section built
#     from `spawnterm-flag list`
#   - the flags section reflects an isolated SPAWNTERM_CONFIG temp file
#   - help is NEVER gated (works with all flags OFF)
#   - -h/--help exits 0; a stray argument exits 2
#   - the umbrella `spawnterm help` delegates to spawnterm-help
#
# Run from anywhere: bash spawnterm/tests/test_help.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"            # spawnterm/
HELP="$ROOT/spawnterm-help"
UMBRELLA="$ROOT/spawnterm"
FLAG="$ROOT/flags/spawnterm-flag"
GUIDE="$ROOT/AGENT_GUIDE.md"

pass=0
fail=0
green() { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
red() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

assert_contains() {
	case "$3" in
		*"$2"*) green "$1" ;;
		*)      red "$1 (missing: $2)" ;;
	esac
}

assert_exit() {
	local label="$1" want="$2"; shift 2
	"$@" >/dev/null 2>&1
	local got=$?
	if [ "$got" = "$want" ]; then green "$label (exit $got)"; else red "$label (want $want, got $got)"; fi
}

# Isolate flag state from the real ~/.config.
export SPAWNTERM_CONFIG="$(mktemp -d)/config.toml"
unset SPAWNTERM_FORCE

echo "=== spawnterm-help behavior tests (#56) ==="
echo "help : $HELP"

echo
echo "--- 1. prints the guide verbatim (single source of truth) ---"
# All flags OFF (help is never gated): still prints the guide.
out="$(sh "$HELP")"
assert_contains "prints the guide title"          "# spawnTerm — agent capability guide" "$out"
assert_contains "prints a capability (status board)" "spawnterm.status_board"             "$out"
assert_contains "prints the MCP help tool row"    "return this guide"                    "$out"
# No duplication: the guide portion equals AGENT_GUIDE.md byte-for-byte.
guide_text="$(cat "$GUIDE")"
case "$out" in
	*"$guide_text"*) green "guide is embedded verbatim (no drift)" ;;
	*)               red "guide text does not match AGENT_GUIDE.md" ;;
esac

echo
echo "--- 2. live 'Currently enabled' section reflects SPAWNTERM_CONFIG ---"
off_out="$(sh "$HELP")"
assert_contains "shows the live section header" "## Currently enabled (live)" "$off_out"
assert_contains "all OFF -> says none enabled"  "No feature flags are enabled" "$off_out"
# Enable two flags in the isolated config; the section must list exactly those.
"$FLAG" enable spawnterm.broker >/dev/null
"$FLAG" enable spawnterm.review >/dev/null
on_out="$(sh "$HELP")"
assert_contains "lists an enabled flag (broker)" "spawnterm.broker" "$on_out"
assert_contains "lists an enabled flag (review)" "spawnterm.review" "$on_out"
case "$on_out" in
	*"spawnterm.mcp                  on"*) red "listed a flag that is OFF as on" ;;
	*)                                     green "does not list OFF flags as enabled" ;;
esac

echo
echo "--- 3. exit codes + never gated ---"
assert_exit "-h exits 0"              0 sh "$HELP" -h
assert_exit "--help exits 0"          0 sh "$HELP" --help
assert_exit "happy path exits 0"      0 sh "$HELP"
assert_exit "stray argument exits 2"  2 sh "$HELP" bogus

echo
echo "--- 4. umbrella dispatcher ---"
umb_out="$(sh "$UMBRELLA" help)"
assert_contains "spawnterm help delegates to spawnterm-help" "# spawnTerm — agent capability guide" "$umb_out"
assert_exit "spawnterm --help exits 0"        0 sh "$UMBRELLA" --help
assert_exit "spawnterm (no args) exits 0"     0 sh "$UMBRELLA"
assert_exit "spawnterm unknown subcmd exits 2" 2 sh "$UMBRELLA" nope-not-a-tool

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]
