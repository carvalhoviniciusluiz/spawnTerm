#!/usr/bin/env bash
# Test suite for the spawnterm i18n foundation (#66).
#
# Exercises: the fallback chain (active -> en -> key), positional interpolation,
# `spawnterm-lang set/get` round-trip, that writing [settings] language preserves
# an existing [features] table (and vice-versa), shell<->Python output parity,
# the config-writer byte parity, no-orphan-keys (every pt-BR key exists in en),
# and the `system` locale resolution. Uses a throwaway $SPAWNTERM_CONFIG so it
# never touches the real ~/.config.
#
# Usage: bash spawnterm/i18n/tests/test_i18n.sh
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"          # spawnterm/i18n
I18N_SH="$HERE/spawnterm-i18n"
I18N_PY="$HERE/spawnterm_i18n.py"
LANG_SH="$HERE/spawnterm-lang"
LANG_PY="$HERE/spawnterm_lang.py"
FLAG_SH="$HERE/../flags/spawnterm-flag"
EN_JSON="$HERE/en.json"
PTBR_JSON="$HERE/pt-BR.json"

WORK="$(mktemp -d)"
export SPAWNTERM_CONFIG="$WORK/config.toml"
# Neutralize inherited locale so `system` resolution is deterministic here.
unset LC_ALL
export LANG="en_US.UTF-8"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }
ok()   { PASS=$((PASS + 1)); printf 'ok  : %s\n' "$1"; }
reset() { rm -f "$SPAWNTERM_CONFIG"; }

# assert_eq <label> <got> <want>
assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1 ('$2')"; else fail "$1: got '$2' want '$3'"; fi
}

# t_sh/t_py <key> [args...] -> echoes the localized string
t_sh() { "$I18N_SH" t "$@"; }
t_py() { python3 "$I18N_PY" t "$@"; }

echo "== default language is en (no config) =="
reset
assert_eq "shell lang default"  "$("$I18N_SH" lang)"        "en"
assert_eq "py    lang default"  "$(python3 "$I18N_PY" lang)" "en"
[ ! -f "$SPAWNTERM_CONFIG" ] && ok "read did not create a config file" || fail "read created a config file"

echo "== fallback: key present in active (pt-BR) =="
reset
"$LANG_SH" set pt-BR >/dev/null
assert_eq "shell pt-BR value"   "$(t_sh cap.messaging.name)" "Mensagens"
assert_eq "py    pt-BR value"   "$(t_py cap.messaging.name)" "Mensagens"

echo "== fallback: key MISSING in both -> returns the key itself =="
reset
assert_eq "shell missing->key (en)"    "$(t_sh no.such.key)"    "no.such.key"
assert_eq "py    missing->key (en)"     "$(t_py no.such.key)"    "no.such.key"
"$LANG_SH" set pt-BR >/dev/null
assert_eq "shell missing->key (pt-BR)"  "$(t_sh no.such.key)"    "no.such.key"
assert_eq "py    missing->key (pt-BR)"  "$(t_py no.such.key)"    "no.such.key"

echo "== fallback: active(pt-BR) missing a key -> en value (synthetic catalog) =="
# Build a private catalog dir where pt-BR is missing a key that en has, then run
# a copy of the helper against it (SD is the script's own dir, so copy scripts).
SANDBOX="$WORK/sandbox"; mkdir -p "$SANDBOX"
cp "$I18N_SH" "$I18N_PY" "$SANDBOX"/
printf '{\n  "only.en": "English only",\n  "both": "EN both"\n}\n' > "$SANDBOX/en.json"
printf '{\n  "both": "PT both"\n}\n' > "$SANDBOX/pt-BR.json"
reset; "$LANG_SH" set pt-BR >/dev/null
assert_eq "shell active-miss->en"   "$("$SANDBOX/spawnterm-i18n" t only.en)"        "English only"
assert_eq "py    active-miss->en"   "$(python3 "$SANDBOX/spawnterm_i18n.py" t only.en)" "English only"
assert_eq "shell active-hit(pt-BR)" "$("$SANDBOX/spawnterm-i18n" t both)"           "PT both"
assert_eq "py    active-hit(pt-BR)" "$(python3 "$SANDBOX/spawnterm_i18n.py" t both)" "PT both"

