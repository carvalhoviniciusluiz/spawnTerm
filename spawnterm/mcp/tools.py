#!/usr/bin/env python3
"""Pure tool registry + handlers for the spawnTerm MCP surface (#18).

This is the **pure, unit-tested heart** of the MCP server. It owns two things
and nothing else:

  1. :data:`TOOLS` — the ordered registry mapping a tool name to its JSON-Schema
     input contract, human description, and handler. This is what ``tools/list``
     serializes and what ``tools/call`` dispatches on.
  2. The six handlers. Each is a pure function ``handler(arguments, deps)`` that
     validates its arguments and maps them onto exactly one Tier 1 spawn plan or
     Tier 2 broker op, returning a JSON-friendly result dict carrying an ``ok``
     boolean. They do **no** transport: the broker client and the spawn launcher
     are injected via :class:`Deps`, so a unit test drives every handler with a
     mock broker / mock launcher and asserts the op it produced.

Nothing here imports a socket, asyncio, ``iterm2``, or reads stdin/stdout. The
JSON-RPC/stdio loop (``server`` / ``spawnterm_mcp``) is the only impure layer;
it wraps these results into MCP ``content`` blocks. This mirrors the rest of the
repo: pure logic decoupled from I/O (see ``broker/mailbox.py``, ``daemon/spawn.py``).

Backing (see ``spawnterm/docs/design.md``): spawnTerm's durable state lives in
the Tier 2 broker (sqlite mailbox/registry/handoff over a unix socket) and the
Tier 1 daemon owns iTerm2 spawning. The MCP tools are thin wrappers — they do
not reimplement transport or state.

    tool           backing op(s)
    ------------   ------------------------------------------------------------
    spawn          daemon spawn plan (build_spawn_plan) + injected launcher,
                   plus a best-effort broker ``register`` when an id is given
    assign         broker ``register`` (upsert role/task/capabilities)
    handoff        broker ``handoff_put`` (append-only state record)
    send_message   broker ``send`` (durable, ack'd mailbox)
    status         broker ``handoff_get`` (latest handoff/state for an agent)
    list_agents    broker ``query`` (registry, role/alive/capability filters)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# The pure spawn-plan builder lives in the Tier 1 daemon package. It imports no
# iTerm2 (see daemon/spawn.py), so pulling it in here keeps this module pure.
# Added to sys.path lazily, mirroring how daemon/bridge.py reaches the broker.
_DAEMON_DIR = Path(__file__).resolve().parent.parent / "daemon"
if str(_DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DAEMON_DIR))

from spawn import SpawnPlan, SpawnPlanError, build_spawn_plan  # type: ignore  # noqa: E402


# --------------------------------------------------------------------------- #
# Injected dependencies (the only impure surfaces, both mockable in tests)
# --------------------------------------------------------------------------- #

# A spawn launcher opens the actual iTerm2 tab. It is injected so this module
# never imports iterm2: production wires a subprocess launcher (spawnterm_mcp),
# tests wire a recording mock. Signature: (arguments, command, plan) -> result.
SpawnLauncher = Callable[[dict, str, SpawnPlan], dict]


@dataclass
class Deps:
    """Everything a handler needs from the outside world, all injectable.

    ``broker`` is anything with ``.request(op: dict) -> dict`` (a real
    :class:`client.BrokerClient` in production, a fake in tests). ``spawn`` is
    the :data:`SpawnLauncher`. ``build_plan`` defaults to the pure
    :func:`spawn.build_spawn_plan` and is overridable for tests.
    """

    broker: Any
    spawn: SpawnLauncher
    build_plan: Callable[..., SpawnPlan] = build_spawn_plan


# --------------------------------------------------------------------------- #
# Result + validation helpers
# --------------------------------------------------------------------------- #


def _ok(**payload: Any) -> dict:
    """A successful tool result (``ok`` is True)."""
    return {"ok": True, **payload}


def _bad_request(message: str, **extra: Any) -> dict:
    """A validation failure result (``ok`` is False, ``error`` is bad_request)."""
    return {"ok": False, "error": "bad_request", "message": message, **extra}


def _require_str(arguments: dict, key: str) -> str:
    """Return a required non-empty string argument or raise ``ValueError``."""
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or non-string '{key}'")
    return value


def _optional_str(arguments: dict, key: str) -> Optional[str]:
    """Return an optional string argument (None if absent); reject wrong type."""
    if key not in arguments or arguments[key] is None:
        return None
    value = arguments[key]
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string")
    return value


def _optional_bool(arguments: dict, key: str) -> Optional[bool]:
    """Return an optional bool argument (None if absent); reject wrong type."""
    if key not in arguments or arguments[key] is None:
        return None
    value = arguments[key]
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be a boolean")
    return value


def _optional_str_list(arguments: dict, key: str) -> Optional[list[str]]:
    """Return an optional list-of-strings argument (None if absent)."""
    if key not in arguments or arguments[key] is None:
        return None
    value = arguments[key]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"'{key}' must be a list of strings")
    return value


def _command_string(value: Any) -> str:
    """Coerce a command argument (string or list-of-strings) to one string.

    A list is shell-joined so the launcher receives a single command line,
    matching how the daemon spawn CLI takes ``-- <command> [args...]``.
    """
    import shlex

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("'command' must be a non-empty string")
        return text
    if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
        return shlex.join(value)
    raise ValueError("'command' must be a non-empty string or list of strings")


# --------------------------------------------------------------------------- #
# Handlers (pure: arguments + injected deps -> result dict)
# --------------------------------------------------------------------------- #


def handle_spawn(arguments: dict, deps: Deps) -> dict:
    """Create/launch an agent: build the Tier 1 spawn plan, then launch it.

    Reuses the pure :func:`spawn.build_spawn_plan` (cwd precedence + dot-free
    ``user.agent_*`` identity vars), invokes the injected launcher to open the
    tab, and — when an ``id`` is given — best-effort registers the agent in the
    Tier 2 broker registry so ``list_agents`` / ``status`` can immediately see
    it. Broker failure never fails the spawn (launching is the primary action).
    """
    try:
        command = _command_string(arguments.get("command"))
        cwd = _optional_str(arguments, "cwd")
        use_home = bool(_optional_bool(arguments, "home") or False)
        agent_id = _optional_str(arguments, "id") or ""
        role = _optional_str(arguments, "role") or ""
        task = _optional_str(arguments, "task") or ""
        status = _optional_str(arguments, "status") or "busy"
    except ValueError as exc:
        return _bad_request(str(exc))

    import os

    try:
        plan = deps.build_plan(
            spawner_cwd=os.getcwd(),
            dir_override=cwd,
            use_home=use_home,
            home=os.path.expanduser("~"),
            agent_id=agent_id,
            role=role,
            task=task,
            status=status,
            tag_identity=True,
        )
    except SpawnPlanError as exc:
        return _bad_request(str(exc))

    launch = deps.spawn(arguments, command, plan)

    registered = None
    register_error = None
    if agent_id:
        op = {"op": "register", "session_id": agent_id, "alive": True}
        if role:
            op["role"] = role
        if task:
            op["task"] = task
        caps = None
        try:
            caps = _optional_str_list(arguments, "capabilities")
        except ValueError:
            caps = None
        if caps is not None:
            op["capabilities"] = caps
        try:
            resp = deps.broker.request(op)
            registered = bool(isinstance(resp, dict) and resp.get("ok"))
        except Exception as exc:  # noqa: BLE001 - broker down must not fail spawn
            registered = False
            register_error = str(exc)

    return _ok(
        command=command,
        plan={"cwd": plan.cwd, "variables": plan.variables, "tagged": plan.tagged},
        launch=launch,
        registered=registered,
        register_error=register_error,
    )


def handle_assign(arguments: dict, deps: Deps) -> dict:
    """Assign a task/role to an agent → broker ``register`` (registry upsert)."""
    try:
        agent_id = _require_str(arguments, "agent_id")
        role = _optional_str(arguments, "role")
        task = _optional_str(arguments, "task")
        capabilities = _optional_str_list(arguments, "capabilities")
        alive = _optional_bool(arguments, "alive")
    except ValueError as exc:
        return _bad_request(str(exc))

    op: dict[str, Any] = {"op": "register", "session_id": agent_id}
    if role is not None:
        op["role"] = role
    if task is not None:
        op["task"] = task
    if capabilities is not None:
        op["capabilities"] = capabilities
    op["alive"] = True if alive is None else alive
    resp = deps.broker.request(op)
    return _broker_result(resp)


def handle_handoff(arguments: dict, deps: Deps) -> dict:
    """Write a handoff/state record → broker ``handoff_put`` (append-only)."""
    try:
        agent_id = _require_str(arguments, "agent_id")
        goal = _require_str(arguments, "goal")
        context_ptr = _optional_str(arguments, "context_ptr")
        owned_files = _optional_str_list(arguments, "owned_files")
        verification_status = _optional_str(arguments, "verification_status")
    except ValueError as exc:
        return _bad_request(str(exc))

    op: dict[str, Any] = {"op": "handoff_put", "agent_id": agent_id, "goal": goal}
    if context_ptr is not None:
        op["context_ptr"] = context_ptr
    if owned_files is not None:
        op["owned_files"] = owned_files
    if verification_status is not None:
        op["verification_status"] = verification_status
    resp = deps.broker.request(op)
    return _broker_result(resp)


def handle_send_message(arguments: dict, deps: Deps) -> dict:
    """Send a durable, ack'd message → broker ``send`` (Tier 2 mailbox)."""
    try:
        to = _require_str(arguments, "to")
        sender = _require_str(arguments, "from")
        body = _require_str(arguments, "body")
    except ValueError as exc:
        return _bad_request(str(exc))

    resp = deps.broker.request({"op": "send", "to": to, "from": sender, "body": body})
    return _broker_result(resp)


