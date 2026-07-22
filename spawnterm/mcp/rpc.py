#!/usr/bin/env python3
"""Pure JSON-RPC 2.0 / MCP request dispatch for the spawnTerm MCP surface (#18).

MCP is JSON-RPC 2.0 over a transport (here: stdio, newline-framed). This module
is the **pure** protocol layer: it turns one decoded request object into one
response object (or ``None`` for a notification), with no stdin/stdout and no
sockets. The stdio read/write loop lives in ``spawnterm_mcp``; the tool handlers
and broker client live behind :class:`tools.Deps`. Keeping dispatch pure means a
unit test can feed it request dicts and assert the exact JSON-RPC replies.

Implemented methods (the minimal correct subset an MCP client needs):

  * ``initialize``      → protocolVersion + capabilities.tools + serverInfo.
  * ``tools/list``      → the six tool descriptors from :data:`tools.TOOLS`.
  * ``tools/call``      → run a handler, wrap its result as an MCP tool result
                          (``content`` text block + ``structuredContent``;
                          ``isError`` reflects the handler's ``ok`` flag, or a
                          raised exception such as an unreachable broker).
  * ``resources/list``  → the one guide resource (AGENT_GUIDE.md).
  * ``resources/read``  → the guide's text for its URI (#56 single source).
  * notifications (no ``id``, e.g. ``notifications/initialized``) → no response.

Everything else is a proper JSON-RPC error (``-32601`` method not found,
``-32600`` invalid request, ``-32700`` parse error) so a malformed request never
crashes the loop.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import tools
from tools import Deps

# The MCP protocol revision we implement. We echo the client's requested version
# back when it sends one (per the handshake contract), else advertise this.
PROTOCOL_VERSION = "2024-11-05"

SERVER_INFO = {"name": "spawnterm", "version": "0.1.0"}

# JSON-RPC error codes (subset we use).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


# --------------------------------------------------------------------------- #
# Response builders
# --------------------------------------------------------------------------- #


def _result(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_result(payload: dict) -> dict:
    """Wrap a handler result dict as an MCP ``tools/call`` result.

    The human-readable ``content`` is the JSON-serialized payload as one text
    block; ``structuredContent`` carries the same payload for machine use.
    ``isError`` is True unless the payload reports ``ok`` truthy.
    """
    text = json.dumps(payload, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": not bool(payload.get("ok")),
    }


# --------------------------------------------------------------------------- #
# Method handlers
# --------------------------------------------------------------------------- #


# A one-line pointer surfaced in the initialize handshake so a connecting agent
# immediately knows the guide exists (without duplicating it — the text lives in
# AGENT_GUIDE.md, reachable via the ``help`` tool or the guide resource).
INSTRUCTIONS = (
    "spawnTerm orchestration tools. Every capability is a feature flag, default "
    "OFF. Call the 'help' tool (or read the '"
    + tools.GUIDE_URI
    + "' resource) for the full capability guide: flags, commands, and examples."
)


def _handle_initialize(params: dict) -> dict:
    """MCP handshake: advertise tool + resource capability, server info, guide.

    Echoes the client's ``protocolVersion`` when it supplies a string one (keeps
    strict clients happy), otherwise advertises :data:`PROTOCOL_VERSION`. Also
    advertises the ``resources`` capability and includes an ``instructions``
    string pointing at the capability guide (#56) — both read the one
    ``AGENT_GUIDE.md`` and never duplicate it.
    """
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False},
        },
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


# The one resource we expose: the capability guide, read from AGENT_GUIDE.md.
GUIDE_RESOURCE = {
    "uri": tools.GUIDE_URI,
    "name": "spawnTerm agent capability guide",
    "description": "AGENT_GUIDE.md — the single source of truth for every "
    "spawnTerm capability, its feature flag, command/MCP tool, and example.",
    "mimeType": "text/markdown",
}


def _handle_resources_read(params: dict) -> dict:
    """Serve ``resources/read`` for the guide URI (reads AGENT_GUIDE.md).

    Any other URI is reported as an empty contents list (unknown resource); a
    missing guide file surfaces as an empty read rather than a crash.
    """
    uri = params.get("uri") if isinstance(params, dict) else None
    if uri != tools.GUIDE_URI:
        return {"contents": []}
    try:
        text = tools.read_guide()
    except OSError:
        return {"contents": []}
    return {
        "contents": [
            {"uri": tools.GUIDE_URI, "mimeType": "text/markdown", "text": text}
        ]
    }


def _handle_tools_call(params: dict, deps: Deps) -> dict:
    """Run one tool and wrap the outcome as an MCP tool result.

    A handler exception (e.g. the broker socket is down) is caught here and
    reported as an ``isError`` tool result — the loop never crashes on a
    backend failure.
    """
    name = params.get("name") if isinstance(params, dict) else None
    if not isinstance(name, str) or not name:
        return _tool_result(
            {"ok": False, "error": "bad_request", "message": "missing tool 'name'"}
        )
    arguments = params.get("arguments") if isinstance(params, dict) else None
    try:
        payload = tools.call_tool(name, arguments, deps)
    except Exception as exc:  # noqa: BLE001 - backend failure => tool error, never crash
        payload = {
            "ok": False,
            "error": "backend_unavailable",
            "message": f"{type(exc).__name__}: {exc}",
        }
    return _tool_result(payload)


def handle_request(request: Any, deps: Deps) -> Optional[dict]:
    """Map one decoded JSON-RPC request to a response object (or None).

    Returns ``None`` for notifications (requests without an ``id``), which by
    the JSON-RPC spec get no reply. Malformed envelopes and unknown methods
    return proper JSON-RPC error objects.
    """
    if not isinstance(request, dict):
        return _error(None, INVALID_REQUEST, "request must be a JSON object")
    if request.get("jsonrpc") != "2.0":
        return _error(request.get("id"), INVALID_REQUEST, "jsonrpc must be '2.0'")

    method = request.get("method")
    if not isinstance(method, str) or not method:
        return _error(request.get("id"), INVALID_REQUEST, "missing method")

    # A request without an id is a notification: act on nothing, reply nothing.
    is_notification = "id" not in request
    req_id = request.get("id")
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}

    if is_notification:
        # We accept (and ignore) any notification, e.g. notifications/initialized.
        return None

    if method == "initialize":
        return _result(req_id, _handle_initialize(params))
    if method == "tools/list":
        return _result(req_id, {"tools": tools.tool_descriptors()})
    if method == "tools/call":
        return _result(req_id, _handle_tools_call(params, deps))
    if method == "resources/list":
        return _result(req_id, {"resources": [GUIDE_RESOURCE]})
    if method == "resources/read":
        return _result(req_id, _handle_resources_read(params))
    if method == "ping":
        # MCP utility ping: empty result.
        return _result(req_id, {})

    return _error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")


def dispatch_line(line: str, deps: Deps) -> Optional[str]:
    """Parse one JSON-RPC line, dispatch it, and return the response line.

    Returns ``None`` when there is nothing to send (a notification, or a blank
    line). A JSON parse failure yields a ``-32700`` error response line. Never
    raises — this is the single funnel the stdio loop calls per input line.
    """
    if line is None or not line.strip():
        return None
    try:
        request = json.loads(line)
    except (ValueError, TypeError):
        response: Optional[dict] = _error(None, PARSE_ERROR, "invalid JSON")
        return json.dumps(response)
    response = handle_request(request, deps)
    if response is None:
        return None
    return json.dumps(response)
