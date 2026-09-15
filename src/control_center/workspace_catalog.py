"""Workspace types - what kinds of iterative work an agent's Workspace offers.

A type is data: an id, a label, a description and the panels it shows. Panels
are a small vocabulary the page renders generically - ``tasks`` (the governed
task board), ``diagram3d`` (a real 3D diagram of the agent's work),
``artifacts:<type>`` (the agent's own artifacts of one type, or ``any``). A new
type is therefore a new row, never a new page, and never tied to one agent.

**Every agent gets every type by default** - the Manager and every present or
future specialist alike - and the owner narrows or widens that per agent
through the Control Center (``workspaces <agent> add|remove|use <type>``),
saved in the governed config. Adding a coding specialist never takes coding
away from anyone else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

PANEL_KINDS = ("tasks", "diagram3d", "artifacts")
WORKSPACE_TYPE_PREFIX = "workspace_type."
AGENT_WORKSPACES_PREFIX = "workspaces."
AGENT_ACTIVE_WORKSPACE_PREFIX = "workspace_active."


def _valid_panel(panel: str) -> bool:
    kind, _sep, arg = panel.partition(":")
    if kind not in PANEL_KINDS:
        return False
    return kind != "artifacts" or bool(arg.strip())


@dataclass(frozen=True)
class WorkspaceType:
    workspace_id: str
    label: str
    description: str
    panels: "tuple[str, ...]"

    def __post_init__(self) -> None:
        ident = "_".join(str(self.workspace_id).strip().lower().split())
        if not ident or not ident.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"a workspace type needs a plain id, got {self.workspace_id!r}")
        panels = tuple(p.strip().lower() for p in self.panels if str(p).strip())
        if not panels or not all(_valid_panel(p) for p in panels):
            raise ValueError(f"workspace type {ident!r} has panels the page cannot render: {panels}")
        object.__setattr__(self, "workspace_id", ident)
        object.__setattr__(self, "panels", panels)

    def to_dict(self) -> dict:
        return {"id": self.workspace_id, "label": self.label, "description": self.description, "panels": list(self.panels)}


DEFAULT_WORKSPACE_TYPES: "tuple[WorkspaceType, ...]" = (
    WorkspaceType("board", "Task board", "What this agent is working on, done and to do.", ("tasks",)),
    WorkspaceType("diagram", "3D diagram", "A live 3D diagram of this agent's work, with its diagrams and images.",
                  ("diagram3d", "artifacts:image")),
    WorkspaceType("coding", "Coding", "Code this agent has written, and the tasks around it.", ("artifacts:code", "tasks")),
    WorkspaceType("documents", "Documents", "Documents this agent produced or was given.",
                  ("artifacts:document", "artifacts:supplied_file")),
)


def agent_key(agent_name: str) -> str:
    return "_".join(str(agent_name or "").strip().lower().split())


def workspace_catalog(values: "Mapping[str, str] | None" = None) -> "tuple[WorkspaceType, ...]":
    """The default types plus any the owner added through the governed config
    (``workspace_type.<id>`` = ``Label|panel,panel``); an unrenderable addition is
    skipped, never shown half-working."""
    types = {t.workspace_id: t for t in DEFAULT_WORKSPACE_TYPES}
    for key, value in (values or {}).items():
        if not str(key).startswith(WORKSPACE_TYPE_PREFIX):
            continue
        label, _sep, panels = str(value).partition("|")
        try:
            added = WorkspaceType(str(key)[len(WORKSPACE_TYPE_PREFIX):], label.strip() or key, "Added by the owner.",
                                  tuple(panels.split(",")))
        except ValueError:
            continue
        types[added.workspace_id] = added
    return tuple(types.values())


def workspaces_for(agent_name: str, values: "Mapping[str, str] | None" = None) -> "tuple[WorkspaceType, ...]":
    """This agent's workspace types: every catalog type unless the owner saved a
    specific list for this agent."""
    catalog = {t.workspace_id: t for t in workspace_catalog(values)}
    chosen = (values or {}).get(AGENT_WORKSPACES_PREFIX + agent_key(agent_name))
    if chosen is None:
        return tuple(catalog.values())
    return tuple(catalog[i] for i in (x.strip() for x in str(chosen).split(",")) if i in catalog)


def active_workspace_for(agent_name: str, values: "Mapping[str, str] | None" = None) -> "str | None":
    available = [t.workspace_id for t in workspaces_for(agent_name, values)]
    if not available:
        return None
    chosen = (values or {}).get(AGENT_ACTIVE_WORKSPACE_PREFIX + agent_key(agent_name))
    return chosen if chosen in available else available[0]