def handle_status(arguments: dict, deps: Deps) -> dict:
    """Read an agent's latest state → broker ``handoff_get`` (latest handoff)."""
    try:
        agent_id = _require_str(arguments, "agent_id")
        goal = _optional_str(arguments, "goal")
    except ValueError as exc:
        return _bad_request(str(exc))

    op: dict[str, Any] = {"op": "handoff_get", "agent_id": agent_id}
    if goal is not None:
        op["goal"] = goal
    resp = deps.broker.request(op)
    return _broker_result(resp)


def handle_list_agents(arguments: dict, deps: Deps) -> dict:
    """List registered agents → broker ``query`` (role/alive/capability filters)."""
    try:
        role = _optional_str(arguments, "role")
        alive = _optional_bool(arguments, "alive")
        capability = _optional_str(arguments, "capability")
    except ValueError as exc:
        return _bad_request(str(exc))

    op: dict[str, Any] = {"op": "query"}
    if role is not None:
        op["role"] = role
    if alive is not None:
        op["alive"] = alive
    if capability is not None:
        op["capability"] = capability
    resp = deps.broker.request(op)
    return _broker_result(resp)


def _broker_result(resp: Any) -> dict:
    """Normalize a raw broker response into a tool result dict.

    The broker already returns ``{ok: bool, ...}``; we pass it through so the
    server can map ``ok`` onto MCP ``isError``. A non-dict reply (shouldn't
    happen) is reported as a broker error rather than crashing.
    """
    if not isinstance(resp, dict):
        return {"ok": False, "error": "broker_error", "message": "non-dict broker reply"}
    return resp