echo "== interpolation: {0} filled, out-of-range left intact =="
reset
assert_eq "shell interp gate.off" "$(t_sh gate.off messaging)" \
  "spawnterm.messaging is off. Enable it with: spawnterm-flag enable spawnterm.messaging"
assert_eq "py    interp gate.off" "$(t_py gate.off messaging)" \
  "spawnterm.messaging is off. Enable it with: spawnterm-flag enable spawnterm.messaging"
# Out-of-range placeholder left as-is (use synthetic catalog with {0} {1}).
printf '{\n  "two": "a {0} b {1} c"\n}\n' > "$SANDBOX/en.json"
printf '{}\n' > "$SANDBOX/pt-BR.json"
assert_eq "shell interp missing arg1" "$("$SANDBOX/spawnterm-i18n" t two X)"        "a X b {1} c"
assert_eq "py    interp missing arg1" "$(python3 "$SANDBOX/spawnterm_i18n.py" t two X)" "a X b {1} c"

echo "== lang set/get round-trip via \$SPAWNTERM_CONFIG =="
reset
"$LANG_SH" set pt-BR >/dev/null
assert_eq "shell get after set pt-BR" "$("$LANG_SH" get)"       "pt-BR"
assert_eq "py    get after set pt-BR" "$(python3 "$LANG_PY" get)" "pt-BR"
python3 "$LANG_PY" set en >/dev/null
assert_eq "shell get after py set en" "$("$LANG_SH" get)"       "en"
assert_eq "py    get after py set en" "$(python3 "$LANG_PY" get)" "en"

echo "== set language PRESERVES an existing [features] table =="
reset
"$FLAG_SH" enable messaging >/dev/null
"$FLAG_SH" enable broker >/dev/null
"$LANG_SH" set pt-BR >/dev/null
assert_eq "features: messaging still ON" "$("$FLAG_SH" spawnterm.messaging)" "1"
assert_eq "features: broker still ON"    "$("$FLAG_SH" spawnterm.broker)"    "1"
assert_eq "settings: language is pt-BR"  "$("$LANG_SH" get)"                 "pt-BR"
grep -q '^\[features\]$'  "$SPAWNTERM_CONFIG" && ok "config keeps [features] table" || fail "[features] table lost"
grep -q '^\[settings\]$'  "$SPAWNTERM_CONFIG" && ok "config has [settings] table"   || fail "[settings] table missing"

echo "== vice-versa: changing language again PRESERVES [features] (both tables coexist) =="
python3 "$LANG_PY" set en >/dev/null
assert_eq "features survive 2nd write" "$("$FLAG_SH" spawnterm.messaging)" "1"
assert_eq "language flipped to en"     "$("$LANG_SH" get)"                 "en"

echo "== no spurious [features] when none exist =="
reset
"$LANG_SH" set en >/dev/null
grep -q '^\[features\]$' "$SPAWNTERM_CONFIG" && fail "fabricated a [features] table" || ok "no spurious [features] table"
grep -q '^\[settings\]$' "$SPAWNTERM_CONFIG" && ok "wrote [settings] table" || fail "missing [settings]"

echo "== config-writer byte parity (shell vs python, features present) =="
reset
"$FLAG_SH" enable mcp >/dev/null
"$LANG_SH" set pt-BR >/dev/null
SH_FILE="$(cat "$SPAWNTERM_CONFIG")"
python3 "$LANG_PY" set pt-BR >/dev/null
PY_FILE="$(cat "$SPAWNTERM_CONFIG")"
if [ "$SH_FILE" = "$PY_FILE" ]; then ok "shell and python write identical config"; else fail "writer mismatch"; diff <(printf '%s' "$SH_FILE") <(printf '%s' "$PY_FILE"); fi

