#!/usr/bin/env bash
# Behavior tests for spawnterm-spawn (Tier 0.4, #10).
#
# spawnterm-spawn drives iTerm2 via osascript, which we cannot exercise
# headless, so every test runs in --dry-run mode and asserts on the plan it
# prints: the resolved cwd, the identity emit calls, and the AppleScript.
#
# Asserts:
#   - default cwd == the spawner's $PWD
#   - --dir <path> and --home override the cwd
#   - identity emits shell out to the MERGED spawnterm-emit (not re-implemented)
#   - those emits, when run, set the DOT-FREE user vars (agent_role/task/status)
#   - --help exits 0; missing command / bad flags error with exit 2
#   - the generated AppleScript parses (osacompile), when available
#
# Run from anywhere: bash spawnterm/spawn/tests/test_spawn.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SPAWN_DIR="$(dirname "$HERE")"
SPAWN="$SPAWN_DIR/spawnterm-spawn"
EMIT="$(cd "$SPAWN_DIR/../emit" && pwd)/spawnterm-emit"

pass=0
fail=0
green() { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
red() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

# assert_contains <label> <needle> <haystack>
assert_contains() {
	case "$3" in
		*"$2"*) green "$1" ;;
		*)      red "$1 (missing: $2)" ;;
	esac
}

# assert_exit <label> <want> <cmd...>
assert_exit() {
	local label="$1" want="$2"; shift 2
	"$@" >/dev/null 2>&1
	local got=$?
	if [ "$got" = "$want" ]; then green "$label (exit $got)"; else red "$label (want $want, got $got)"; fi
}

echo "=== spawnterm-spawn behavior tests (dry-run) ==="
echo "spawn: $SPAWN"
echo "emit : $EMIT"

echo
echo "--- 1. cwd resolution ---"
# Default: inherit the spawner's $PWD. Run from a known directory.
WORKDIR="$(mktemp -d)"
default_out="$(cd "$WORKDIR" && sh "$SPAWN" --role worker --dry-run -- true)"
assert_contains "default cwd inherits spawner \$PWD" "resolved cwd : $WORKDIR" "$default_out"
assert_contains "default cwd -> cd into \$PWD"        "cd '$WORKDIR'"           "$default_out"

# --dir overrides.
dir_out="$(sh "$SPAWN" --dir /tmp/spawn-target --role worker --dry-run -- true)"
assert_contains "--dir overrides cwd"        "resolved cwd : /tmp/spawn-target" "$dir_out"
assert_contains "--dir=... form overrides"   "resolved cwd : /tmp/other" \
	"$(sh "$SPAWN" --dir=/tmp/other --dry-run -- true)"

# --home overrides to $HOME.
home_out="$(sh "$SPAWN" --home --role worker --dry-run -- true)"
assert_contains "--home overrides cwd to \$HOME" "resolved cwd : $HOME" "$home_out"

echo
echo "--- 2. identity emits use the merged spawnterm-emit + dot-free vars ---"
id_out="$(sh "$SPAWN" --role tech-lead --task 'build #10' --status idle --dry-run -- claude)"
# Every identity facet is a call to the MERGED spawnterm-emit (absolute path),
# not a re-implemented escape code.
assert_contains "role emit shells out to merged spawnterm-emit"   "$EMIT' role 'tech-lead'"   "$id_out"
assert_contains "task emit shells out to merged spawnterm-emit"   "$EMIT' task 'build #10'"    "$id_out"
assert_contains "status emit shells out to merged spawnterm-emit" "$EMIT' status 'idle'"       "$id_out"
assert_contains "color derived from status"                       "$EMIT' color 'idle'"        "$id_out"
assert_contains "badge emit shells out to merged spawnterm-emit"  "$EMIT' badge"               "$id_out"
# No hand-rolled escape codes in spawn's own output (identity is delegated).
case "$id_out" in
	*SetUserVar*|*1337*) red "spawn re-implemented escape codes (should delegate to emit)" ;;
	*)                   green "spawn does not re-implement escape codes" ;;
esac
# The emit calls, when actually run, set the DOT-FREE user vars agent_role /
# agent_task / agent_status (iTerm2 forbids '.' in a SetUserVar key). Verify the
# mechanism against the real emit (bypassing its gate).
emit_role="$(SPAWNTERM_FORCE=1 sh "$EMIT" role tech-lead | cat -v)"
assert_contains "emit role -> dot-free user var agent_role"     "SetUserVar=agent_role="   "$emit_role"
case "$emit_role" in
	*agent.role*) red "emit produced a DOTTED var (agent.role) — must be dot-free" ;;
	*)            green "emit role has no dotted agent.role" ;;
esac
emit_status="$(SPAWNTERM_FORCE=1 sh "$EMIT" status idle | cat -v)"
assert_contains "emit status -> dot-free user var agent_status" "SetUserVar=agent_status=" "$emit_status"

echo
echo "--- 3. gate forwarding ---"
# Default: no bypass; the plan says emit self-gates.
assert_contains "default: emit self-gates (no bypass)" "gate bypass  : no" \
	"$(sh "$SPAWN" --role x --dry-run -- true)"
# --no-gate forwards --no-gate into every emit call.
nogate_out="$(sh "$SPAWN" --role x --no-gate --dry-run -- true)"
assert_contains "--no-gate forwarded to emit" "$EMIT' --no-gate role 'x'" "$nogate_out"
# SPAWNTERM_FORCE=1 in the spawner env is forwarded as --no-gate.
force_out="$(SPAWNTERM_FORCE=1 sh "$SPAWN" --role x --dry-run -- true)"
assert_contains "SPAWNTERM_FORCE=1 forwarded as --no-gate" "$EMIT' --no-gate role 'x'" "$force_out"

echo
echo "--- 4. the command to run is preserved as trailing args ---"
cmd_out="$(sh "$SPAWN" --role x --dry-run -- claude --resume --model opus)"
assert_contains "trailing command preserved" "'claude' '--resume' '--model' 'opus'" "$cmd_out"

echo
echo "--- 5. exit codes ---"
assert_exit "--help exits 0"                 0 sh "$SPAWN" --help
assert_exit "missing command exits 2"        2 sh "$SPAWN" --role x
assert_exit "unknown option exits 2"         2 sh "$SPAWN" --bogus -- true
assert_exit "bad status exits 2"             2 sh "$SPAWN" --status wat -- true
assert_exit "--home + --dir conflict exits 2" 2 sh "$SPAWN" --home --dir /tmp -- true
assert_exit "dry-run happy path exits 0"     0 sh "$SPAWN" --role x --dry-run -- true

echo
echo "--- 6. generated AppleScript parses ---"
if command -v osacompile >/dev/null 2>&1; then
	AS_FILE="$(mktemp).applescript"
	sh "$SPAWN" --role worker --task "o'brien build #10" --dry-run -- claude --resume \
		| awk '/AppleScript that WOULD run:/{f=1;next} f' | sed 's/^    //' > "$AS_FILE"
	if osacompile -o /dev/null "$AS_FILE" >/dev/null 2>&1; then
		green "AppleScript compiles (osacompile), even with an apostrophe in --task"
	else
		red "AppleScript failed to compile"
	fi
	rm -f "$AS_FILE"
else
	printf '  \033[33mNOTE\033[0m osacompile not available; skipping AppleScript parse check\n'
fi

rm -rf "$WORKDIR"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]
