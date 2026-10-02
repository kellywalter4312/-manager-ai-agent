"""GM-15: groups of things that belong together across independent
connections - declared, nested, read at once with group-wide aggregates,
tested by conditions over their members, and acted on through one
confirmation that lists every member's own step. The subjects (a cold room,
a loading bay) appear nowhere in src/."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.contracts import Task
from src.manager.activity_session import PendingConfirmation
from src.manager.ecosystem_capabilities import bind_ecosystem, entity_groups_intent
from src.services.automation.conditions import Condition
from src.services.connections.groups import Group, GroupRegistry, Member
from src.services.ecosystem_state import build_ecosystem_state
from src.services.observations.store import Observation
from src.services.observations.watches import Watch
from tests.gm_support import Device, System, allow_loopback, isolate_owner_state, settings_for  # noqa: F401

T0 = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


class TestTheRegistry:
    def test_members_are_references_and_nest(self, tmp_path):
        groups = GroupRegistry(tmp_path)
        groups.declare(Group("cold room", (Member("connection", "chiller"), Member("connection", "door_sensor"))))
        groups.declare(Group("site", (Member("group", "cold room"), Member("entity", "yard thermometer"))))
        assert [m.name for m in groups.resolve("site")] == ["chiller", "door_sensor", "yard thermometer"]
        assert groups.member_connections("site") == ["chiller", "door_sensor"]
        assert GroupRegistry(tmp_path).get("Cold Room").name == "cold room"   # persisted, found by any spelling

    def test_a_cycle_is_refused_and_the_group_kept_as_it_was(self, tmp_path):
        groups = GroupRegistry(tmp_path)
        groups.declare(Group("a", (Member("connection", "x"),)))
        groups.declare(Group("b", (Member("group", "a"),)))
        with pytest.raises(ValueError, match="circle"):
            groups.add_members("a", [Member("group", "b")])
        assert [m.name for m in groups.get("a").members] == ["x"]
        with pytest.raises(ValueError, match="cannot contain itself"):
            groups.declare(Group("c", (Member("group", "c"),)))

    def test_shrinking_and_removing(self, tmp_path):
        groups = GroupRegistry(tmp_path)
        groups.declare(Group("bay", (Member("connection", "door"), Member("connection", "light"))))
        assert [m.name for m in groups.remove_members("bay", ["door"]).members] == ["light"]
        assert groups.remove("bay") and groups.get("bay") is None

    def test_a_member_must_be_a_known_kind(self):
        with pytest.raises(ValueError):
            Member("device", "x")


class TestGroupsInTheEcosystem:
    def test_connection_members_stand_for_what_their_watches_keep(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.watches.declare(Watch("w1", "chiller", "status", "chiller unit"))
        ecosystem.groups.declare(Group("cold room", (Member("connection", "chiller"), Member("connection", "door_sensor"),
                                                     Member("entity", "probe 3"))))
        assert ecosystem.group_entities("cold room") == ["chiller unit", "door_sensor", "probe 3"]
        assert ecosystem.group_entities("not a group") == []

    @pytest.mark.parametrize("quantifier,expected", [("any", True), ("all", False)])
    def test_a_condition_on_a_group_is_tested_on_every_member(self, tmp_path, quantifier, expected):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.groups.declare(Group("cold room", (Member("entity", "probe 1"), Member("entity", "probe 2"))))
        ecosystem.observations.record([Observation("probe 1", "temp_c", 9.5, observed_at=T0),
                                       Observation("probe 2", "temp_c", 3.0, observed_at=T0)])
        condition = ecosystem.conditions.declare(Condition(
            condition_id="c1", entity="cold room", attribute="temp_c", operator="above", threshold=8,
            quantifier=quantifier))
        event = ecosystem.engine.evaluate(condition, now=T0 + timedelta(seconds=1))
        assert (event is not None) is expected
        if event:
            assert event.detail.startswith("probe 1")


class TestRecognition:
    @pytest.fixture
    def bound(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.groups.declare(Group("cold room", (Member("connection", "chiller"),)))
        bind_ecosystem(ecosystem)
        yield
        bind_ecosystem(None)

    @pytest.mark.parametrize("text", [
        "group the chiller and the door sensor as the cold room", "make a group called bay with the door",
        "what groups do I have?", "how is the cold room doing?", "switch off everything in the cold room",
        "remove the chiller from the cold room group", "delete the cold room group",
    ])
    def test_group_asks(self, bound, text):
        assert entity_groups_intent(Task(description=text))

    @pytest.mark.parametrize("text", [
        "what groups of animals live in the arctic?", "the study group meets on friday",
        "the cold room is where we keep milk",
    ])
    def test_not_group_asks(self, bound, text):
        assert not entity_groups_intent(Task(description=text))


class TestTheOneConfirmationGate:
    def test_steps_need_their_group(self):
        with pytest.raises(ValueError, match="must name its group"):
            PendingConfirmation(operation="x", description="d", steps=({"connection": "a", "operation": "b"},))
        pending = PendingConfirmation(operation="act", description="d", group="bay",
                                      steps=({"connection": "a", "operation": "off", "params": {}},))
        assert pending.group == "bay" and not pending.connection


class TestThroughTheRealSystem:
    def test_declare_read_condition_act_and_reload(self, tmp_path, allow_loopback):
        chiller = Device(get={"/status": lambda d, p: {"temp_c": d.state.get("temp", 4.0), "compressor": d.state.get("c", "on")}},
                         post={"/stop": lambda d, p: d.state.__setitem__("c", "off") or {"compressor": "off"}})
        fans = Device(get={"/fans": lambda d, p: {"temp_c": d.state.get("temp", 6.0), "speed": d.state.get("s", 2)}},
                      post={"/stop": lambda d, p: d.state.__setitem__("s", 0) or {"speed": 0}})
        try:
            system = System(tmp_path)
            system.cc(f"services chiller declare {chiller.url} status:GET:/status:open,stop:POST:/stop:open")
            system.cc(f"services air_fans declare {fans.url} fans:GET:/fans:open,stop:POST:/stop:open")
            system.cc("observe chiller status every 30s")
            system.cc("observe air_fans fans every 30s")

            turn = system.say("group the chiller and the air fans as the cold room")
            assert turn.capability == "entity_groups" and turn.outcome.startswith("Group set"), turn.outcome
            state = system.say("how is the cold room doing?")
            assert "Across the group" in state.outcome and "mean 5.0" in state.outcome, state.outcome

            turn = system.say("tell me when the temperature of anything in the cold room goes above 8")
            assert turn.capability == "condition_watch" and "Condition set" in turn.outcome, turn.outcome
            fans.state["temp"] = 9.2
            system.cc("observe sample")
            assert any("air_fans" in e.detail for e in system.ecosystem.events.all())

            ask = system.say("stop everything in the cold room")
            assert ask.capability == "entity_groups" and "Proceed" in ask.outcome, ask.outcome
            assert "'stop' on 'air_fans'" in ask.outcome and "'stop' on 'chiller'" in ask.outcome
            assert chiller.state.get("c", "on") == "on" and fans.state.get("s", 2) == 2
            done = system.say("yes")
            assert done.capability == "entity_groups" and "2 of 2 done" in done.outcome, done.outcome
            assert chiller.state["c"] == "off" and fans.state["s"] == 0
            assert ("entity_groups", True) in system.audit()

            assert system.cc("groups add bay entity:dock door,connection:chiller").startswith("Group set")
            assert ("admin.groups", True) in system.audit()
            system.rebuild()
            assert set(system.ecosystem.group_entities("cold room")) == {"chiller", "air_fans"}
            listed = system.cc("groups")
            assert "cold room: " in listed and "bay: " in listed
            system.cc("services air_fans forget")
            assert "not found now: air_fans" in system.cc("groups")
        finally:
            chiller.close()
            fans.close()
