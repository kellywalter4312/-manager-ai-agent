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

A workspace is created, named, renamed, described, given a capability profile,
archived and restored from the Control Center - the built-in rows included,
which are seeds rather than a fixed list. Its **capability profile** names the
work done there (Manager capability names or specialist capability words); an
empty profile allows everything. Moving between workspaces is the agent's
active workspace (``workspace_active.<agent>``), changed by the owner, the page
or the agent itself in conversation - one saved fact every surface reads.

A Workspace is never an Agent: it holds no Brain, memory or identity. Agents
work *in* workspaces; the same workspace can be given to any registered agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping

# ``produced`` - the artifacts made while an agent was working in this
# workspace (attributed when each was created).
# ``model3d`` - the agent's genuine 3D models (glTF), shown turning in 3D.
PANEL_KINDS = ("tasks", "diagram3d", "artifacts", "produced", "model3d")
# The artifact metadata key naming the workspace an artifact was made in.
ARTIFACT_WORKSPACE_KEY = "workspace"
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
    capabilities: "tuple[str, ...]" = ()
    archived: bool = False

    def __post_init__(self) -> None:
        ident = "_".join(str(self.workspace_id).strip().lower().split())
        if not ident or not ident.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"a workspace type needs a plain id, got {self.workspace_id!r}")
        panels = tuple(p.strip().lower() for p in self.panels if str(p).strip())
        if not panels or not all(_valid_panel(p) for p in panels):
            raise ValueError(f"workspace type {ident!r} has panels the page cannot render: {panels}")
        object.__setattr__(self, "workspace_id", ident)
        object.__setattr__(self, "panels", panels)
        object.__setattr__(self, "label", " ".join(str(self.label or ident).split()) or ident)
        object.__setattr__(self, "capabilities", tuple(dict.fromkeys(
            "_".join(str(c).strip().lower().split()) for c in self.capabilities if str(c).strip()
        )))

    def permits(self, names: "Iterable[str]") -> bool:
        """Whether work named by any of ``names`` belongs in this workspace -
        always, when the profile is empty."""
        if not self.capabilities:
            return True
        return any("_".join(str(n).strip().lower().split()) in self.capabilities for n in names)

    def to_dict(self) -> dict:
        return {"id": self.workspace_id, "label": self.label, "description": self.description, "panels": list(self.panels),
                "capabilities": list(self.capabilities), "archived": self.archived}

    def to_value(self) -> str:
        """The governed-config value this definition is saved as."""
        return json.dumps({"label": self.label, "description": self.description, "panels": list(self.panels),
                           "capabilities": list(self.capabilities), "archived": self.archived}, sort_keys=True)


DEFAULT_WORKSPACE_TYPES: "tuple[WorkspaceType, ...]" = (
    WorkspaceType("board", "Task board", "What this agent is working on, done and to do.", ("tasks",)),
    WorkspaceType("diagram", "3D diagram", "A live 3D diagram of this agent's work, its 3D models, diagrams and images.",
                  ("diagram3d", "model3d", "artifacts:image")),
    WorkspaceType("coding", "Coding", "Code this agent has written, and the tasks around it.", ("artifacts:code", "tasks")),
    WorkspaceType("documents", "Documents", "Documents this agent produced or was given.",
                  ("artifacts:document", "artifacts:supplied_file")),
)


def agent_key(agent_name: str) -> str:
    return "_".join(str(agent_name or "").strip().lower().split())


def _parse_definition(ident: str, value: str, seed: "WorkspaceType | None") -> WorkspaceType:
    """One saved definition: the JSON form, or the original ``Label|panel,panel``
    form. A saved row for a built-in id changes only the fields it names."""
    text = str(value).strip()
    if text.startswith("{"):
        data = json.loads(text)
        base = seed or WorkspaceType(ident, ident, "Added by the owner.", tuple(data.get("panels") or ("tasks",)))
        return WorkspaceType(
            ident,
            data.get("label") or base.label,
            data["description"] if "description" in data else base.description,
            tuple(data.get("panels") or base.panels),
            tuple(data["capabilities"]) if "capabilities" in data else base.capabilities,
            bool(data.get("archived", base.archived)),
        )
    label, _sep, panels = text.partition("|")
    return WorkspaceType(ident, label.strip() or ident, "Added by the owner.", tuple(panels.split(",")))


