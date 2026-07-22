#!/usr/bin/env bash
# Run the it2agent daemon unit tests. Pure Python; no iterm2 required.
# Covers: registry (#26), envelope (#26), flag gate/purity (#26), router (#28,
# test_router.py), and the status-bar dashboard formatter (#29, test_dashboard.py).
# New test_*.py files are auto-discovered below.
#
# Usage: bash it2agent/daemon/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag-gate tests are deterministic.
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
unset IT2AGENT_FORCE

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v
