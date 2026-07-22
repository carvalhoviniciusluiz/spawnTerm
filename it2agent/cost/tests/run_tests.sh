#!/usr/bin/env bash
# Run the it2agent cost-dashboard unit tests (#16). Pure Python stdlib; no
# iterm2, no network. Covers costlib (parsing/aggregation/cost/idle-burn/caps),
# the CLI + flag gate, and the pure status-bar formatter. New test_*.py files
# are auto-discovered below.
#
# Usage: bash it2agent/cost/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag-gate tests are deterministic.
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
unset IT2AGENT_FORCE
unset IT2AGENT_COST_SOURCE
unset IT2AGENT_COST_PRICES

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v
