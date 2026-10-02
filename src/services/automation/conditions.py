"""Condition -> event -> response (GM-02).

A **condition** is a declared question over observations: "is <attribute> of
<entity> <operator> <threshold>?", optionally over a window and an aggregate
("the mean over the last 10 minutes"). It is checked the moment a new
observation of its entity arrives (``EcosystemState.observe`` tells the engine)
and on every ecosystem tick (which is how "has stopped reporting" is noticed -
silence never arrives).

**Edge-triggered, so it tells you once.** A condition fires when it *becomes*
true - not on every reading while it stays true - and again only after it has
been false in between (plus an optional cool-down). Declaring a condition that
is already true fires it once, because the person asked to know.

**An event is a record, and its response is bounded.** Firing appends an
``Event`` to a persisted log. Its response is either to *notify* (delivered by
the proactive path every other reminder uses) or to *propose* an action on a
connection - which is never taken here: a proposal waits for the person (a
"yes" in conversation raises the ordinary confirmation, or the owner approves
it in the Control Center), exactly the gate every consequential action already
passes through.

**Operators are an open registry.** ``register_operator`` adds a comparison
(the spatial model adds "inside"/"outside" a zone this way); nothing here
enumerates subjects, and a new kind of test joins without a redesign.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.services.declared_store import DeclaredDocument, slug
from src.services.observations.store import (
    Observation,
    ObservationStore,
    attribute_key,
    entity_key,
    is_number,
    summarize,
    utc_now,
)

MAX_EVENTS = 2000


# -- operators --------------------------------------------------------------------

@dataclass(frozen=True)
class ConditionOperator:
    """One kind of test. ``test(current, threshold, context)`` decides; the
    ``threshold`` it receives is already parsed to ``kind`` ("number", "text",
    "duration", "none", or anything a registering mechanism defines)."""

    name: str
    words: "tuple[str, ...]"
    kind: str
    test: "Callable[[Any, Any, Mapping[str, Any]], bool]" = field(repr=False, compare=False)
    describe: str = ""


_OPERATORS: "dict[str, ConditionOperator]" = {}
_OPERATORS_LOCK = threading.Lock()


def register_operator(operator: ConditionOperator) -> None:
    with _OPERATORS_LOCK:
        _OPERATORS[operator.name] = operator


def operators() -> "tuple[ConditionOperator, ...]":
    with _OPERATORS_LOCK:
        return tuple(_OPERATORS.values())


def operator_named(name: str) -> "ConditionOperator | None":
    with _OPERATORS_LOCK:
        return _OPERATORS.get(str(name or "").strip().lower())


def _num(value: Any) -> "float | None":
    if is_number(value):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _compare(fn: Callable[[float, float], bool]) -> "Callable[[Any, Any, Mapping[str, Any]], bool]":
    def test(current: Any, threshold: Any, context: Mapping[str, Any]) -> bool:
        a, b = _num(current), _num(threshold)
        return a is not None and b is not None and fn(a, b)
    return test


def _same(current: Any, threshold: Any, context: Mapping[str, Any]) -> bool:
    a, b = _num(current), _num(threshold)
    if a is not None and b is not None:
        return a == b
    return str(current).strip().lower() == str(threshold).strip().lower()


for _operator in (
    ConditionOperator("above", ("goes above", "rises above", "exceeds", "is above", "above", "over",
                                "greater than", "more than", "higher than"), "number", _compare(lambda a, b: a > b), ">"),
    ConditionOperator("below", ("drops below", "falls below", "goes below", "is below", "below", "under",
                                "less than", "lower than"), "number", _compare(lambda a, b: a < b), "<"),
    ConditionOperator("at_least", ("reaches", "at least", "hits"), "number", _compare(lambda a, b: a >= b), ">="),
    ConditionOperator("at_most", ("at most", "no more than"), "number", _compare(lambda a, b: a <= b), "<="),
    ConditionOperator("equals", ("becomes", "turns", "changes to", "equals", "is"), "text", _same, "="),
    ConditionOperator("not_equals", ("is not", "isn't", "stops being", "is no longer"), "text",
                      lambda c, t, ctx: not _same(c, t, ctx), "!="),
    ConditionOperator("changes", ("changes", "changed"), "none",
                      lambda c, t, ctx: "previous" in ctx and ctx["previous"] is not None and not _same(c, ctx["previous"], ctx),
                      "changes"),
    ConditionOperator("rises_by", ("rises by", "goes up by", "increases by", "climbs by"), "number",
                      lambda c, t, ctx: _num(ctx.get("change")) is not None and _num(t) is not None and ctx["change"] >= _num(t),
                      "rises by"),
    ConditionOperator("falls_by", ("falls by", "drops by", "goes down by", "decreases by"), "number",
                      lambda c, t, ctx: _num(ctx.get("change")) is not None and _num(t) is not None and -ctx["change"] >= _num(t),
                      "falls by"),
    ConditionOperator("stale", ("stops reporting", "goes quiet", "goes silent", "has no reading", "hasn't reported",
                                "stops updating"), "duration",
                      lambda c, t, ctx: ctx.get("silent_seconds") is not None and ctx["silent_seconds"] >= float(t),
                      "silent for"),
):
    register_operator(_operator)


def _unusual(current: Any, threshold: Any, context: Mapping[str, Any]) -> bool:
    """GM-09 -> GM-02: the reading is far from what its own recent history
    usually is - the same robust score the anomalies technique reports, so
    "unusual" means one thing everywhere."""
    from src.services.observations.techniques import UNUSUAL_SCORE, robust_score

    value = _num(current)
    history = list(context.get("recent") or ())[:-1]
    if value is None:
        return False
    score = robust_score(history, value)
    return score is not None and abs(score) > UNUSUAL_SCORE


register_operator(ConditionOperator(
    "unusual", ("does anything unusual", "does something unusual", "behaves unusually", "looks unusual",
                "is unusual", "goes abnormal", "looks abnormal", "is abnormal", "reads oddly", "acts strangely",
                "anything unusual", "anything abnormal"),
    "none", _unusual, "is unusual for it",
))


AGGREGATES = ("latest", "mean", "max", "min", "count", "change", "rate")


# -- the declaration --------------------------------------------------------------

@dataclass(frozen=True)
class ProposedAction:
    connection: str
    operation: str
    params: "Mapping[str, Any]" = field(default_factory=dict)

    def describe(self) -> str:
        detail = f" with {', '.join(f'{k}={v}' for k, v in self.params.items())}" if self.params else ""
        return f"'{self.operation}' on '{self.connection}'{detail}"

    def to_dict(self) -> dict:
        return {"connection": self.connection, "operation": self.operation, "params": dict(self.params)}


@dataclass(frozen=True)
class Condition:
    condition_id: str
    entity: str
    attribute: str
    operator: str
    threshold: Any = None
    window_seconds: int = 0
    aggregate: str = "latest"
    response: str = "notify"
    action: "ProposedAction | None" = None
    cooldown_seconds: int = 0
    enabled: bool = True
    label: str = ""
    declared_by: str = ""
    declared_at: str = ""
    # GM-15: when the entity is a group, whether any member or all must meet it.
    quantifier: str = "any"

    def __post_init__(self) -> None:
        if not " ".join(str(self.entity).split()) or not attribute_key(self.attribute):
            raise ValueError("a condition needs the entity and attribute it is about")
        operator = operator_named(self.operator)
        if operator is None:
            known = ", ".join(o.name for o in operators())
            raise ValueError(f"'{self.operator}' is not a known test; known: {known}")
        if self.aggregate not in AGGREGATES:
            raise ValueError(f"'{self.aggregate}' is not an aggregate; known: {', '.join(AGGREGATES)}")
        if self.response not in ("notify", "propose"):
            raise ValueError("a condition's response is 'notify' or 'propose'")
        if self.response == "propose" and self.action is None:
            raise ValueError("a condition that proposes an action must say which action")
        if operator.kind == "number" and _num(self.threshold) is None:
            raise ValueError(f"'{operator.name}' needs a number to compare with")
        if operator.kind == "duration" and _num(self.threshold) is None:
            raise ValueError(f"'{operator.name}' needs a length of time")
        if self.quantifier not in ("any", "all"):
            raise ValueError("a group condition applies to 'any' or 'all' of its members")
        object.__setattr__(self, "attribute", attribute_key(self.attribute))
        object.__setattr__(self, "operator", operator.name)

    def describe(self) -> str:
        if self.label:
            return self.label
        operator = operator_named(self.operator)
        over = f" ({self.aggregate} over {self.window_seconds}s)" if self.window_seconds else ""
        threshold = "" if operator.kind == "none" else f" {self.threshold}"
        if operator.kind == "duration":
            threshold = f" {int(float(self.threshold))}s"
        then = f", then propose {self.action.describe()}" if self.action else ""
        return f"{self.entity} {self.attribute.replace('_', ' ')}{over} {operator.describe or operator.name}{threshold}{then}"

    def to_dict(self) -> dict:
        return {
            "condition_id": self.condition_id, "entity": self.entity, "attribute": self.attribute,
            "operator": self.operator, "threshold": self.threshold, "window_seconds": self.window_seconds,
            "aggregate": self.aggregate, "response": self.response,
            "action": self.action.to_dict() if self.action else None, "cooldown_seconds": self.cooldown_seconds,
            "enabled": self.enabled, "label": self.label, "declared_by": self.declared_by,
            "declared_at": self.declared_at, "quantifier": self.quantifier,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Condition":
        action = data.get("action")
        return cls(
            condition_id=str(data["condition_id"]), entity=str(data["entity"]), attribute=str(data["attribute"]),
            operator=str(data["operator"]), threshold=data.get("threshold"),
            window_seconds=int(data.get("window_seconds") or 0), aggregate=str(data.get("aggregate") or "latest"),
            response=str(data.get("response") or "notify"),
            action=ProposedAction(str(action["connection"]), str(action["operation"]), dict(action.get("params") or {})) if action else None,
            cooldown_seconds=int(data.get("cooldown_seconds") or 0), enabled=bool(data.get("enabled", True)),
            label=str(data.get("label") or ""), declared_by=str(data.get("declared_by") or ""),
            declared_at=str(data.get("declared_at") or ""), quantifier=str(data.get("quantifier") or "any"),
        )


@dataclass(frozen=True)
class Event:
    event_id: str
    condition_id: str
    label: str
    fired_at: str
    value: Any = None
    detail: str = ""
    response: str = "notify"
    action: "dict | None" = None
    status: str = "new"          # new | delivered | proposed | approved | executed | dismissed | failed
    resolved_at: str = ""
    resolution: str = ""

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "condition_id": self.condition_id, "label": self.label,
            "fired_at": self.fired_at, "value": self.value, "detail": self.detail, "response": self.response,
            "action": self.action, "status": self.status, "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Event":
        return cls(**{k: data.get(k, default) for k, default in (
            ("event_id", ""), ("condition_id", ""), ("label", ""), ("fired_at", ""), ("value", None),
            ("detail", ""), ("response", "notify"), ("action", None), ("status", "new"),
            ("resolved_at", ""), ("resolution", ""),
        )})

    def describe(self) -> str:
        when = self.fired_at[:16].replace("T", " ")
        tail = f" - proposed {self.action['operation']} on {self.action['connection']}" if self.action else ""
        return f"[{self.status}] {when} UTC {self.label}: {self.detail}{tail}"


# -- stores -------------------------------------------------------------------------

class ConditionRegistry:
    def __init__(self, root: "Path | str") -> None:
        self._document = DeclaredDocument(Path(root) / "conditions.json", key="conditions")
        self._state = DeclaredDocument(Path(root) / "condition_state.json", key="state")

    def all(self) -> "list[Condition]":
        found = []
        for item in self._document.load():
            try:
                found.append(Condition.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue
        return found

    def get(self, condition_id: str) -> "Condition | None":
        return next((c for c in self.all() if c.condition_id in (condition_id, slug(condition_id))), None)

    def declare(self, condition: Condition) -> Condition:
        def change(items: list) -> Condition:
            items[:] = [i for i in items if i.get("condition_id") != condition.condition_id]
            items.append(condition.to_dict())
            return condition
        return self._document.update(change)

    def remove(self, condition_id: str) -> bool:
        target = self.get(condition_id)
        if target is None:
            return False
        self._document.update(lambda items: items.__setitem__(slice(None), [i for i in items if i.get("condition_id") != target.condition_id]))
        self._state.update(lambda items: items.__setitem__(slice(None), [i for i in items if i.get("condition_id") != target.condition_id]))
        return True

    def set_enabled(self, condition_id: str, enabled: bool) -> "Condition | None":
        target = self.get(condition_id)
        if target is None:
            return None
        return self.declare(Condition.from_dict({**target.to_dict(), "enabled": enabled}))

    def state(self) -> "dict[str, dict]":
        return {str(item.get("condition_id")): item for item in self._state.load()}

    def set_state(self, condition_id: str, **values: Any) -> None:
        def change(items: list) -> None:
            current = next((i for i in items if i.get("condition_id") == condition_id), None)
            if current is None:
                current = {"condition_id": condition_id}
                items.append(current)
            current.update(values)
        self._state.update(change)


class EventLog:
    def __init__(self, root: "Path | str") -> None:
        self._document = DeclaredDocument(Path(root) / "events.json", key="events")

    def all(self) -> "list[Event]":
        return [Event.from_dict(item) for item in self._document.load()]

    def get(self, event_id: str) -> "Event | None":
        return next((e for e in self.all() if e.event_id == event_id), None)

    def append(self, event: Event) -> Event:
        def change(items: list) -> Event:
            items.append(event.to_dict())
            del items[:-MAX_EVENTS]
            return event
        return self._document.update(change)

    def set_status(self, event_id: str, status: str, resolution: str = "", *, now: "datetime | None" = None) -> "Event | None":
        def change(items: list) -> "Event | None":
            for item in items:
                if item.get("event_id") == event_id:
                    item["status"] = status
                    if resolution:
                        item["resolution"] = resolution
                    item["resolved_at"] = (now or utc_now()).isoformat()
                    return Event.from_dict(item)
            return None
        return self._document.update(change)

    def pending(self) -> "list[Event]":
        """Every event nobody has seen or answered yet."""
        return [e for e in self.all() if e.status in ("new", "proposed")]


# -- evaluation ----------------------------------------------------------------------

def parse_threshold(operator: ConditionOperator, text: Any) -> Any:
    if operator.kind == "number":
        match = re.search(r"-?\d+(?:\.\d+)?", str(text))
        return float(match.group(0)) if match else None
    if operator.kind == "duration":
        from src.services.observations.store import parse_duration

        spoken = str(text or "").strip().lower()
        spoken = re.sub(r"\b(?:for|at least|more than|over)\s+", "", spoken)
        spoken = re.sub(r"^(an?|one)\s+", "1 ", spoken)
        found = parse_duration(spoken) or parse_duration(spoken.replace(" ", ""))
        return found.total_seconds() if found else None
    return None if operator.kind == "none" else str(text).strip()


class ConditionEngine:
    """Evaluates declared conditions against the observation store and
    appends the events they raise. ``members`` resolves a group name to its
    member entities (GM-15); without one, every entity stands alone."""

    def __init__(
        self,
        store: ObservationStore,
        registry: ConditionRegistry,
        events: EventLog,
        *,
        clock: "Callable[[], datetime]" = utc_now,
        members: "Callable[[str], Sequence[str] | None] | None" = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._events = events
        self._clock = clock
        self._members = members
        self._lock = threading.RLock()
        self._subscribers: "list[Callable[[Event], Any]]" = []

    def subscribe(self, subscriber: "Callable[[Event], Any]") -> None:
        """Be told of every event as it fires (the live server delivers into
        the open conversation this way)."""
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: "Callable[[Event], Any]") -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def bind_members(self, members: "Callable[[str], Sequence[str] | None]") -> None:
        self._members = members

    def _entities_for(self, condition: Condition) -> "list[str]":
        if self._members is not None:
            members = self._members(condition.entity)
            if members:
                return list(members)
        return [condition.entity]

    def _value(self, entity: str, condition: Condition, now: datetime) -> "tuple[Any, dict]":
        window = timedelta(seconds=condition.window_seconds) if condition.window_seconds else None
        since = now - window if window else None
        series = [o for o in self._store.series(entity, condition.attribute, since=since, until=now)
                  if o.attribute == condition.attribute]
        context: dict = {"count": len(series)}
        latest_ever = self._store.series(entity, condition.attribute, limit=1)
        latest_ever = [o for o in latest_ever if o.attribute == condition.attribute] or latest_ever
        if latest_ever:
            context["silent_seconds"] = (now - latest_ever[-1].observed_at).total_seconds()
            context["last_at"] = latest_ever[-1].observed_at.isoformat()
        else:
            context["silent_seconds"] = None
        if len(series) >= 2:
            context["previous"] = series[-2].value
        # What it has recently been, oldest first, the current reading last -
        # for a test that judges a reading against its own history (GM-09).
        context["recent"] = [o.value for o in series[-200:]]
        summary = summarize(entity, condition.attribute, series, since=since, until=now)
        context["change"] = summary.change
        if not series:
            return None, context
        if condition.aggregate == "latest":
            return series[-1].value, context
        values = {"mean": summary.mean, "max": summary.maximum, "min": summary.minimum, "count": summary.count,
                  "change": summary.change, "rate": summary.rate_per_hour}
        return values.get(condition.aggregate), context

    def evaluate(self, condition: Condition, *, now: "datetime | None" = None) -> "Event | None":
        now = now or self._clock()
        operator = operator_named(condition.operator)
        results = []
        for entity in self._entities_for(condition):
            value, context = self._value(entity, condition, now)
            if value is None and operator.kind != "duration":
                continue
            if operator.kind == "duration" and context.get("silent_seconds") is None:
                continue
            results.append((entity, value, context, bool(operator.test(value, condition.threshold, context))))
        if not results:
            return None
        met = [r for r in results if r[3]]
        truth = bool(met) if condition.quantifier == "any" else (len(met) == len(results))
        with self._lock:
            state = self._registry.state().get(condition.condition_id, {})
            was = state.get("truth")
            fire = truth and was is not True
            last_fired = state.get("last_fired_at")
            if fire and last_fired and condition.cooldown_seconds:
                try:
                    if now - datetime.fromisoformat(last_fired) < timedelta(seconds=condition.cooldown_seconds):
                        fire = False
                except ValueError:
                    pass
            shown = met[0] if met else results[-1]
            self._registry.set_state(condition.condition_id, truth=truth, value=shown[1],
                                     evaluated_at=now.isoformat(), **({"last_fired_at": now.isoformat()} if fire else {}))
            if not fire:
                return None
            where = f"{shown[0]} " if shown[0] != condition.entity else ""
            if operator.kind == "duration":
                detail = f"{where}no {condition.attribute.replace('_', ' ')} for {int(shown[2]['silent_seconds'])} s"
            else:
                unit = ""
                detail = f"{where}{condition.attribute.replace('_', ' ')} is {shown[1]}{unit}"
            if condition.quantifier == "all" and len(results) > 1:
                detail += f" (all {len(results)} members)"
            event = Event(
                event_id=uuid.uuid4().hex[:12], condition_id=condition.condition_id, label=condition.describe(),
                fired_at=now.isoformat(), value=shown[1], detail=detail, response=condition.response,
                action=condition.action.to_dict() if condition.action else None,
                status="proposed" if condition.response == "propose" else "new",
            )
            self._events.append(event)
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:  # noqa: BLE001 - delivery never un-fires an event
                pass
        return event

    def on_arrival(self, observations: Sequence[Observation]) -> "list[Event]":
        """Evaluate every condition an arriving batch could change."""
        touched = {(entity_key(o.entity), o.attribute) for o in observations}
        # A device's clock may run ahead of this one: evaluating "now" before
        # the reading's own timestamp would leave the reading that just arrived
        # outside the window. The later of the two is the honest "now".
        now = max([self._clock(), *(o.observed_at for o in observations)])
        fired = []
        for condition in self._registry.all():
            if not condition.enabled:
                continue
            entities = {entity_key(e) for e in self._entities_for(condition)}
            if any(key in entities and attribute == condition.attribute for key, attribute in touched):
                event = self.evaluate(condition, now=now)
                if event is not None:
                    fired.append(event)
        return fired

    def evaluate_all(self, now: "datetime | None" = None) -> "list[Event]":
        """Evaluate every enabled condition - what the tick does, and how
        silence ("stops reporting") is ever noticed."""
        fired = []
        for condition in self._registry.all():
            if condition.enabled:
                event = self.evaluate(condition, now=now)
                if event is not None:
                    fired.append(event)
        return fired


def new_condition_id(entity: str, attribute: str, operator: str, existing: Iterable[Condition]) -> str:
    base = slug(f"{entity} {attribute.split('.')[-1]} {operator}")
    taken = {c.condition_id for c in existing}
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}_{n}"
        n += 1
    return candidate
