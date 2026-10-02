"""The governed boundary to groups across connections (GM-15): list them,
declare or reshape one, resolve what it holds, and read the state of all its
members at once.

Reading is all this tool does to the world. Acting on a group is never done
here: the capability that plans one operation per member raises a single
confirmation listing every step, and only a "yes" runs each through the
member's own connection (``src/manager/guided_interaction.py``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec
from src.services.connections.groups import Group, Member
from src.services.observations.store import is_number

GROUPS_TOOL = "groups"
_OPERATIONS = ("groups", "declare", "add", "remove_members", "remove", "resolve", "state")


class GroupTool(Tool):
    def __init__(self, ecosystem: Any, connections: Any, *, owner_agent: str, max_sensitivity: str = "personal") -> None:
        self._ecosystem = ecosystem
        self._connections = connections
        self._owner_agent = owner_agent
        self._ceiling = max_sensitivity

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=GROUPS_TOOL,
            description=("Groups of things that belong together across different connections - a room, a line, "
                         "a field - their members, and the state of all of them at once."),
            schema={"operation": str},
            optional={"content": str},
        )

    # -- helpers -----------------------------------------------------------

    def _connection_names(self) -> "list[str]":
        return list(getattr(self._connections, "names", ()) or ()) if self._connections is not None else []

    def _exists(self, member: Member) -> bool:
        if member.kind == "connection":
            return member.name in self._connection_names()
        if member.kind == "entity":
            return self._ecosystem.observations.resolve_entity(member.name) is not None
        return self._ecosystem.groups.get(member.name) is not None

    def _members(self, content: dict) -> "list[Member]":
        found = []
        for raw in content.get("members") or ():
            if isinstance(raw, dict):
                found.append(Member(str(raw.get("kind") or "connection"), str(raw.get("name") or "")))
            else:
                found.append(self._classify(str(raw)))
        return found

    def _classify(self, name: str) -> Member:
        """A bare member name, placed by what it names: a connection this
        system has, a group, or else an observed entity."""
        name = " ".join(name.split())
        if name in self._connection_names():
            return Member("connection", name)
        if self._ecosystem.groups.get(name) is not None:
            return Member("group", self._ecosystem.groups.get(name).name)
        resolved = self._ecosystem.observations.resolve_entity(name)
        return Member("entity", resolved or name)

    def _described(self, group: Group) -> dict:
        try:
            resolved = self._ecosystem.groups.resolve(group.name)
        except ValueError as exc:
            resolved, problem = [], str(exc)
        else:
            problem = ""
        return {
            **group.to_dict(), "text": group.describe(),
            "resolved": [m.to_dict() for m in resolved],
            "missing": [m.to_dict() for m in group.members if not self._exists(m)],
            "entities": self._ecosystem.group_entities(group.name), "problem": problem,
        }

    # -- operations --------------------------------------------------------

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        content = json.loads(raw) if raw else {}
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown groups operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        groups = self._ecosystem.groups
        if operation == "groups":
            return json.dumps({"groups": [self._described(g) for g in groups.all()]})
        name = " ".join(str(content.get("name") or "").split())
        if not name:
            raise ValueError("name the group")
        if operation == "declare":
            existing = groups.get(name)
            group = groups.declare(Group(
                name=name, members=tuple(self._members(content)), description=str(content.get("description") or ""),
                declared_by=str(content.get("declared_by") or self._owner_agent),
                declared_at=datetime.now(timezone.utc).isoformat(),
            ))
            return json.dumps({"group": self._described(group), "replaced": existing is not None})
        if operation == "add":
            group = groups.add_members(name, self._members(content),
                                       declared_by=str(content.get("declared_by") or self._owner_agent))
            return json.dumps({"group": self._described(group)})
        if operation == "remove_members":
            group = groups.remove_members(name, [str(n) for n in content.get("names") or ()])
            return json.dumps({"group": self._described(group) if group else None})
        if operation == "remove":
            return json.dumps({"removed": groups.remove(name)})
        group = groups.get(name)
        if group is None:
            raise ValueError(f"there is no group called '{name}'")
        if operation == "resolve":
            return json.dumps({"group": self._described(group)})
        return json.dumps(self._state(group, content))

    def _state(self, group: Group, content: dict) -> dict:
        """Every member's latest observations, and - for each reading more
        than one member reports as a number - the group's mean, lowest and
        highest. A window ("window_seconds") summarises each member over it."""
        store = self._ecosystem.observations
        window = float(content.get("window_seconds") or 0)
        members: list[dict] = []
        numbers: "dict[str, list[tuple[str, float]]]" = {}
        for entity in self._ecosystem.group_entities(group.name):
            resolved = store.resolve_entity(entity)
            row: dict = {"entity": entity, "observed": resolved is not None, "latest": {}}
            if resolved is not None:
                for attribute, observation in store.latest(resolved, max_sensitivity=self._ceiling).items():
                    value = observation.value
                    if window and is_number(value):
                        summary = store.summarize(resolved, attribute, window=timedelta(seconds=window),
                                                  max_sensitivity=self._ceiling)
                        row.setdefault("summary", {})[attribute] = summary.to_dict()
                        value = summary.mean if summary.mean is not None else value
                    row["latest"][attribute] = {"value": observation.value, "unit": observation.unit,
                                                "at": observation.observed_at.isoformat()}
                    if is_number(value):
                        numbers.setdefault(attribute, []).append((entity, float(value)))
            members.append(row)
        aggregates = {}
        for attribute, values in numbers.items():
            if len(values) < 2:
                continue
            only = [v for _e, v in values]
            aggregates[attribute] = {
                "members": len(values), "mean": round(sum(only) / len(only), 4),
                "min": min(only), "max": max(only),
                "lowest": min(values, key=lambda x: x[1])[0], "highest": max(values, key=lambda x: x[1])[0],
            }
        connections = [m.name for m in self._ecosystem.groups.resolve(group.name) if m.kind == "connection"]
        return {"group": self._described(group), "members": members, "aggregates": aggregates,
                "connections": connections, "window_seconds": window}
