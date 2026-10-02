"""GM-06: live data-bound views - declared views whose widgets are bound to
observed readings, their history (through a series technique), groups,
events, a camera's latest look or a safe live read, resolved to one JSON
every surface draws with one renderer, served at /live/<view> and as the
workspace panel live:<view>. The subjects (a kiln, a cold store) appear
nowhere in src/."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.agents.tools.live_view import LiveViewTool
from src.control_center.workspace_catalog import WorkspaceType
from src.services.connections.groups import Group, Member
from src.services.ecosystem_state import build_ecosystem_state
from src.services.observations.store import Observation
from src.services.views.live_views import LiveView, register_widget_kind, resolve_view, widget_kinds, WidgetKind
from tests.gm_support import Device, System, allow_loopback, isolate_owner_state, settings_for  # noqa: F401

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def ecosystem(tmp_path):
    eco = build_ecosystem_state(settings_for(tmp_path))
    start = _now() - timedelta(hours=2)
    eco.observe([Observation("kiln", "temp_c", 800 + 3 * i, observed_at=start + timedelta(minutes=2 * i), unit="C")
                 for i in range(60)])
    return eco


class TestTheView:
    def test_widget_kinds_are_an_open_registry(self):
        names = {k.name for k in widget_kinds()}
        assert {"value", "series", "group", "events", "look", "reading"} <= names
        register_widget_kind(WidgetKind("constant", (), lambda w, ctx: {"state": "ok", "value": 42}))
        try:
            view = LiveView("t", "T", ({"kind": "constant"},))
            assert resolve_view(view, None, None)["widgets"][0]["value"] == 42
        finally:
            from src.services.views import live_views
            live_views._KINDS.pop("constant", None)

    def test_a_view_is_validated(self):
        with pytest.raises(ValueError, match="not a widget kind"):
            LiveView("v", "V", ({"kind": "hologram"},))
        with pytest.raises(ValueError, match="needs entity"):
            LiveView("v", "V", ({"kind": "value", "attribute": "x"},))
        assert LiveView("v", "V", (), refresh_seconds=0).refresh_seconds == 2

    def test_values_histories_and_techniques_resolve(self, ecosystem):
        view = LiveView("kiln", "Kiln", (
            {"kind": "value", "entity": "kiln", "attribute": "temp_c"},
            {"kind": "series", "entity": "kiln", "attribute": "temp_c", "window_seconds": 3600, "technique": "smooth"},
            {"kind": "series", "entity": "nothing", "attribute": "x"},
            {"kind": "events"},
        ))
        resolved = resolve_view(view, ecosystem, None)
        value, series, missing, events = resolved["widgets"]
        assert value["value"] == 977 and value["unit"] == "C"
        # A reading exactly one hour old sits on the window's edge.
        assert series["technique"]["id"] == "smooth" and series["count"] in (29, 30) and len(series["points"]) == series["count"]
        assert missing["state"] == "empty" and "nothing observed" in missing["note"]
        assert events["state"] == "empty"

    def test_a_group_widget_aggregates_its_members(self, ecosystem):
        ecosystem.observe([Observation("probe b", "temp_c", 1000.0, observed_at=_now())])
        ecosystem.groups.declare(Group("firing room", (Member("entity", "kiln"), Member("entity", "probe b"))))
        widget = resolve_view(LiveView("g", "G", ({"kind": "group", "group": "firing room"},)), ecosystem, None)["widgets"][0]
        assert widget["aggregates"]["temp_c"]["max"] == 1000.0 and len(widget["members"]) == 2

    def test_what_is_above_the_ceiling_is_withheld(self, ecosystem):
        ecosystem.observe([Observation("clinic scale", "weight_kg", 71.2, observed_at=_now(), sensitivity="sensitive")])
        widget = {"kind": "value", "entity": "clinic scale", "attribute": "weight_kg"}
        assert resolve_view(LiveView("p", "P", (widget,)), ecosystem, None)["widgets"][0]["state"] == "withheld"
        allowed = LiveView("s", "S", (widget,), max_sensitivity="sensitive")
        assert resolve_view(allowed, ecosystem, None)["widgets"][0]["value"] == 71.2

    def test_one_broken_widget_never_blanks_the_view(self, ecosystem):
        register_widget_kind(WidgetKind("broken", (), lambda w, ctx: 1 / 0))
        try:
            resolved = resolve_view(LiveView("b", "B", ({"kind": "broken"}, {"kind": "events"})), ecosystem, None)
            assert resolved["widgets"][0]["state"] == "error" and "ZeroDivisionError" in resolved["widgets"][0]["note"]
            assert resolved["widgets"][1]["state"] == "empty"
        finally:
            from src.services.views import live_views
            live_views._KINDS.pop("broken", None)


class TestTheTool:
    def test_declare_add_remove_and_the_ceiling(self, ecosystem):
        tool = LiveViewTool(ecosystem, None, owner_agent="Scribe", max_sensitivity="personal")
        declared = json.loads(tool.run(operation="declare", content=json.dumps(
            {"title": "Kiln room", "widgets": [{"kind": "value", "entity": "kiln", "attribute": "temp_c"}]})))
        assert declared["path"] == "live/kiln_room" and declared["view"]["declared_by"] == "Scribe"
        added = json.loads(tool.run(operation="add", content=json.dumps({"view": "kiln room", "widgets": [{"kind": "events"}]})))
        assert len(added["view"]["widgets"]) == 2
        with pytest.raises(ValueError, match="up to 'personal'"):
            tool.run(operation="declare", content=json.dumps({"title": "x", "widgets": [], "max_sensitivity": "sensitive"}))
        assert json.loads(tool.run(operation="remove", content=json.dumps({"view": "kiln_room"})))["removed"]


class TestTheSurfaces:
    def test_the_panel_kind_and_the_one_renderer(self):
        assert WorkspaceType("monitor", "Monitor", "", ("live:kiln_room",)).panels == ("live:kiln_room",)
        with pytest.raises(ValueError):
            WorkspaceType("monitor", "Monitor", "", ("live",))
        page = (REPO / "ui" / "index.html").read_text(encoding="utf-8")
        assert 'if(kind === "live")' in page and 'script.src = "live.js"' in page
        renderer = (REPO / "ui" / "live_view.js").read_text(encoding="utf-8")
        assert "window.AlfredLive" in renderer and "fetch(`${base}live/" in renderer

    def test_routes_serve_json_page_renderer_and_index(self, tmp_path):
        from scripts.serve_ui import LiveSystem, route

        system = System(tmp_path)
        system.ecosystem.observe([Observation("cold store", "temp_c", -18.5, observed_at=_now(), unit="C")])
        assert system.cc("views declare cold Cold_store value:cold_store/temp_c events every=3").startswith("View set:")
        live = LiveSystem(runtime=system.runtime, manager=system.manager, channel=system.channel,
                          conversation=system.conversation, centre=system.centre, lock=threading.Lock())
        status, body, ctype = route("/live/cold.json", live)
        assert status == 200 and json.loads(body)["widgets"][0]["value"] == -18.5 and json.loads(body)["refresh_seconds"] == 3
        status, body, ctype = route("/live/cold", live)
        assert status == 200 and b'data-live-view="cold"' in body and b"../live.js" in body
        status, body, ctype = route("/live.js", live)
        assert status == 200 and ctype.startswith("text/javascript")
        assert b"Cold store" in route("/live/", live)[1]
        assert route("/live/unknown.json", live)[0] == 404
        assert route("/live/look/nothing.png", live)[0] == 404


class TestThroughTheRealSystem:
    def test_declared_in_conversation_governed_in_the_control_center(self, tmp_path, allow_loopback):
        kiln = Device(get={"/status": lambda d, p: {"temp_c": d.state.get("temp", 900.0)}})
        try:
            system = System(tmp_path)
            system.cc(f"services kiln declare {kiln.url} status:GET:/status:open")
            system.cc("observe kiln status every 30s")
            turn = system.say("make me a dashboard called Firing with the kiln temperature and any alerts")
            assert turn.capability == "live_view" and "live/firing" in turn.outcome, turn.outcome
            view = system.ecosystem.views.get("firing")
            assert {w["kind"] for w in view.widgets} == {"series", "events"}
            kiln.state["temp"] = 1040.0
            system.cc("observe sample")
            assert "last 1040" in system.cc("views firing")
            assert system.say("what dashboards do I have?").capability == "live_view"
            system.rebuild()
            assert system.ecosystem.views.get("firing") is not None
            assert system.cc("views remove firing").startswith("Removed the view")
            assert ("admin.views", True) in system.audit()
            assert ("live_view", True) in system.audit()
        finally:
            kiln.close()
