#!/usr/bin/env bash
# Run the spawnTerm daemon unit tests. Pure Python; no iterm2 required.
# Covers: registry (#26), envelope (#26), flag gate/purity (#26), and the
# status-bar dashboard formatter (#29, test_dashboard.py). New test_*.py files
# are auto-discovered below.
#
# Usage: bash spawnterm/daemon/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag-gate tests are deterministic.
export SPAWNTERM_CONFIG="$(mktemp -d)/config.toml"
unset SPAWNTERM_FORCE

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v