# --------------------------------------------------------------------------- #
# Tool registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolSpec:
    """A single MCP tool: its name, description, JSON-Schema, and handler."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict, Deps], dict]

    def descriptor(self) -> dict:
        """The ``tools/list`` wire descriptor (name/description/inputSchema)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _obj(properties: dict, required: list[str]) -> dict:
    """Build a JSON-Schema object with the given properties/required keys."""
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_STATUS_ENUM = ["busy", "blocked", "done", "idle"]

# Ordered registry. ``tools/list`` preserves this order.
TOOLS: dict[str, ToolSpec] = {}


def _register(spec: ToolSpec) -> None:
    TOOLS[spec.name] = spec


_register(
    ToolSpec(
        name="spawn",
        description=(
            "Create and launch a new agent in an iTerm2 tab. Builds the Tier 1 "
            "spawn plan (working directory + dot-free user.agent_* identity vars) "
            "and invokes the spawn path; when 'id' is given the agent is also "
            "registered in the broker so status/list_agents see it."
        ),
        input_schema=_obj(
            {
                "command": {
                    "description": "Command to run in the new tab (string or argv list).",
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "id": {"type": "string", "description": "agent_id (stable handle)."},
                "role": {"type": "string", "description": "agent_role."},
                "task": {"type": "string", "description": "agent_task."},
                "status": {"type": "string", "enum": _STATUS_ENUM, "description": "initial agent_status."},
                "cwd": {"type": "string", "description": "working directory (overrides inherited cwd)."},
                "home": {"type": "boolean", "description": "open in $HOME (excludes cwd)."},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "capability tags recorded on registration.",
                },
            },
            required=["command"],
        ),
        handler=handle_spawn,
    )
)

