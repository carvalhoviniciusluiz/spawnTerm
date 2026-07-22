#!/usr/bin/env bash
# Run the it2agent MCP surface unit tests (#18). Pure Python + stdlib only; no
# pip deps, no iTerm2, no sockets, no live services. Covers: the six tool
# handlers (arguments -> broker/spawn op, via a mock broker + mock launcher), the
# JSON-RPC/MCP dispatch (initialize / tools/list schemas / tools/call / malformed
# -> JSON-RPC error / notifications), the agent.mcp flag gate + purity, and a
# framed stdio round-trip. New test_*.py files are auto-discovered.
#
# Usage: bash it2agent/mcp/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag-gate tests are deterministic.
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
unset IT2AGENT_FORCE

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v
