#!/usr/bin/env bash
# Behavior tests for it2agent-install / it2agent-uninstall (#120).
#
# Asserts:
#   - install --dir <tmp> creates an executable wrapper for EVERY it2agent CLI
#     the repo enumerates dynamically, and each wrapper exec's the right target
#   - a wrapper actually works end to end (it2agent-flag list via the wrapper,
#     with an isolated IT2AGENT_CONFIG)
#   - install is idempotent (a second run neither errors nor duplicates)
#   - uninstall --dir <tmp> removes ONLY our wrappers; unrelated files survive
#   - dynamic enumeration: a brand-new it2agent-* file in the repo is picked up
#   - the umbrella dispatches `it2agent install` -> it2agent-install
#
# Run from anywhere: bash it2agent/tests/test_install.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"            # it2agent/
INSTALL="$ROOT/it2agent-install"
UNINSTALL="$ROOT/it2agent-uninstall"
UMBRELLA="$ROOT/it2agent"

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

# Isolated, self-cleaning workspace.
WORK="$(mktemp -d)"
BINDIR="$WORK/bin"
export IT2AGENT_CONFIG="$WORK/config.toml"
unset IT2AGENT_FORCE
FIXTURE=""
cleanup() {
	rm -rf "$WORK"
	[ -n "$FIXTURE" ] && rm -rf "$FIXTURE"
}
trap cleanup EXIT

echo "=== it2agent-install / it2agent-uninstall tests (#120) ==="
echo "install : $INSTALL"

echo
echo "--- 1. install creates an executable wrapper for every enumerated CLI ---"
inst_out="$("$INSTALL" --dir "$BINDIR" 2>&1)"
assert_exit "install exits 0" 0 sh "$INSTALL" --dir "$BINDIR"
assert_contains "prints an install summary" "Installed" "$inst_out"

# The set of expected wrappers is exactly the dynamic enumeration (--list).
missing=""
notexec=""
badtarget=""
n_expected=0
# POSIX: list-then-loop via a tmpfile (macOS /bin/sh is bash 3.2 in POSIX mode
# and rejects `done < <(...)` process substitution). The redirect keeps the loop
# in the current shell so n_expected/missing/notexec/badtarget persist.
_cli_list="$(mktemp)"
"$INSTALL" --list > "$_cli_list"
while IFS= read -r cli; do
	[ -n "$cli" ] || continue
	n_expected=$((n_expected + 1))
	name="$(basename "$cli")"
	w="$BINDIR/$name"
	if [ ! -f "$w" ]; then missing="$missing $name"; continue; fi
	[ -x "$w" ] || notexec="$notexec $name"
	# The wrapper must exec the exact source CLI it wraps.
	grep -q "exec \"$cli\" \"\$@\"" "$w" || badtarget="$badtarget $name"
done < "$_cli_list"
rm -f "$_cli_list"

if [ -z "$missing" ]; then green "a wrapper exists for every enumerated CLI ($n_expected)"; else red "missing wrappers:$missing"; fi
if [ -z "$notexec" ]; then green "every wrapper is executable"; else red "not executable:$notexec"; fi
if [ -z "$badtarget" ]; then green "every wrapper exec's its exact source target"; else red "wrong target:$badtarget"; fi

echo
echo "--- 2. a wrapper works end to end (it2agent-flag list) ---"
flag_out="$("$BINDIR/it2agent-flag" list 2>&1)"
assert_exit "it2agent-flag via wrapper exits 0" 0 "$BINDIR/it2agent-flag" list
assert_contains "flag list output looks like flags" "agent." "$flag_out"

echo
echo "--- 3. idempotent: a second run does not error or duplicate ---"
before="$(ls -1 "$BINDIR" | sort)"
n_before="$(ls -1 "$BINDIR" | wc -l | tr -d ' ')"
assert_exit "second install exits 0" 0 sh "$INSTALL" --dir "$BINDIR"
after="$(ls -1 "$BINDIR" | sort)"
n_after="$(ls -1 "$BINDIR" | wc -l | tr -d ' ')"
if [ "$before" = "$after" ] && [ "$n_before" = "$n_after" ]; then
	green "wrapper set unchanged after re-run ($n_after files, no dupes)"
else
	red "wrapper set changed on re-run ($n_before -> $n_after)"
fi

echo
echo "--- 4. uninstall removes ONLY our wrappers ---"
# Seed unrelated files that must survive: a plain file, and a foreign wrapper
# that exec's something outside the repo.
echo "not ours" > "$BINDIR/some-unrelated-tool"
printf '#!/bin/sh\nexec "/usr/bin/true" "$@"\n' > "$BINDIR/foreign-wrapper"
chmod +x "$BINDIR/foreign-wrapper"
uninst_out="$("$UNINSTALL" --dir "$BINDIR" 2>&1)"
assert_contains "uninstall prints a removal summary" "Removed" "$uninst_out"
if [ -f "$BINDIR/some-unrelated-tool" ]; then green "unrelated plain file survived"; else red "unrelated plain file was deleted"; fi
if [ -f "$BINDIR/foreign-wrapper" ]; then green "foreign wrapper (target outside repo) survived"; else red "foreign wrapper was deleted"; fi
if [ ! -f "$BINDIR/it2agent-flag" ]; then green "our wrapper (it2agent-flag) was removed"; else red "our wrapper was not removed"; fi
if [ ! -f "$BINDIR/it2agent" ]; then green "our umbrella wrapper was removed"; else red "umbrella wrapper was not removed"; fi

echo
echo "--- 5. dynamic enumeration: a new it2agent-* file is picked up ---"
FIXTURE="$ROOT/tests/fixture-cli-$$"
mkdir -p "$FIXTURE"
NEWCLI="$FIXTURE/it2agent-zztest-$$"
printf '#!/bin/sh\necho zztest-ok\n' > "$NEWCLI"
chmod +x "$NEWCLI"
list_out="$("$INSTALL" --list)"
assert_contains "enumeration discovers the new CLI" "it2agent-zztest-$$" "$list_out"
"$INSTALL" --dir "$BINDIR" >/dev/null 2>&1
if [ -x "$BINDIR/it2agent-zztest-$$" ]; then green "install created a wrapper for the new CLI"; else red "new CLI was not wrapped"; fi
# Clean the fixture out of the repo immediately.
rm -rf "$FIXTURE"; FIXTURE=""

echo
echo "--- 6. umbrella dispatches install/uninstall ---"
umb_out="$(sh "$UMBRELLA" install --dir "$BINDIR" 2>&1)"
assert_contains "it2agent install delegates to it2agent-install" "Installed" "$umb_out"
assert_exit "it2agent uninstall exits 0" 0 sh "$UMBRELLA" uninstall --dir "$BINDIR"
assert_exit "install -h exits 0" 0 sh "$INSTALL" -h
assert_exit "install bad arg exits 2" 2 sh "$INSTALL" --bogus

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]
