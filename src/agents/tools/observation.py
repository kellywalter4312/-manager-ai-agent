"""The governed boundary between an agent and the ecosystem's observations
(GM-01): what has been observed, summaries over a window, and the watches
that keep observing.

One tool, a small closed set of operations, handed JSON and returning JSON -
the shape ``artifact_runtime``/``image_intelligence`` already use. It owns no
store and no connection: the ``EcosystemState`` it is given owns the stores,
and every connection it samples comes from the same ``ConnectionResolver``
the rest of the system uses, under the same "only a safe read can be watched"
rule the watch module enforces.

**Sensitivity is a ceiling on the tool, not a filter in the handler.** The
tool is built with the highest level its holder may read (``max_sensitivity``);
an observation above it is simply not returned, so no capability handler can
forget to check.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Callable

from src.agents.tools.tool import Tool, ToolSpec
from src.services.declared_store import slug
from src.services.observations.store import utc_now
from src.services.observations.watches import (
    DEFAULT_INTERVAL_SECONDS,
    MODE_SAMPLE,
    MODE_STREAM,
    Watch,
    new_watch_id,
    sample,
    watchable,
)

OBSERVATION_TOOL = "observation"
_OPERATIONS = ("entities", "summary", "series", "latest", "watch", "unwatch", "watches", "sample", "listen",
               "techniques", "analyze")


class ObservationTool(Tool):
    def __init__(
        self,
        ecosystem: Any,
        connections: Any,
        *,
        owner_agent: str,
        max_sensitivity: str = "sensitive",
        clock: "Callable[[], Any]" = utc_now,
    ) -> None:
        self._ecosystem = ecosystem
        self._connections = connections
        self._owner_agent = owner_agent
        self._max_sensitivity = max_sensitivity
        self._clock = clock

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=OBSERVATION_TOOL,
            description=(
                "What the ecosystem has observed over time - entities, their attributes, summaries over a "
                "window - what can be found in them with the governed series techniques (peaks, anomalies, "
                "rhythms, relationships), and the watches that keep observing (declare, remove, sample now). "
                "Only a safe read can be watched."
            ),
            schema={"operation": str},
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        try:
            content = json.loads(arguments.get("content") or "{}") if str(arguments.get("content") or "").strip() else {}
        except ValueError as exc:
            raise ValueError(f"observation content must be JSON: {exc}") from exc
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown observation operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        return json.dumps(getattr(self, f"_{operation}")(content))

    # -- reading -----------------------------------------------------------

    def _entities(self, content: dict) -> dict:
        entities = self._ecosystem.observations.entities(max_sensitivity=self._max_sensitivity)
        return {"entities": [
            {"name": e["name"], "attributes": sorted(e["attributes"]), "sensitivity": e.get("sensitivity", ""),
             "counts": {a: v["count"] for a, v in e["attributes"].items()},
             "last": max((v["last"] for v in e["attributes"].values()), default=None)}
            for e in entities
        ]}

    def _window(self, content: dict) -> "timedelta | None":
        seconds = content.get("window_seconds")
        return timedelta(seconds=float(seconds)) if seconds else None

    def _summary(self, content: dict) -> dict:
        entity, attribute = str(content.get("entity") or ""), str(content.get("attribute") or "")
        if not entity or not attribute:
            raise ValueError("a summary needs an entity and an attribute")
        summary = self._ecosystem.observations.summarize(
            entity, attribute, window=self._window(content), now=self._clock(), max_sensitivity=self._max_sensitivity,
        )
        return {"summary": summary.to_dict(), "text": summary.describe()}

    def _series(self, content: dict) -> dict:
        entity = str(content.get("entity") or "")
        window = self._window(content)
        now = self._clock()
        found = self._ecosystem.observations.series(
            entity, content.get("attribute") or None, since=(now - window) if window else None, until=now,
            limit=int(content.get("limit") or 500), max_sensitivity=self._max_sensitivity,
        )
        return {"entity": entity, "points": [
            {"attribute": o.attribute, "value": o.value, "at": o.observed_at.isoformat(), "unit": o.unit, "source": o.source}
            for o in found
        ]}

    def _latest(self, content: dict) -> dict:
        entity = str(content.get("entity") or "")
        latest = self._ecosystem.observations.latest(entity, max_sensitivity=self._max_sensitivity)
        return {"entity": entity, "latest": {
            attribute: {"value": o.value, "at": o.observed_at.isoformat(), "unit": o.unit}
            for attribute, o in sorted(latest.items())
        }}

    # -- GM-09: series techniques ------------------------------------------

    def _techniques(self, content: dict) -> dict:
        from src.services.observations.techniques import current_techniques

        return {"techniques": [
            {"id": t.technique_id, "label": t.label, "purpose": t.purpose, "words": list(t.words), "inputs": t.inputs,
             "produces_series": t.produces_series, "minimum": t.minimum}
            for t in current_techniques()
        ]}

    def _readings(self, entity: str, attribute: str, window: "timedelta | None") -> list:
        now = self._clock()
        found = self._ecosystem.observations.series(
            entity, attribute, since=(now - window) if window else None, until=now, limit=5000,
            max_sensitivity=self._max_sensitivity,
        )
        exact = [o for o in found if o.attribute == attribute] or found
        return [(o.observed_at, o.value) for o in exact]

    def _analyze(self, content: dict) -> dict:
        from src.services.observations.techniques import current_techniques

        wanted = str(content.get("technique") or "").strip().lower()
        technique = next((t for t in current_techniques() if t.technique_id == wanted), None)
        if technique is None:
            available = ", ".join(t.technique_id for t in current_techniques()) or "none"
            raise ValueError(f"no series technique called '{wanted}' is available now (available: {available})")
        window = self._window(content)
        inputs = [(str(content.get("entity") or ""), str(content.get("attribute") or ""))]
        if technique.inputs == 2:
            inputs.append((str(content.get("with_entity") or content.get("entity") or ""),
                           str(content.get("with_attribute") or "")))
        series = []
        for entity, attribute in inputs:
            if not entity or not attribute:
                raise ValueError(f"{technique.label} needs an entity and an attribute for each of its {technique.inputs} series")
            series.append(self._readings(entity, attribute, window))
        result = technique.apply(series)
        return {"result": result.to_dict(), "inputs": [{"entity": e, "attribute": a, "readings": len(s)}
                                                        for (e, a), s in zip(inputs, series)]}

    # -- watches -----------------------------------------------------------

    def _watch(self, content: dict) -> dict:
        connection_name = str(content.get("connection") or "").strip()
        operation = str(content.get("operation") or "").strip()
        connection = self._connections.get(connection_name) if self._connections is not None else None
        if connection is None or connection.name != connection_name:
            raise ValueError(f"'{connection_name}' is not a connection this system has")
        refusal = watchable(connection, operation)
        if refusal:
            raise ValueError(refusal)
        entity = " ".join(str(content.get("entity") or connection_name).split())
        # GM-13: what this source reports is as sensitive as the owner's rules
        # say the source (or the entity it is kept as) is.
        policy = getattr(self._ecosystem, "sensitivity", None)
        if not content.get("sensitivity") and policy is not None:
            content["sensitivity"] = policy.classify(entity, source=connection_name)
        registry = self._ecosystem.watches
        existing = registry.all()
        same = [w for w in existing if w.connection == connection_name and w.operation == operation and w.entity == entity]
        watch_id = same[0].watch_id if same else new_watch_id(entity, operation, existing)
        watch = Watch(
            watch_id=watch_id, connection=connection_name, operation=operation, entity=entity,
            interval_seconds=int(content.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS),
            params=dict(content.get("params") or {}), keep=tuple(content.get("keep") or ()),
            units=dict(content.get("units") or {}), sensitivity=str(content.get("sensitivity") or ""),
            mode=MODE_STREAM if connection.supports_streaming(operation) else MODE_SAMPLE,
            declared_by=str(content.get("declared_by") or self._owner_agent),
            declared_at=self._clock().isoformat(), frames_to=str(content.get("frames_to") or ""),
            listen_seconds=int(content.get("listen_seconds") or 20),
        )
        registry.declare(watch)
        return {"watch": watch.to_dict(), "replaced": bool(same), "text": watch.describe()}

    def _unwatch(self, content: dict) -> dict:
        watch_id = str(content.get("watch_id") or "")
        return {"removed": self._ecosystem.watches.remove(watch_id), "watch_id": slug(watch_id)}

    def _watches(self, content: dict) -> dict:
        state = self._ecosystem.watches.state()
        return {"watches": [
            {**w.to_dict(), "text": w.describe(), "state": state.get(w.watch_id, {})}
            for w in self._ecosystem.watches.all()
        ]}

    def _listen(self, content: dict) -> dict:
        """GM-04: listen to a stream now, within limits, keeping what it says
        as observations as each item arrives (so conditions react mid-listen)."""
        from src.services.observations.extract import observations_from

        connection_name = str(content.get("connection") or "").strip()
        operation = str(content.get("operation") or "").strip()
        connection = self._connections.get(connection_name) if self._connections is not None else None
        if connection is None or connection.name != connection_name:
            raise ValueError(f"'{connection_name}' is not a connection this system has")
        refusal = watchable(connection, operation)
        if refusal:
            raise ValueError(refusal)
        if not connection.supports_streaming(operation):
            raise ValueError(f"'{operation}' on '{connection_name}' answers once - it is not a stream")
        entity = " ".join(str(content.get("entity") or connection_name).split())
        recorded = 0
        frames: list = []

        def arrive(item):
            nonlocal recorded
            if item.payload is not None:
                frames.append(item)
            if item.data is not None:
                recorded += self._ecosystem.observe(observations_from(
                    item.data, entity=entity, source=f"listen:{connection_name}/{operation}",
                    observed_at=item.received_at,
                ))
            return True

        result = connection.stream(
            operation, max_items=int(content.get("max_items") or 1000),
            max_seconds=float(content.get("seconds") or 10), on_item=arrive,
            **dict(content.get("params") or {}),
        )
        kinds: dict[str, int] = {}
        for item in result.items:
            kinds[item.content_type or ("json" if item.data is not None else "text")] = kinds.get(
                item.content_type or ("json" if item.data is not None else "text"), 0) + 1
        return {
            "ok": result.ok, "items": len(result.items), "recorded": recorded, "frames": len(frames),
            "content_types": kinds, "ended_because": result.ended_because, "detail": result.detail,
            "entity": entity,
        }

    def _sample(self, content: dict) -> dict:
        wanted = str(content.get("watch_id") or "").strip()
        registry = self._ecosystem.watches
        watches = [registry.get(wanted)] if wanted else registry.all()
        if wanted and watches[0] is None:
            raise ValueError(f"no watch called '{wanted}'")
        outcomes = []
        now = self._clock()
        for watch in watches:
            if not watch.enabled and not wanted:
                continue
            connection = self._connections.get(watch.connection) if self._connections is not None else None
            if connection is not None and connection.name != watch.connection:
                connection = None
            outcome = sample(watch, connection, self._ecosystem.observe, now=now)
            registry.note(outcome, now)
            # The same hand-over the tick performs - a watch's frames reach
            # what it declared (perception) however the read was triggered.
            from src.services.automation.tick import hand_over_frames

            handed = hand_over_frames(watch, self._ecosystem, list(outcome.frames))
            outcomes.append({"watch_id": outcome.watch_id, "ok": outcome.ok, "recorded": outcome.recorded,
                             "detail": outcome.detail + (f"; {handed}" if handed else ""),
                             "frames": len(outcome.frames)})
        return {"outcomes": outcomes}