_register(
    ToolSpec(
        name="assign",
        description=(
            "Assign a role/task (and optional capabilities) to an agent by "
            "upserting its broker registry entry. Idempotent per agent_id."
        ),
        input_schema=_obj(
            {
                "agent_id": {"type": "string", "description": "the agent's id / session key."},
                "role": {"type": "string", "description": "agent role."},
                "task": {"type": "string", "description": "task description."},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "capability tags.",
                },
                "alive": {"type": "boolean", "description": "liveness flag (default true)."},
            },
            required=["agent_id"],
        ),
        handler=handle_assign,
    )
)

_register(
    ToolSpec(
        name="handoff",
        description=(
            "Write a durable handoff/state record for an agent (append-only "
            "history in the broker): goal, context pointer, owned files, "
            "verification status."
        ),
        input_schema=_obj(
            {
                "agent_id": {"type": "string", "description": "the handing-off agent's id."},
                "goal": {"type": "string", "description": "the goal this handoff is for."},
                "context_ptr": {"type": "string", "description": "pointer to context (path/url/id)."},
                "owned_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "files this agent owns.",
                },
                "verification_status": {"type": "string", "description": "e.g. passing/failing/unknown."},
            },
            required=["agent_id", "goal"],
        ),
        handler=handle_handoff,
    )
)

_register(
    ToolSpec(
        name="send_message",
        description=(
            "Send a durable, acknowledged message to another agent via the "
            "broker mailbox (survives restarts; re-delivered until acked)."
        ),
        input_schema=_obj(
            {
                "to": {"type": "string", "description": "recipient agent id or role."},
                "from": {"type": "string", "description": "sender agent id."},
                "body": {"type": "string", "description": "message body."},
            },
            required=["to", "from", "body"],
        ),
        handler=handle_send_message,
    )
)

_register(
    ToolSpec(
        name="status",
        description=(
            "Get an agent's latest handoff/state record from the broker "
            "(optionally scoped to a goal). Returns null when the agent has no "
            "handoff yet."
        ),
        input_schema=_obj(
            {
                "agent_id": {"type": "string", "description": "the agent to inspect."},
                "goal": {"type": "string", "description": "scope to a single goal (optional)."},
            },
            required=["agent_id"],
        ),
        handler=handle_status,
    )
)

_register(
    ToolSpec(
        name="list_agents",
        description=(
            "List agents from the broker registry, optionally filtered by role, "
            "liveness, or a capability tag."
        ),
        input_schema=_obj(
            {
                "role": {"type": "string", "description": "filter by role."},
                "alive": {"type": "boolean", "description": "filter by liveness."},
                "capability": {"type": "string", "description": "filter by a capability tag."},
            },
            required=[],
        ),
        handler=handle_list_agents,
    )
)


def tool_descriptors() -> list[dict]:
    """Return the ``tools/list`` payload: every tool's wire descriptor, in order."""
    return [spec.descriptor() for spec in TOOLS.values()]


def call_tool(name: str, arguments: Optional[dict], deps: Deps) -> dict:
    """Dispatch a ``tools/call`` to its handler; return the result dict.

    Unknown tool → a structured error result (``ok`` False), not an exception,
    so the server maps it to an MCP ``isError`` result. A non-dict ``arguments``
    is treated as empty. Handler exceptions (e.g. an unreachable broker) are NOT
    caught here — the server layer catches them and reports a tool error.
    """
    spec = TOOLS.get(name)
    if spec is None:
        return {"ok": False, "error": "unknown_tool", "message": f"no such tool: {name}"}
    args = arguments if isinstance(arguments, dict) else {}
    return spec.handler(args, deps)
