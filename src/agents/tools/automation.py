"""The governed boundary to the ecosystem's conditions and the events they
raise (GM-02).

Declaring a condition, listing and pausing them, evaluating now, reading the
event log, and resolving a proposal. The one consequential operation -
``approve``, which runs a proposed action on a real connection - is only ever
invoked after a person has confirmed it (the Control Center's confirmed,
audited ``events approve``, or the conversation's ordinary yes/no gate, which
reaches the connection through ``connected_action`` instead of this tool).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from src.agents.tools.tool import Tool, ToolSpec
from src.services.automation.conditions import (
    Condition,
    ProposedAction,
    new_condition_id,
    operator_named,
    operators,
    parse_threshold,
)
from src.services.observations.store import utc_now

AUTOMATION_TOOL = "automation"
_OPERATIONS = ("operators", "conditions", "declare", "remove", "pause", "resume", "evaluate", "events",
               "approve", "dismiss", "delivered")


class AutomationTool(Tool):
    def __init__(self, ecosystem: Any, connections: Any, *, owner_agent: str,
                 clock: "Callable[[], Any]" = utc_now) -> None:
        self._ecosystem = ecosystem
        self._connections = connections
        self._owner_agent = owner_agent
        self._clock = clock

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=AUTOMATION_TOOL,
            description=(
                "Declared conditions over observations and the events they raise: declare, list, pause, "
                "evaluate now, read events, and resolve a proposed action after the person confirmed it."
            ),
            schema={"operation": str},
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        try:
            content = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise ValueError(f"automation content must be JSON: {exc}") from exc
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown automation operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        return json.dumps(getattr(self, f"_{operation}")(content))

    def _operators(self, content: dict) -> dict:
        return {"operators": [{"name": o.name, "kind": o.kind, "words": list(o.words)} for o in operators()]}

    def _conditions(self, content: dict) -> dict:
        state = self._ecosystem.conditions.state()
        return {"conditions": [
            {**c.to_dict(), "text": c.describe(), "state": state.get(c.condition_id, {})}
            for c in self._ecosystem.conditions.all()
        ]}

    def _declare(self, content: dict) -> dict:
        operator = operator_named(content.get("operator", ""))
        if operator is None:
            raise ValueError(f"'{content.get('operator')}' is not a known test")
        threshold = content.get("threshold")
        if isinstance(threshold, str) or threshold is None:
            parsed = parse_threshold(operator, threshold if threshold is not None else "")
            threshold = parsed if parsed is not None or operator.kind in ("none",) else threshold
        action = content.get("action")
        if action:
            connection = self._connections.get(action["connection"]) if self._connections is not None else None
            if connection is None or connection.name != action["connection"]:
                raise ValueError(f"'{action['connection']}' is not a connection this system has")
            if action["operation"] not in connection.supported_operations:
                raise ValueError(f"'{action['connection']}' has no operation named '{action['operation']}'")
        registry = self._ecosystem.conditions
        condition_id = content.get("condition_id") or new_condition_id(
            content["entity"], content["attribute"], operator.name, registry.all())
        condition = Condition(
            condition_id=condition_id, entity=str(content["entity"]), attribute=str(content["attribute"]),
            operator=operator.name, threshold=threshold, window_seconds=int(content.get("window_seconds") or 0),
            aggregate=str(content.get("aggregate") or "latest"),
            response="propose" if action else "notify",
            action=ProposedAction(action["connection"], action["operation"], dict(action.get("params") or {})) if action else None,
            cooldown_seconds=int(content.get("cooldown_seconds") or 0), label=str(content.get("label") or ""),
            declared_by=str(content.get("declared_by") or self._owner_agent), declared_at=self._clock().isoformat(),
            quantifier=str(content.get("quantifier") or "any"),
        )
        registry.declare(condition)
        fired = self._ecosystem.engine.evaluate(condition, now=self._clock())
        return {"condition": condition.to_dict(), "text": condition.describe(),
                "fired": fired.to_dict() if fired else None}

    def _remove(self, content: dict) -> dict:
        return {"removed": self._ecosystem.conditions.remove(str(content.get("condition_id") or ""))}

    def _pause(self, content: dict) -> dict:
        updated = self._ecosystem.conditions.set_enabled(str(content.get("condition_id") or ""), False)
        return {"condition": updated.to_dict() if updated else None}

    def _resume(self, content: dict) -> dict:
        updated = self._ecosystem.conditions.set_enabled(str(content.get("condition_id") or ""), True)
        return {"condition": updated.to_dict() if updated else None}

    def _evaluate(self, content: dict) -> dict:
        wanted = str(content.get("condition_id") or "")
        now = self._clock()
        if wanted:
            condition = self._ecosystem.conditions.get(wanted)
            if condition is None:
                raise ValueError(f"no condition called '{wanted}'")
            fired = [e for e in [self._ecosystem.engine.evaluate(condition, now=now)] if e]
        else:
            fired = self._ecosystem.engine.evaluate_all(now)
        return {"fired": [e.to_dict() for e in fired]}

    def _events(self, content: dict) -> dict:
        events = self._ecosystem.events.all()
        if content.get("pending_only"):
            events = [e for e in events if e.status in ("new", "proposed")]
        limit = int(content.get("limit") or 20)
        return {"events": [{**e.to_dict(), "text": e.describe()} for e in events[-limit:]]}

    def _resolve(self, event_id: str, status: str, resolution: str) -> dict:
        updated = self._ecosystem.events.set_status(event_id, status, resolution, now=self._clock())
        if updated is None:
            raise ValueError(f"no event called '{event_id}'")
        return {"event": updated.to_dict()}

    def _dismiss(self, content: dict) -> dict:
        return self._resolve(str(content.get("event_id") or ""), "dismissed", str(content.get("reason") or "dismissed"))

    def _delivered(self, content: dict) -> dict:
        event = self._ecosystem.events.get(str(content.get("event_id") or ""))
        if event is None:
            raise ValueError("no such event")
        if event.status == "new":
            return self._resolve(event.event_id, "delivered", str(content.get("where") or "delivered"))
        return {"event": event.to_dict()}

    def _approve(self, content: dict) -> dict:
        """Run a proposed action - only after the person confirmed it."""
        event = self._ecosystem.events.get(str(content.get("event_id") or ""))
        if event is None:
            raise ValueError(f"no event called '{content.get('event_id')}'")
        if not event.action:
            raise ValueError("that event proposed no action")
        if event.status not in ("proposed", "delivered"):
            raise ValueError(f"that proposal is already {event.status}")
        connection = self._connections.get(event.action["connection"]) if self._connections is not None else None
        if connection is None or connection.name != event.action["connection"]:
            self._resolve(event.event_id, "failed", f"'{event.action['connection']}' is not connected now")
            raise ValueError(f"'{event.action['connection']}' is not a connection this system has now")
        result = connection.execute(event.action["operation"], **dict(event.action.get("params") or {}))
        if not result.ok:
            self._resolve(event.event_id, "failed", result.detail or result.status.value)
            return {"executed": False, "detail": result.detail or result.status.value}
        self._resolve(event.event_id, "executed", (result.detail or "done")[:300])
        return {"executed": True, "detail": result.detail}