def all_workspaces(values: "Mapping[str, str] | None" = None) -> "tuple[WorkspaceType, ...]":
    """Every workspace definition, archived ones included - the seeds with the
    owner's changes applied, then every workspace the owner created. An
    unrenderable definition is skipped, never shown half-working."""
    types = {t.workspace_id: t for t in DEFAULT_WORKSPACE_TYPES}
    for key, value in (values or {}).items():
        if not str(key).startswith(WORKSPACE_TYPE_PREFIX):
            continue
        ident = "_".join(str(key)[len(WORKSPACE_TYPE_PREFIX):].strip().lower().split())
        try:
            defined = _parse_definition(ident, value, types.get(ident))
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            continue
        types[defined.workspace_id] = defined
    return tuple(types.values())


def workspace_catalog(values: "Mapping[str, str] | None" = None) -> "tuple[WorkspaceType, ...]":
    """The workspaces agents can be in: every definition not archived."""
    return tuple(t for t in all_workspaces(values) if not t.archived)


def find_workspace(
    values: "Mapping[str, str] | None", name: str, *, within: "Iterable[WorkspaceType] | None" = None,
) -> "WorkspaceType | None":
    """A workspace by how a person names it - its id, its label, or a word of
    its label ("research" for "Research lab"). None when nothing, or more than
    one, matches."""
    candidates = tuple(within if within is not None else all_workspaces(values))
    wanted = " ".join(str(name or "").strip().lower().replace("_", " ").split())
    if not wanted:
        return None
    for match in (
        lambda t: t.workspace_id.replace("_", " ") == wanted or t.label.lower() == wanted,
        lambda t: wanted in t.label.lower().split() or wanted in t.workspace_id.split("_"),
        lambda t: wanted in t.label.lower(),
    ):
        found = [t for t in candidates if match(t)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


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


def active_workspace_type_for(agent_name: str, values: "Mapping[str, str] | None" = None) -> "WorkspaceType | None":
    """The workspace this agent is working in right now, as a definition."""
    active = active_workspace_for(agent_name, values)
    return next((t for t in workspaces_for(agent_name, values) if t.workspace_id == active), None)


def workspaces_permitting(
    agent_name: str, values: "Mapping[str, str] | None", names: "Iterable[str]",
) -> "tuple[WorkspaceType, ...]":
    """This agent's workspaces where work named by ``names`` belongs."""
    names = tuple(names)
    return tuple(t for t in workspaces_for(agent_name, values) if t.permits(names))


def workspace_refusal(agent_name: str, values: "Mapping[str, str] | None", names: "Iterable[str]") -> "str | None":
    """Why this work does not belong in the workspace the agent is in - naming
    the workspace, what it is set up for, and where the work does belong - or
    None when it belongs."""
    names = tuple(names)
    here = active_workspace_type_for(agent_name, values)
    if here is None or here.permits(names):
        return None
    elsewhere = [t.label for t in workspaces_permitting(agent_name, values, names)]
    move = (f" It belongs in: {', '.join(elsewhere)} - say 'go to the {elsewhere[0]} workspace'." if elsewhere
            else " None of my workspaces includes it; the owner can add it in the Control Center "
                 f"(workspace profile {here.workspace_id} ...).")
    return (
        f"That isn't work for the {here.label} workspace I'm in - it's set up for "
        f"{', '.join(here.capabilities)}.{move}"
    )


def workspace_artifact_metadata(agent_name: str, values: "Mapping[str, str] | None") -> "dict[str, str]":
    """What an artifact this agent makes right now records about where it was
    made."""
    active = active_workspace_for(agent_name, values)
    return {ARTIFACT_WORKSPACE_KEY: active} if active else {}
