"""What is worth observing, and the sampler that observes it (GM-01, GM-04).

A **watch** is a declaration: "read <operation> on <connection> every <interval>
and keep what it says as observations of <entity>". The owner declares one in
the Control Center, or a person asks for one in conversation ("keep an eye on
the proofing cabinet every five minutes"); either way it is persisted, listed,
and removable, and nothing samples anything nobody declared.

**Only what is safe to repeat is ever watched.** A watch runs with nobody
watching it, so an operation the connection says changes state
(``requires_confirmation_for``) can never be declared as one - the refusal
names the rule. Reading is the whole of what a watch does.

**Two modes, one declaration.** A request operation is *sampled* each time its
interval comes round; a stream operation (GM-04) is *listened to* in bounded
windows, recording each item as it arrives. Either way the observations land
in the one ``ObservationStore`` and whatever listens for arrivals (conditions,
GM-02) hears them at once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.providers.connection import Connection, StreamItem
from src.services.declared_store import DeclaredDocument, slug
from src.services.observations.extract import observations_from
from src.services.observations.store import Observation, utc_now

MIN_INTERVAL_SECONDS = 5
DEFAULT_INTERVAL_SECONDS = 60
MODE_SAMPLE = "sample"
MODE_STREAM = "stream"


@dataclass(frozen=True)
class Watch:
    watch_id: str
    connection: str
    operation: str
    entity: str
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    params: "Mapping[str, Any]" = field(default_factory=dict)
    keep: "tuple[str, ...]" = ()
    units: "Mapping[str, str]" = field(default_factory=dict)
    sensitivity: str = ""
    mode: str = MODE_SAMPLE
    enabled: bool = True
    declared_by: str = ""
    declared_at: str = ""
    # GM-04: a stream watch listens in windows of this length each time it is due.
    listen_seconds: int = 20
    # GM-03: what a frame arriving on this watch is handed to (e.g. "perception").
    frames_to: str = ""

    def __post_init__(self) -> None:
        for name in ("connection", "operation", "entity"):
            if not " ".join(str(getattr(self, name) or "").split()):
                raise ValueError(f"a watch needs its {name}")
        if int(self.interval_seconds) < MIN_INTERVAL_SECONDS:
            raise ValueError(f"a watch samples at most every {MIN_INTERVAL_SECONDS} seconds")
        if self.mode not in (MODE_SAMPLE, MODE_STREAM):
            raise ValueError(f"a watch is either '{MODE_SAMPLE}' or '{MODE_STREAM}', not '{self.mode}'")
        object.__setattr__(self, "interval_seconds", int(self.interval_seconds))
        object.__setattr__(self, "listen_seconds", max(1, int(self.listen_seconds)))
        object.__setattr__(self, "keep", tuple(str(k) for k in self.keep if str(k).strip()))
        object.__setattr__(self, "params", {str(k): v for k, v in dict(self.params).items()})

    def describe(self) -> str:
        every = f"every {_human_seconds(self.interval_seconds)}"
        how = "listens to" if self.mode == MODE_STREAM else "reads"
        kept = f", keeping {', '.join(self.keep)}" if self.keep else ""
        state = "" if self.enabled else " (paused)"
        return f"{self.watch_id}: {how} {self.connection}/{self.operation} {every} as '{self.entity}'{kept}{state}"

    def to_dict(self) -> dict:
        return {
            "watch_id": self.watch_id, "connection": self.connection, "operation": self.operation,
            "entity": self.entity, "interval_seconds": self.interval_seconds, "params": dict(self.params),
            "keep": list(self.keep), "units": dict(self.units), "sensitivity": self.sensitivity,
            "mode": self.mode, "enabled": self.enabled, "declared_by": self.declared_by,
            "declared_at": self.declared_at, "listen_seconds": self.listen_seconds, "frames_to": self.frames_to,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Watch":
        return cls(
            watch_id=str(data["watch_id"]), connection=str(data["connection"]), operation=str(data["operation"]),
            entity=str(data.get("entity") or data["connection"]),
            interval_seconds=int(data.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)),
            params=dict(data.get("params") or {}), keep=tuple(data.get("keep") or ()),
            units=dict(data.get("units") or {}), sensitivity=str(data.get("sensitivity", "")),
            mode=str(data.get("mode", MODE_SAMPLE)), enabled=bool(data.get("enabled", True)),
            declared_by=str(data.get("declared_by", "")), declared_at=str(data.get("declared_at", "")),
            listen_seconds=int(data.get("listen_seconds", 20)), frames_to=str(data.get("frames_to", "")),
        )


def _human_seconds(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} day(s)"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour(s)"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute(s)"
    return f"{seconds} second(s)"


def watchable(connection: Connection, operation: str) -> "str | None":
    """Why ``operation`` on ``connection`` cannot be watched, or None."""
    if operation not in connection.supported_operations and not connection.supports_streaming(operation):
        offered = ", ".join(connection.supported_operations) or "nothing"
        return f"'{connection.name}' has no operation named '{operation}' (it offers: {offered})"
    if connection.requires_confirmation_for(operation):
        return (
            f"'{operation}' on '{connection.name}' changes something on the real service, and a watch runs "
            "unattended - only a safe read can be watched"
        )
    return None


@dataclass(frozen=True)
class SampleOutcome:
    watch_id: str
    ok: bool
    recorded: int
    detail: str
    observations: "tuple[Observation, ...]" = ()
    frames: "tuple[StreamItem, ...]" = ()


class WatchRegistry:
    """Declared watches (``watches.json``) and their sampling state
    (``watch_state.json``), under the ecosystem root."""

    def __init__(self, root: "Path | str") -> None:
        root = Path(root)
        self._document = DeclaredDocument(root / "watches.json", key="watches")
        self._state = DeclaredDocument(root / "watch_state.json", key="state")

    @property
    def problem(self) -> str:
        return self._document.problem

    def all(self) -> "list[Watch]":
        watches = []
        for item in self._document.load():
            try:
                watches.append(Watch.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue
        return watches

    def get(self, watch_id: str) -> "Watch | None":
        wanted = slug(watch_id)
        return next((w for w in self.all() if w.watch_id == wanted or w.watch_id == watch_id), None)

    def for_operation(self, connection: str, operation: str) -> "list[Watch]":
        return [w for w in self.all() if w.connection == connection and w.operation == operation]

    def declare(self, watch: Watch) -> Watch:
        def change(items: list) -> Watch:
            items[:] = [item for item in items if item.get("watch_id") != watch.watch_id]
            items.append(watch.to_dict())
            return watch
        return self._document.update(change)

    def remove(self, watch_id: str) -> bool:
        target = self.get(watch_id)
        if target is None:
            return False

        def change(items: list) -> bool:
            before = len(items)
            items[:] = [item for item in items if item.get("watch_id") != target.watch_id]
            return len(items) < before
        removed = self._document.update(change)
        self._state.update(lambda items: items.__setitem__(slice(None), [i for i in items if i.get("watch_id") != target.watch_id]))
        return removed

    def set_enabled(self, watch_id: str, enabled: bool) -> "Watch | None":
        target = self.get(watch_id)
        if target is None:
            return None
        updated = Watch.from_dict({**target.to_dict(), "enabled": enabled})
        return self.declare(updated)

    # -- state -------------------------------------------------------------

    def state(self) -> "dict[str, dict]":
        return {str(item.get("watch_id")): item for item in self._state.load()}

    def note(self, outcome: SampleOutcome, at: datetime) -> None:
        def change(items: list) -> None:
            current = next((item for item in items if item.get("watch_id") == outcome.watch_id), None)
            if current is None:
                current = {"watch_id": outcome.watch_id, "samples": 0, "failures": 0}
                items.append(current)
            current["last_sampled_at"] = at.isoformat()
            current["last_ok"] = outcome.ok
            current["last_detail"] = outcome.detail[:300]
            current["samples"] = int(current.get("samples", 0)) + (1 if outcome.ok else 0)
            current["failures"] = int(current.get("failures", 0)) + (0 if outcome.ok else 1)
            current["recorded"] = int(current.get("recorded", 0)) + outcome.recorded
        self._state.update(change)

    def due(self, now: "datetime | None" = None) -> "list[Watch]":
        now = now or utc_now()
        state = self.state()
        due = []
        for watch in self.all():
            if not watch.enabled:
                continue
            last = state.get(watch.watch_id, {}).get("last_sampled_at")
            if last:
                try:
                    if now - datetime.fromisoformat(last) < timedelta(seconds=watch.interval_seconds):
                        continue
                except ValueError:
                    pass
            due.append(watch)
        return due


def new_watch_id(entity: str, operation: str, existing: Iterable[Watch]) -> str:
    base = slug(f"{entity} {operation}")
    taken = {w.watch_id for w in existing}
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def sample(
    watch: Watch,
    connection: "Connection | None",
    record: "Callable[[list[Observation]], int]",
    *,
    now: "datetime | None" = None,
    on_item: "Callable[[StreamItem, list[Observation]], None] | None" = None,
) -> SampleOutcome:
    """Run one watch once: read (or listen), keep what it says. Never raises
    for an ordinary failure - the outcome says what happened."""
    now = now or utc_now()
    if connection is None:
        return SampleOutcome(watch.watch_id, False, 0, f"'{watch.connection}' is not a connection this system has now")
    refusal = watchable(connection, watch.operation)
    if refusal:
        return SampleOutcome(watch.watch_id, False, 0, refusal)
    source = f"watch:{watch.watch_id} {watch.connection}/{watch.operation}"
    if watch.mode == MODE_STREAM or connection.supports_streaming(watch.operation):
        recorded: list[Observation] = []
        frames: list[StreamItem] = []

        def arrive(item: StreamItem) -> bool:
            if item.payload is not None:
                frames.append(item)
            if item.data is not None:
                found = observations_from(
                    item.data, entity=watch.entity, source=source, observed_at=item.received_at,
                    keep=watch.keep, units=dict(watch.units), sensitivity=watch.sensitivity,
                )
                record(found)
                recorded.extend(found)
                if on_item is not None:
                    on_item(item, found)
            elif on_item is not None:
                on_item(item, [])
            return True

        result = connection.stream(
            watch.operation, max_items=10_000, max_seconds=float(watch.listen_seconds), on_item=arrive,
            **{k: v for k, v in watch.params.items()},
        )
        detail = result.detail or f"{len(result.items)} item(s), ended: {result.ended_because}"
        return SampleOutcome(watch.watch_id, result.ok, len(recorded), detail, tuple(recorded), tuple(frames))

    result = connection.execute(watch.operation, **{k: v for k, v in watch.params.items()})
    if not result.ok:
        return SampleOutcome(watch.watch_id, False, 0, result.detail or result.status.value)
    observed_at = result.executed_at or now
    if result.data is None:
        text = (result.detail or "").strip()
        if result.payload is not None:
            item = StreamItem(sequence=0, received_at=observed_at, payload=result.payload, content_type=result.content_type)
            if on_item is not None:
                on_item(item, [])
            return SampleOutcome(watch.watch_id, True, 0, result.detail, (), (item,))
        data: Any = _number_or_text(text)
    else:
        data = result.data
    found = observations_from(
        data, entity=watch.entity, source=source, observed_at=observed_at,
        keep=watch.keep, units=dict(watch.units), sensitivity=watch.sensitivity,
    )
    recorded_count = record(found)
    if on_item is not None:
        on_item(StreamItem(sequence=0, received_at=observed_at, data=data if not isinstance(data, str) or len(data) < 200 else None), found)
    return SampleOutcome(
        watch.watch_id, True, recorded_count,
        f"recorded {recorded_count} observation(s) of '{watch.entity}'" if recorded_count else "read succeeded, nothing observable in the answer",
        tuple(found),
    )


def _number_or_text(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        pass
    try:
        return float(text) if "." in text or "e" in text.lower() else int(text)
    except ValueError:
        return text if text and len(text) <= 200 else None
