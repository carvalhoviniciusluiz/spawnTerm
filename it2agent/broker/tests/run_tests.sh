#!/usr/bin/env bash
# Run the it2agent broker unit tests (#34). Pure Python + stdlib only; no pip
# deps, no iTerm2, no external services. Covers: path resolution, sqlite schema
# (WAL/busy-timeout/idempotent migration), wire-protocol round-trips, op-dispatch
# (ping/health/unknown/extensibility), the agent.broker flag gate + purity,
# and an end-to-end unix-socket round-trip (socket in a tmpdir). New test_*.py
# files are auto-discovered.
#
# Usage: bash it2agent/broker/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config and ~/.local/state so tests are deterministic.
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
unset IT2AGENT_FORCE
unset IT2AGENT_BROKER_DB
unset IT2AGENT_BROKER_SOCK

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v
