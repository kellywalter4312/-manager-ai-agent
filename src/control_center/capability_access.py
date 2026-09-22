"""Which agent may use which capability - authorisation, never ownership.

A capability is registered once (``src/manager/capabilities.py``) and states
its own authority there: ``grantable`` (the default), ``every_agent`` or
``manager`` (with the reason it is Manager-only). This module answers the
per-agent question from that registration and the owner's governed grants:

    the Manager          every registered capability - system-level authority
    any other agent      every ``every_agent`` capability, plus each
                         ``grantable`` one the owner granted it
                         (``capability_grant.<agent>`` in the governed config)

Nothing here names a capability, an agent or a workspace. A capability
registered tomorrow is grantable the moment it exists; a specialist created
tomorrow is granted by the same key; granting one agent something never
removes it from the Manager or from anyone else, because a grant is a fact
about the grantee only. Workspaces play no part: a workspace organises where
work happens and cannot give or take away what an agent may do.

Pure - no I/O, no import of the orchestrator - so the Control Center, the
composition root and tests read the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

CAPABILITY_GRANT_PREFIX = "capability_grant."

GRANTABLE = "grantable"
EVERY_AGENT = "every_agent"
MANAGER_ONLY = "manager"


def agent_key(agent_name: str) -> str:
    return "_".join(str(agent_name or "").strip().lower().split())


def grant_key(agent_name: str) -> str:
    return CAPABILITY_GRANT_PREFIX + agent_key(agent_name)


def granted_names(agent_name: str, values: "Mapping[str, str] | None") -> "tuple[str, ...]":
    """What the owner granted this agent, as saved - in the order granted."""
    raw = (values or {}).get(grant_key(agent_name)) or ""
    return tuple(dict.fromkeys(n.strip() for n in str(raw).split(",") if n.strip()))


def with_grants(agent_name: str, names: "Iterable[str]") -> "tuple[str, str]":
    """The governed-config ``(key, value)`` recording exactly these grants."""
    return grant_key(agent_name), ",".join(dict.fromkeys(n for n in names if n))


def authority_of(capability: Any) -> str:
    return str(getattr(capability, "authority", GRANTABLE) or GRANTABLE)


def reachable_capabilities(
    agent_name: str,
    values: "Mapping[str, str] | None",
    registry: "Mapping[str, Any]",
    *,
    is_manager: bool,
) -> "dict[str, Any]":
    """Every capability in ``registry`` this agent may use right now.

    ``registry`` is the live one - a capability the owner disabled for the
    whole system is already absent from it, for every agent alike."""
    if is_manager:
        return dict(registry)
    granted = set(granted_names(agent_name, values))
    return {
        name: capability for name, capability in registry.items()
        if authority_of(capability) == EVERY_AGENT
        or (authority_of(capability) == GRANTABLE and name in granted)
    }


@dataclass(frozen=True)
class AccessRow:
    name: str
    authority: str
    state: str          # "has it", "granted", "grantable", "manager only", "disabled"
    detail: str = ""


def access_rows(
    agent_name: str,
    values: "Mapping[str, str] | None",
    registered: "Mapping[str, Any]",
    live: "Mapping[str, Any]",
    *,
    is_manager: bool,
) -> "tuple[AccessRow, ...]":
    """One row per registered capability: what this agent's access to it is,
    and why. ``registered`` is every capability; ``live`` the enabled ones."""
    granted = set(granted_names(agent_name, values))
    rows = []
    for name in sorted(registered):
        capability = registered[name]
        authority = authority_of(capability)
        if name not in live:
            rows.append(AccessRow(name, authority, "disabled", "disabled for every agent (capabilities enable)"))
        elif is_manager:
            rows.append(AccessRow(name, authority, "has it", "system-level: the Manager holds every capability"))
        elif authority == EVERY_AGENT:
            route = str(getattr(capability, "own_path", "") or "")
            rows.append(AccessRow(name, authority, "has it", f"every agent - through {route}" if route else "every agent"))
        elif authority == MANAGER_ONLY:
            rows.append(AccessRow(name, authority, "manager only", str(getattr(capability, "authority_reason", ""))))
        elif name in granted:
            rows.append(AccessRow(name, authority, "granted", "granted by the owner"))
        else:
            rows.append(AccessRow(name, authority, "grantable", "not granted - capabilities grant <agent> " + name))
    return tuple(rows)


def grant_refusal(name: str, registered: "Mapping[str, Any]") -> "str | None":
    """Why ``name`` cannot be granted to a specialist, or None when it can."""
    capability = registered.get(name)
    if capability is None:
        return f"no capability called '{name}'"
    authority = authority_of(capability)
    if authority == MANAGER_ONLY:
        return f"'{name}' is Manager-only: {getattr(capability, 'authority_reason', '')}"
    if authority == EVERY_AGENT:
        return f"'{name}' needs no grant - every agent already has it"
    return None
