"""The governed boundary to live data-bound views (GM-06): list them, declare
one, add or take away widgets, remove one, and resolve one now.

A view only ever reads. What it may show is capped by its sensitivity ceiling,
which can never be set above the ceiling of whoever declares it - a
specialist cannot make a view that shows what it could not read itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec
from src.core.sensitivity import normalise_level, rank
from src.services.views.live_views import LiveView, new_view_id, resolve_view, widget_kinds, widget_label

LIVE_VIEW_TOOL = "live_view"
_OPERATIONS = ("views", "kinds", "declare", "add", "remove_widgets", "remove", "resolve")


class LiveViewTool(Tool):
    def __init__(self, ecosystem: Any, connections: Any, *, owner_agent: str, max_sensitivity: str = "personal") -> None:
        self._ecosystem = ecosystem
        self._connections = connections
        self._owner_agent = owner_agent
        self._ceiling = max_sensitivity

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=LIVE_VIEW_TOOL,
            description=("Live views (dashboards): widgets bound to observed readings, their history, groups, "
                         "events or what a camera saw, redrawn as the data changes. Views only ever read."),
            schema={"operation": str},
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        content = json.loads(raw) if raw else {}
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown live_view operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        views = self._ecosystem.views
        if operation == "views":
            return json.dumps({"views": [{**v.to_dict(), "text": v.describe(), "path": f"live/{v.view_id}"}
                                         for v in views.all()]})
        if operation == "kinds":
            return json.dumps({"kinds": [{"name": k.name, "needs": list(k.needs), "describes": k.describes}
                                         for k in widget_kinds()]})
        if operation == "declare":
            return json.dumps(self._declare(content))
        view = views.get(str(content.get("view") or ""))
        if view is None:
            raise ValueError(f"there is no live view called '{content.get('view')}'")
        if operation == "add":
            updated = views.declare(LiveView(view.view_id, view.title, view.widgets + tuple(content.get("widgets") or ()),
                                             view.refresh_seconds, view.max_sensitivity, view.declared_by, view.declared_at))
            return json.dumps({"view": updated.to_dict(), "text": updated.describe()})
        if operation == "remove_widgets":
            drop = {str(n).strip().lower() for n in content.get("labels") or ()}
            kept = tuple(w for w in view.widgets if widget_label(w).lower() not in drop
                         and not any(d in widget_label(w).lower() for d in drop))
            updated = views.declare(LiveView(view.view_id, view.title, kept, view.refresh_seconds, view.max_sensitivity,
                                             view.declared_by, view.declared_at))
            return json.dumps({"view": updated.to_dict(), "text": updated.describe(),
                               "removed": len(view.widgets) - len(kept)})
        if operation == "remove":
            return json.dumps({"removed": views.remove(view.view_id)})
        return json.dumps(resolve_view(view, self._ecosystem, self._connections))

    def _declare(self, content: dict) -> dict:
        title = " ".join(str(content.get("title") or "").split())
        if not title:
            raise ValueError("a view needs a title")
        level = normalise_level(content.get("max_sensitivity") or "personal") or "ordinary"
        if rank(level) > rank(self._ceiling):
            raise ValueError(f"a view I declare can show up to '{self._ceiling}' - not '{level}'")
        existing = self._ecosystem.views.get(str(content.get("view_id") or title))
        view = self._ecosystem.views.declare(LiveView(
            view_id=str(content.get("view_id") or (existing.view_id if existing else new_view_id(title))),
            title=title, widgets=tuple(content.get("widgets") or ()),
            refresh_seconds=int(content.get("refresh_seconds") or 10), max_sensitivity=level,
            declared_by=str(content.get("declared_by") or self._owner_agent),
            declared_at=datetime.now(timezone.utc).isoformat(),
        ))
        return {"view": view.to_dict(), "text": view.describe(), "path": f"live/{view.view_id}",
                "replaced": existing is not None}
