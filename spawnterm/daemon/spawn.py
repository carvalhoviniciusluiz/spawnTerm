#!/usr/bin/env python3
"""Spawn-plan computation for the spawnTerm daemon (Tier 1.2, #27).

This module is **pure**: it has no ``iterm2`` import and performs no I/O, so it
imports and unit-tests without a running iTerm2 (matching ``registry`` /
``envelope``). Given the spawner's context and a few options it computes a
:class:`SpawnPlan` — the resolved working directory the new agent tab should
open in, plus the ordered list of ``user.agent_*`` user-variable assignments to
stamp its identity. The thin iTerm2 adapter (``adapter.DaemonAdapter``) executes
that plan; the CLI in ``spawnterm_daemon`` wires it up.

Two concerns live here, both testable offline:

  1. **cwd inheritance** (the #3 quick win). Precedence, mirroring the reference
     shell wrapper ``spawnterm/spawn/spawnterm-spawn`` (#10):
         --home            -> the user's home directory
         --dir <path>      -> that specific directory
         (default)         -> inherit the spawner's cwd
     ``--home`` and ``--dir`` are mutually exclusive (raises
     :class:`SpawnPlanError`).

  2. **identity vars**. The ordered dot-free assignments. iTerm2 forbids ``.``
     in the *key* portion of a user variable and prefixes ``user.`` itself, so
     the API-visible names are ``user.agent_id`` / ``user.agent_role`` /
     ``user.agent_task`` / ``user.agent_status`` — the suffix after ``user.`` is
     dot-free (see #23 / registry.AGENT_VAR_KEYS). Empty values are skipped.

Gating: identity tagging gates on ``spawnterm.status_board`` (the same flag the
emitter self-gates on). This module stays **pure** — it does not read the flag
itself; the caller passes the already-resolved ``tag_identity`` boolean. When it
is False the plan carries an EMPTY variable list (the tab still spawns, it is
just not tagged), exactly like ``spawnterm-emit`` no-ops when the flag is OFF.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Ordered identity fields. The order is the assignment order in the plan:
# id first (stable handle), then role/task (human context), then status (the
# lifecycle state the board colors on). Kept dot-free per #23.
IDENTITY_FIELDS = ("id", "role", "task", "status")

# iTerm2's own prefix for user-set variables. The suffix after this must be
# dot-free; we build it from ``agent_<field>`` which never contains a dot.
USER_VAR_PREFIX = "user."

# Lifecycle statuses the board understands (matches spawnterm-emit / the shell
# wrapper). Kept for validation symmetry; an empty status is allowed (skipped).
KNOWN_STATUSES = ("busy", "blocked", "done", "idle")


class SpawnPlanError(ValueError):
    """Raised for an invalid spawn request (e.g. --home and --dir together)."""


def agent_var_name(field_name: str) -> str:
    """Return the API-visible, dot-free user-var name for an identity field.

    ``"id"`` -> ``"user.agent_id"``. The portion after ``user.`` is guaranteed
    dot-free, which iTerm2 requires for a SetUserVar key.
    """
    suffix = f"agent_{field_name}"
    assert "." not in suffix, "agent user-var suffix must be dot-free (#23)"
    return USER_VAR_PREFIX + suffix


@dataclass(frozen=True)
class SpawnPlan:
    """A resolved, ready-to-execute spawn request.

    ``cwd`` is the directory the new tab opens in. ``variables`` is the ordered
    list of ``(name, value)`` user-variable assignments to apply to the new
    session, where each ``name`` is a dot-free ``user.agent_*`` key. ``tagged``
    records whether identity tagging was enabled (gate ON); when False,
    ``variables`` is empty by construction.
    """

    cwd: str
    variables: list[tuple[str, str]] = field(default_factory=list)
    tagged: bool = False


def resolve_cwd(
    spawner_cwd: str,
    *,
    dir_override: str | None = None,
    use_home: bool = False,
    home: str | None = None,
) -> str:
    """Resolve the working directory for a spawned tab.

    Precedence: ``--home`` > ``--dir`` > inherit ``spawner_cwd`` (default).
    ``use_home`` and a non-empty ``dir_override`` are mutually exclusive and
    raise :class:`SpawnPlanError`. When ``use_home`` is set, ``home`` must be
    provided (the caller supplies ``$HOME`` — this module does no I/O).
    """
    has_dir = bool(dir_override)
    if use_home and has_dir:
        raise SpawnPlanError("--home and --dir are mutually exclusive")
    if use_home:
        if not home:
            raise SpawnPlanError("--home requested but no home directory provided")
        return home
    if has_dir:
        return dir_override  # type: ignore[return-value]
    return spawner_cwd


def build_identity_variables(
    *,
    agent_id: str = "",
    role: str = "",
    task: str = "",
    status: str = "",
    tag_identity: bool = True,
) -> list[tuple[str, str]]:
    """Return the ordered dot-free ``user.agent_*`` assignments to apply.

    Order follows :data:`IDENTITY_FIELDS` (id, role, task, status). Empty values
    are skipped so we never stamp a blank var. When ``tag_identity`` is False
    (the ``spawnterm.status_board`` gate is OFF) this returns an EMPTY list — the
    tab spawns untagged, mirroring how ``spawnterm-emit`` no-ops when gated off.
    """
    if not tag_identity:
        return []
    if status and status not in KNOWN_STATUSES:
        raise SpawnPlanError(
            f"unknown status: {status} (one of: {', '.join(KNOWN_STATUSES)})"
        )
    values = {"id": agent_id, "role": role, "task": task, "status": status}
    variables: list[tuple[str, str]] = []
    for field_name in IDENTITY_FIELDS:
        value = values[field_name]
        if value:
            variables.append((agent_var_name(field_name), value))
    return variables


def build_spawn_plan(
    *,
    spawner_cwd: str,
    dir_override: str | None = None,
    use_home: bool = False,
    home: str | None = None,
    agent_id: str = "",
    role: str = "",
    task: str = "",
    status: str = "busy",
    tag_identity: bool = True,
) -> SpawnPlan:
    """Compute the full :class:`SpawnPlan` from a spawn request.

    Combines :func:`resolve_cwd` and :func:`build_identity_variables`. ``status``
    defaults to ``busy`` (a freshly spawned agent is working), matching the shell
    wrapper. ``tag_identity`` is the already-resolved ``spawnterm.status_board``
    gate result (this module never reads the flag). Raises
    :class:`SpawnPlanError` on an invalid request.
    """
    cwd = resolve_cwd(
        spawner_cwd,
        dir_override=dir_override,
        use_home=use_home,
        home=home,
    )
    variables = build_identity_variables(
        agent_id=agent_id,
        role=role,
        task=task,
        status=status,
        tag_identity=tag_identity,
    )
    return SpawnPlan(cwd=cwd, variables=variables, tagged=bool(tag_identity))
