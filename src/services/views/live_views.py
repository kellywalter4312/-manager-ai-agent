"""Live data-bound interfaces (GM-06): a declared view whose widgets are bound
to what the ecosystem knows - an observed reading, its history (optionally
through a series technique, GM-09), a group of things across connections
(GM-15), the events conditions raised (GM-02), what a camera watch last saw
(GM-03) or a safe read of a connection itself - and that updates as the data
does.

**A view is data.** An id, a title, how often it refreshes, and its widgets;
each widget names a kind and what it is bound to. Nothing here draws: a view
is *resolved* to plain JSON (``resolve_view``) and every surface draws that
JSON with the one widget renderer (``ui/live_view.js``) - the workspace panel
``live:<view>``, the served page ``/live/<view>``, or anything built later.

**Widget kinds are an open registry** (``register_widget_kind``): the six
here are rows, and a new kind of binding is one more row, never a new page.

**Reading, never acting.** Resolving a view only reads; a ``reading`` widget
may read a connection live, but only an operation the connection says is
safe, and never more often than its own minimum interval. What a view may
show is capped by its sensitivity ceiling (default "personal"): an
observation above it is not shown, and the widget says so.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.core.sensitivity import normalise_level
from src.services.declared_store import DeclaredDocument, slug

DEFAULT_REFRESH_SECONDS = 10
MIN_REFRESH_SECONDS = 2
MAX_POINTS = 240
MIN_LIVE_READ_SECONDS = 5.0


@dataclass(frozen=True)
class WidgetKind:
    name: str
    needs: "tuple[str, ...]"
    resolve: "Callable[[Mapping[str, Any], 'ViewContext'], dict]" = field(repr=False, compare=False)
    describes: str = ""


_KINDS: "dict[str, WidgetKind]" = {}


def register_widget_kind(kind: WidgetKind) -> None:
    _KINDS[kind.name] = kind


def widget_kinds() -> "tuple[WidgetKind, ...]":
    return tuple(_KINDS.values())


@dataclass(frozen=True)
class LiveView:
    view_id: str
    title: str
    widgets: "tuple[Mapping[str, Any], ...]"
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    max_sensitivity: str = "personal"
    declared_by: str = ""
    declared_at: str = ""

    def __post_init__(self) -> None:
        if not slug(self.view_id):
            raise ValueError("a view needs an id")
        if not " ".join(str(self.title or "").split()):
            raise ValueError("a view needs a title")
        widgets = tuple(dict(w) for w in self.widgets)
        for widget in widgets:
            kind = _KINDS.get(str(widget.get("kind") or ""))
            if kind is None:
                raise ValueError(f"'{widget.get('kind')}' is not a widget kind (known: {', '.join(sorted(_KINDS))})")
            missing = [n for n in kind.needs if not str(widget.get(n) or "").strip()]
            if missing:
                raise ValueError(f"a '{kind.name}' widget needs {', '.join(missing)}")
        object.__setattr__(self, "view_id", slug(self.view_id))
        object.__setattr__(self, "widgets", widgets)
        object.__setattr__(self, "refresh_seconds", max(MIN_REFRESH_SECONDS, int(self.refresh_seconds)))
        object.__setattr__(self, "max_sensitivity", normalise_level(self.max_sensitivity) or "ordinary")

    def to_dict(self) -> dict:
        return {"view_id": self.view_id, "title": self.title, "widgets": [dict(w) for w in self.widgets],
                "refresh_seconds": self.refresh_seconds, "max_sensitivity": self.max_sensitivity,
                "declared_by": self.declared_by, "declared_at": self.declared_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveView":
        return cls(view_id=str(data["view_id"]), title=str(data.get("title") or data["view_id"]),
                   widgets=tuple(data.get("widgets") or ()),
                   refresh_seconds=int(data.get("refresh_seconds") or DEFAULT_REFRESH_SECONDS),
                   max_sensitivity=str(data.get("max_sensitivity") or "personal"),
                   declared_by=str(data.get("declared_by") or ""), declared_at=str(data.get("declared_at") or ""))

    def describe(self) -> str:
        parts = ", ".join(widget_label(w) for w in self.widgets) or "nothing yet"
        return f"{self.view_id}: '{self.title}' - {parts} (refreshes every {self.refresh_seconds}s)"


def widget_label(widget: Mapping[str, Any]) -> str:
    if widget.get("label"):
        return str(widget["label"])
    kind = widget.get("kind")
    if kind in ("value", "series"):
        base = f"{widget.get('entity')} {str(widget.get('attribute') or '').replace('_', ' ')}".strip()
        return base + (f" ({widget['technique']})" if widget.get("technique") else "") + (" history" if kind == "series" else "")
    if kind == "group":
        return f"the {widget.get('group')} group"
    if kind == "look":
        return f"what {widget.get('watch')} sees"
    if kind == "reading":
        return f"{widget.get('connection')}/{widget.get('operation')} now"
    return str(kind)


class LiveViewRegistry:
    def __init__(self, root: "Path | str") -> None:
        self._document = DeclaredDocument(Path(root) / "views.json", key="views")

    def all(self) -> "list[LiveView]":
        found = []
        for item in self._document.load():
            try:
                found.append(LiveView.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue
        return found

    def get(self, view_id: str) -> "LiveView | None":
        wanted = slug(view_id)
        return next((v for v in self.all() if v.view_id == wanted or slug(v.title) == wanted), None)

    def declare(self, view: LiveView) -> LiveView:
        def change(items: list) -> LiveView:
            items[:] = [i for i in items if slug(i.get("view_id", "")) != view.view_id]
            items.append(view.to_dict())
            return view
        return self._document.update(change)

    def remove(self, view_id: str) -> bool:
        wanted = slug(view_id)

        def change(items: list) -> bool:
            before = len(items)
            items[:] = [i for i in items if slug(i.get("view_id", "")) != wanted and slug(i.get("title", "")) != wanted]
            return len(items) < before
        return self._document.update(change)


def new_view_id(title: str) -> str:
    return slug(title)[:40] or f"view_{uuid.uuid4().hex[:6]}"


# -- resolution -------------------------------------------------------------------

@dataclass
class ViewContext:
    ecosystem: Any
    connections: Any
    max_sensitivity: str
    now: datetime


_LIVE_READS: "dict[tuple, tuple[float, dict]]" = {}
_LIVE_LOCK = threading.Lock()


def _hidden(level: str, ceiling: str) -> bool:
    from src.core.sensitivity import rank

    return bool(level) and rank(level) > rank(ceiling)


def _value(widget, ctx: ViewContext) -> dict:
    store = ctx.ecosystem.observations
    entity = store.resolve_entity(str(widget["entity"]))
    if entity is None:
        return {"state": "empty", "note": f"nothing observed of '{widget['entity']}' yet"}
    series = store.series(entity, str(widget["attribute"]), limit=1)
    exact = [o for o in series if o.attribute == str(widget["attribute"])] or series
    if not exact:
        return {"state": "empty", "note": f"'{entity}' has no '{widget['attribute']}' reading yet"}
    latest = exact[-1]
    if _hidden(latest.sensitivity, ctx.max_sensitivity):
        return {"state": "withheld", "note": f"marked {latest.sensitivity} - not shown on this view"}
    age = (ctx.now - latest.observed_at).total_seconds()
    return {"state": "ok", "value": latest.value, "unit": latest.unit, "at": latest.observed_at.isoformat(),
            "age_seconds": round(age, 1)}


def _thin(points: list) -> list:
    if len(points) <= MAX_POINTS:
        return points
    step = len(points) / MAX_POINTS
    return [points[int(i * step)] for i in range(MAX_POINTS - 1)] + [points[-1]]


def _series(widget, ctx: ViewContext) -> dict:
    from src.services.observations.store import is_number

    store = ctx.ecosystem.observations
    entity = store.resolve_entity(str(widget["entity"]))
    if entity is None:
        return {"state": "empty", "note": f"nothing observed of '{widget['entity']}' yet"}
    window = float(widget.get("window_seconds") or 86400)
    found = store.series(entity, str(widget["attribute"]), since=ctx.now - timedelta(seconds=window), until=ctx.now,
                         limit=5000, max_sensitivity=ctx.max_sensitivity)
    readings = [(o.observed_at, o.value) for o in found if o.attribute == str(widget["attribute"]) and is_number(o.value)]
    unit = next((o.unit for o in reversed(found) if o.unit), "")
    result: dict = {"state": "ok" if readings else "empty", "unit": unit, "window_seconds": window}
    markers: list = []
    technique_id = str(widget.get("technique") or "")
    if technique_id and readings:
        from src.services.observations.techniques import current_techniques

        technique = next((t for t in current_techniques() if t.technique_id == technique_id), None)
        if technique is None:
            result["note"] = f"the '{technique_id}' technique is not available now"
        elif technique.inputs != 1:
            result["note"] = f"'{technique_id}' relates two series - it cannot shape one widget"
        else:
            try:
                outcome = technique.apply([readings])
            except ValueError as exc:
                result["note"] = str(exc)
            else:
                if outcome.derived:
                    readings = list(outcome.derived)
                markers = [{"at": f["at"], "value": f["value"], "what": f["what"]} for f in outcome.findings][:50]
                result["technique"] = {"id": technique.technique_id, "label": technique.label, "detail": outcome.detail}
    points = _thin([[t.isoformat(), float(v)] for t, v in readings])
    if points:
        values = [p[1] for p in points]
        result.update(points=points, min=min(values), max=max(values), last=values[-1], count=len(readings))
    result["markers"] = markers
    if not readings and "note" not in result:
        result["note"] = f"no numeric '{widget['attribute']}' readings in this window"
    return result


def _group(widget, ctx: ViewContext) -> dict:
    import json

    from src.agents.tools.groups import GroupTool

    tool = GroupTool(ctx.ecosystem, ctx.connections, owner_agent="view", max_sensitivity=ctx.max_sensitivity)
    try:
        state = json.loads(tool.run(operation="state", content=json.dumps({"name": str(widget["group"])})))
    except ValueError as exc:
        return {"state": "empty", "note": str(exc)}
    return {"state": "ok", "members": state["members"], "aggregates": state["aggregates"],
            "missing": state["group"].get("missing") or []}


def _events(widget, ctx: ViewContext) -> dict:
    limit = max(1, min(50, int(widget.get("limit") or 8)))
    events = ctx.ecosystem.events.all()[-limit:]
    return {"state": "ok" if events else "empty", "events": [
        {"at": e.fired_at, "text": e.detail, "label": e.label, "status": e.status} for e in reversed(events)
    ], **({} if events else {"note": "no condition has fired yet"})}


def _look(widget, ctx: ViewContext) -> dict:
    from src.services.perception.frames import look_path

    watch = ctx.ecosystem.watches.get(str(widget["watch"]))
    if watch is None:
        return {"state": "empty", "note": f"there is no watch called '{widget['watch']}'"}
    path = look_path(ctx.ecosystem, watch.watch_id)
    result: dict = {"state": "empty", "entity": watch.entity}
    if path.is_file():
        result.update(state="ok", image=f"live/look/{watch.watch_id}.png?v={int(path.stat().st_mtime)}",
                      seen_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
    else:
        result["note"] = "nothing seen yet - the watch hands its frames to perception when it next reads"
    latest = ctx.ecosystem.observations.latest(watch.entity, max_sensitivity=ctx.max_sensitivity)
    if "in_view" in latest:
        result["in_view"] = latest["in_view"].value
    return result


def _reading(widget, ctx: ViewContext) -> dict:
    from src.services.observations.extract import flatten

    name, operation = str(widget["connection"]), str(widget["operation"])
    connection = ctx.connections.get(name) if ctx.connections is not None else None
    if connection is None or connection.name != name:
        return {"state": "empty", "note": f"'{name}' is not a connection this system has now"}
    if connection.requires_confirmation_for(operation) or operation not in connection.supported_operations:
        return {"state": "empty", "note": f"'{operation}' on '{name}' is not a safe read, so a view never runs it"}
    key = (name, operation)
    with _LIVE_LOCK:
        cached = _LIVE_READS.get(key)
    if cached and time.monotonic() - cached[0] < MIN_LIVE_READ_SECONDS:
        return cached[1]
    result = connection.execute(operation)
    if not result.ok:
        answer = {"state": "error", "note": result.detail or result.status.value}
    elif result.data is not None:
        answer = {"state": "ok", "fields": dict(flatten(result.data)[:24]),
                  "at": (result.executed_at or ctx.now).isoformat()}
    else:
        answer = {"state": "ok", "text": (result.detail or "")[:400], "at": (result.executed_at or ctx.now).isoformat()}
    with _LIVE_LOCK:
        _LIVE_READS[key] = (time.monotonic(), answer)
    return answer


for _kind in (
    WidgetKind("value", ("entity", "attribute"), _value, "the latest observed value of one reading"),
    WidgetKind("series", ("entity", "attribute"), _series,
               "one reading's history over a window, optionally through a series technique"),
    WidgetKind("group", ("group",), _group, "every member of a group at once, with group-wide aggregates"),
    WidgetKind("events", (), _events, "what declared conditions raised, newest first"),
    WidgetKind("look", ("watch",), _look, "the latest picture a camera watch's perception saw, and the count"),
    WidgetKind("reading", ("connection", "operation"), _reading, "a safe read of a connection, live"),
):
    register_widget_kind(_kind)


def resolve_view(view: LiveView, ecosystem: Any, connections: Any, *, now: "datetime | None" = None) -> dict:
    """The view as plain JSON, every widget resolved now. A widget that
    fails says why; it never stops the others."""
    ctx = ViewContext(ecosystem, connections, view.max_sensitivity, now or datetime.now(timezone.utc))
    widgets = []
    for widget in view.widgets:
        kind = _KINDS.get(str(widget.get("kind")))
        try:
            data = kind.resolve(widget, ctx) if kind is not None else {"state": "error", "note": "unknown widget kind"}
        except Exception as exc:  # noqa: BLE001 - one broken binding never blanks the view
            data = {"state": "error", "note": f"{type(exc).__name__}: {exc}"}
        widgets.append({"kind": widget.get("kind"), "label": widget_label(widget), **data})
    return {"view_id": view.view_id, "title": view.title, "refresh_seconds": view.refresh_seconds,
            "resolved_at": ctx.now.isoformat(), "widgets": widgets}
