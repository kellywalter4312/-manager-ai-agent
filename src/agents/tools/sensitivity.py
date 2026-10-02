"""The governed boundary to the owner's sensitivity rules (GM-13): list them,
declare one (a word, a source or a memory kind -> a level), remove one, set a
level's retention. Raising what a cloud reasoning provider may receive is not
here - that is an owner decision taken in the Control Center only."""

from __future__ import annotations

import json
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec

SENSITIVITY_TOOL = "sensitivity"
_OPERATIONS = ("rules", "declare", "remove", "retention", "classify")


class SensitivityTool(Tool):
    def __init__(self, ecosystem: Any, *, owner_agent: str) -> None:
        self._policy = ecosystem.sensitivity
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=SENSITIVITY_TOOL,
            description=("The owner's sensitivity rules - which words, sources and kinds of record are personal, "
                         "sensitive or restricted - and how long each level is kept."),
            schema={"operation": str},
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        content = json.loads(raw) if raw else {}
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown sensitivity operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        policy = self._policy
        if operation == "rules":
            return json.dumps({
                "rules": [{**r.to_dict(), "text": r.describe()} for r in policy.rules()],
                "retention": policy.retention(), "cloud_ceiling": policy.cloud_ceiling(),
            })
        if operation == "declare":
            rule = policy.declare(str(content.get("kind") or "word"), str(content.get("match") or ""),
                                  str(content.get("level") or "sensitive"),
                                  declared_by=str(content.get("declared_by") or self._owner_agent))
            return json.dumps({"rule": rule.to_dict(), "text": rule.describe()})
        if operation == "remove":
            return json.dumps({"removed": policy.remove(str(content.get("match") or content.get("rule_id") or ""))})
        if operation == "retention":
            days = content.get("days")
            policy.set_retention(str(content.get("level") or ""), int(days) if days not in (None, "", "off") else None)
            return json.dumps({"retention": policy.retention()})
        return json.dumps({"level": policy.classify(str(content.get("text") or ""),
                                                    source=str(content.get("source") or ""),
                                                    kind=str(content.get("kind") or ""))})
