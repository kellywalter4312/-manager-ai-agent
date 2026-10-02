"""The ecosystem's own declared state - one object the composition root
builds once from ``Settings.ecosystem_dir`` and every surface shares.

It holds the stores the GM campaign added, each a plain file-backed store
safe to share for the life of a process (the same shape ``ArtifactStore``
already has on ``Runtime``). Nothing in here reasons, routes or speaks;
capabilities, tools and the Control Center reach these stores, and the
ecosystem tick (``src/services/automation/tick.py``) runs what is due in them.

**One way in for everything observed.** ``observe()`` is the single entry
point every observing source uses - a watch, a stream, a perception run, a
capture, a supervised task - so every observation lands in the one store and
every listener for arrivals (conditions, GM-02) hears it the moment it lands,
whichever source produced it. A source that wrote to the store directly would
be invisible to everything that reacts; there is deliberately no such path.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.services.automation.conditions import ConditionEngine, ConditionRegistry, EventLog
from src.services.automation.tick import register_tick_step
from src.services.connections.groups import GroupRegistry
from src.services.views.live_views import LiveViewRegistry
from src.services.memory.sensitivity import SensitivityPolicy, retention_step
from src.services.observations.store import Observation, ObservationStore
from src.services.observations.watches import WatchRegistry

logger = logging.getLogger(__name__)

ArrivalListener = Callable[[Sequence[Observation]], Any]


@dataclass(frozen=True)
class EcosystemState:
    root: Path
    observations: ObservationStore      # GM-01
    watches: WatchRegistry              # GM-01 / GM-04
    conditions: ConditionRegistry       # GM-02
    events: EventLog                    # GM-02
    engine: ConditionEngine             # GM-02
    sensitivity: SensitivityPolicy      # GM-13
    groups: GroupRegistry               # GM-15
    views: LiveViewRegistry             # GM-06
    _listeners: "list[ArrivalListener]" = field(default_factory=list, repr=False, compare=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    _memories: "list[Callable[[], Iterable[Any]]]" = field(default_factory=list, repr=False, compare=False)
    _providers: "dict[str, Any]" = field(default_factory=dict, repr=False, compare=False)

    def bind_provider(self, axis: str, provider: Any) -> None:
        """The live backend of a provider axis a mechanism here consumes
        (perception for frames a watch delivers, GM-03) - bound by the
        composition root at start and again on every live backend rebind, so
        the ecosystem never builds or chooses a backend of its own."""
        with self._lock:
            self._providers[str(axis)] = provider

    def provider(self, axis: str) -> Any:
        with self._lock:
            return self._providers.get(str(axis))

    def bind_memories(self, memories: "Callable[[], Iterable[Any]]") -> None:
        """GM-13: the live memory stores whose records retention applies to
        (bound by the composition root, which owns them)."""
        with self._lock:
            self._memories[:] = [memories]

    def memories(self) -> "list[Any]":
        with self._lock:
            sources = list(self._memories)
        found: list = []
        for source in sources:
            found.extend(source() or ())
        return found

    def listen(self, listener: ArrivalListener) -> None:
        """Be told of every observation as it is recorded."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def observe(self, observations: Iterable[Observation]) -> int:
        """Record observations and tell every listener. Returns how many were
        recorded. A listener that fails is logged and never stops the others,
        and never un-records what was observed."""
        batch = list(observations)
        if not batch:
            return 0
        recorded = self.observations.record(batch)
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(batch)
            except Exception:  # noqa: BLE001 - one reaction must not cost the rest
                logger.exception("an observation listener failed")
        return recorded

    def group_entities(self, name: str) -> "list[str]":
        """GM-15: the observed entities a group stands for - its entity
        members, and each connection member's watched entities (or its own
        name when nothing renames them). Empty for a name that is no group."""
        if self.groups.get(name) is None:
            return []
        watched: "dict[str, list[str]]" = {}
        for watch in self.watches.all():
            watched.setdefault(watch.connection, []).append(watch.entity)
        return self.groups.member_entities(name, watched=watched)

    def reading_observer(self) -> "Callable[[str, str, Mapping[str, Any], Any], None]":
        """GM-01: an observer for ``ConnectionActionTool`` that keeps a reading
        a person asked for when that (connection, operation) is watched - so
        the history is the same whether the scheduler or a person took the
        reading. Nothing is observed for an operation nobody declared a watch on."""
        from datetime import datetime, timezone

        from src.services.observations.extract import observations_from

        def kept(connection_name: str, operation: str, arguments: "Mapping[str, Any]", result: Any) -> None:
            watches = [w for w in self.watches.for_operation(connection_name, operation)
                       if w.enabled and all(str(arguments.get(k, v)) == str(v) for k, v in w.params.items())]
            if not watches or result.data is None:
                return
            for watch in watches:
                self.observe(observations_from(
                    result.data, entity=watch.entity, source=f"watch:{watch.watch_id} (read on request)",
                    observed_at=result.executed_at or datetime.now(timezone.utc), keep=watch.keep,
                    units=dict(watch.units), sensitivity=watch.sensitivity,
                ))
        return kept


def build_ecosystem_state(settings: Any) -> EcosystemState:
    root = Path(getattr(settings, "ecosystem_dir", "data/ecosystem"))
    observations = ObservationStore(root)
    conditions = ConditionRegistry(root)
    events = EventLog(root)
    state = EcosystemState(
        root=root,
        observations=observations,
        watches=WatchRegistry(root),
        conditions=conditions,
        events=events,
        engine=ConditionEngine(observations, conditions, events),
        sensitivity=SensitivityPolicy(root),
        groups=GroupRegistry(root),
        views=LiveViewRegistry(root),
    )
    # GM-15: a condition declared on a group is tested on each of its members.
    state.engine.bind_members(state.group_entities)
    # GM-02: every condition an arriving observation could change is checked
    # the moment it lands, whichever source observed it.
    state.listen(state.engine.on_arrival)
    return state


def _sensitivity_retention(ecosystem: Any, connections: Any, now) -> "str | None":
    """GM-13 on every tick: forget what has outlived its level's retention."""
    policy = getattr(ecosystem, "sensitivity", None)
    if policy is None or not policy.retention():
        return None
    lines = retention_step(policy, ecosystem.memories, ecosystem.observations, now)
    return "; ".join(lines) if lines else None


register_tick_step("sensitivity_retention", _sensitivity_retention)
