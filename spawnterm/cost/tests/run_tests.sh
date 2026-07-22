#!/usr/bin/env bash
# Run the spawnTerm cost-dashboard unit tests (#16). Pure Python stdlib; no
# iterm2, no network. Covers costlib (parsing/aggregation/cost/idle-burn/caps),
# the CLI + flag gate, and the pure status-bar formatter. New test_*.py files
# are auto-discovered below.
#
# Usage: bash spawnterm/cost/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag-gate tests are deterministic.
export SPAWNTERM_CONFIG="$(mktemp -d)/config.toml"
unset SPAWNTERM_FORCE
unset SPAWNTERM_COST_SOURCE
unset SPAWNTERM_COST_PRICES

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v