echo "== shell<->python output parity across every en key =="
reset
mismatch=0
KEYS="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1])).keys()))' "$EN_JSON")"
for k in $KEYS; do
  s="$(t_sh "$k")"; p="$(t_py "$k")"
  [ "$s" = "$p" ] || { mismatch=$((mismatch + 1)); printf '  parity diff en %s: shell=%s py=%s\n' "$k" "$s" "$p"; }
done
[ "$mismatch" -eq 0 ] && ok "shell==python for all en keys" || fail "$mismatch en-key parity mismatches"
"$LANG_SH" set pt-BR >/dev/null
mismatch=0
for k in $KEYS; do
  s="$(t_sh "$k")"; p="$(t_py "$k")"
  [ "$s" = "$p" ] || { mismatch=$((mismatch + 1)); printf '  parity diff pt-BR %s: shell=%s py=%s\n' "$k" "$s" "$p"; }
done
[ "$mismatch" -eq 0 ] && ok "shell==python for all keys (active pt-BR)" || fail "$mismatch pt-BR-key parity mismatches"

echo "== no orphan keys: every pt-BR key exists in en =="
ORPHANS="$(python3 -c 'import json,sys
en=set(json.load(open(sys.argv[1])))
pt=set(json.load(open(sys.argv[2])))
print("\n".join(sorted(pt-en)))' "$EN_JSON" "$PTBR_JSON")"
[ -z "$ORPHANS" ] && ok "pt-BR has no orphan keys" || { fail "pt-BR orphan keys:"; printf '%s\n' "$ORPHANS"; }

echo "== system resolution from locale =="
reset
"$LANG_SH" set system >/dev/null
assert_eq "system + LANG=pt* -> pt-BR (shell)" "$(LANG=pt_BR.UTF-8 LC_ALL= "$I18N_SH" lang)"        "pt-BR"
assert_eq "system + LANG=pt* -> pt-BR (py)"    "$(LANG=pt_BR.UTF-8 LC_ALL= python3 "$I18N_PY" lang)" "pt-BR"
assert_eq "system + LANG=en* -> en (shell)"    "$(LANG=en_US.UTF-8 LC_ALL= "$I18N_SH" lang)"        "en"
assert_eq "system + LANG=en* -> en (py)"       "$(LANG=en_US.UTF-8 LC_ALL= python3 "$I18N_PY" lang)" "en"

echo "== importable Python API =="
IMP="$(cd "$HERE" && SPAWNTERM_CONFIG="$SPAWNTERM_CONFIG" python3 -c 'import spawnterm_i18n as m; print(m.t("cap.messaging.name"), "|", m.active_language())')"
ok "import spawnterm_i18n.t/active_language works ($IMP)"

echo "== lang list = catalogs found =="
LIST_SH="$("$LANG_SH" list)"
LIST_PY="$(python3 "$LANG_PY" list)"
assert_eq "list parity shell==python" "$LIST_SH" "$LIST_PY"
printf '%s\n' "$LIST_SH" | grep -qx "en"    && ok "list includes en"    || fail "list missing en"
printf '%s\n' "$LIST_SH" | grep -qx "pt-BR" && ok "list includes pt-BR" || fail "list missing pt-BR"

echo "== exit codes =="
"$I18N_SH" >/dev/null 2>&1;               [ "$?" = "2" ] && ok "shell i18n no-args rc=2" || fail "shell i18n no-args rc"
python3 "$I18N_PY" >/dev/null 2>&1;        [ "$?" = "2" ] && ok "py i18n no-args rc=2"    || fail "py i18n no-args rc"
"$LANG_SH" set bogus >/dev/null 2>&1;      [ "$?" = "2" ] && ok "shell lang set bogus rc=2" || fail "shell lang set bogus rc"
python3 "$LANG_PY" set bogus >/dev/null 2>&1; [ "$?" = "2" ] && ok "py lang set bogus rc=2" || fail "py lang set bogus rc"
"$I18N_SH" t >/dev/null 2>&1;              [ "$?" = "2" ] && ok "shell i18n t no-key rc=2" || fail "shell i18n t no-key rc"

echo
echo "==================================="
printf 'PASS=%d  FAIL=%d\n' "$PASS" "$FAIL"
echo "==================================="
[ "$FAIL" -eq 0 ]
