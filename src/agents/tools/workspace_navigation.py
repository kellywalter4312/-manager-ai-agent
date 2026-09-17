"""The governed boundary between an agent and the workspaces it works in.

Environment access only: it reads the owner's workspace definitions and each
agent's saved active workspace (``src/control_center/workspace_catalog.py``)
through the governed config, and writes one fact - which workspace an agent is
in now. Creating, renaming, profiling and archiving workspaces stays with the
owner in the Control Center; an agent can only move among the workspaces it has
been given. No reasoning lives here.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping

from src.agents.tools.tool import Tool, ToolSpec
from src.control_center.workspace_catalog import (
    AGENT_ACTIVE_WORKSPACE_PREFIX,
    active_workspace_for,
    agent_key,
    find_workspace,
    workspaces_for,
)


class WorkspaceNavigationTool(Tool):
    def __init__(
        self, *, load_values: "Callable[[], Mapping[str, str]]", save_value: "Callable[[str, str], Any]",
        agent_names: "Callable[[], Iterable[str]]",
    ) -> None:
        self._load_values = load_values
        self._save_value = save_value
        self._agent_names = agent_names

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="workspace",
            description=(
                "Lists the workspaces an agent has and which it is in, or moves the agent into one of its "
                "workspaces (saved - the Control Center, the page and a restart all read the same choice)."
            ),
            schema={"operation": str, "content": str},
        )

    def _agent(self, name: str) -> str:
        known = {str(n).lower(): str(n) for n in self._agent_names()}
        agent = known.get(str(name or "").strip().lower())
        if agent is None:
            raise ValueError(f"no agent called '{name}'. Registered: {', '.join(sorted(known.values()))}")
        return agent

    def _state(self, agent: str, values: "Mapping[str, str]") -> dict:
        active = active_workspace_for(agent, values)
        return {
            "agent": agent,
            "active": active,
            "workspaces": [t.to_dict() for t in workspaces_for(agent, values)],
        }

    def run(self, **arguments: str) -> str:
        operation = arguments["operation"].strip().lower()
        request = json.loads(arguments["content"] or "{}")
        agent = self._agent(request.get("agent", ""))
        values = dict(self._load_values())
        if operation == "list":
            return json.dumps(self._state(agent, values))
        if operation == "enter":
            name = str(request.get("workspace", "")).strip()
            mine = workspaces_for(agent, values)
            target = find_workspace(values, name, within=mine)
            if target is None:
                anywhere = find_workspace(values, name)
                if anywhere is not None and anywhere.archived:
                    raise ValueError(f"the {anywhere.label} workspace is archived - the owner can restore it in the Control Center")
                if anywhere is not None:
                    raise ValueError(
                        f"the {anywhere.label} workspace isn't one of {agent}'s - the owner can add it in the Control Center "
                        f"(workspace {agent} add {anywhere.workspace_id})"
                    )
                labels = ", ".join(t.label for t in mine) or "none"
                raise ValueError(f"there's no workspace called '{name}'. {agent}'s workspaces: {labels}")
            self._save_value(AGENT_ACTIVE_WORKSPACE_PREFIX + agent_key(agent), target.workspace_id)
            saved = dict(self._load_values())
            if active_workspace_for(agent, saved) != target.workspace_id:
                raise ValueError(f"the move to {target.label} was written but did not read back")
            return json.dumps({**self._state(agent, saved), "entered": target.to_dict()})
        raise ValueError(f"unknown workspace operation {operation!r}; expected list or enter")
