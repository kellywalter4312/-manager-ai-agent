"""D-2 + GM-02: the proactive pipeline's sources are an open registry, and a
declared condition over observations raises events that notify or propose -
never act alone. Subjects (a tide gauge, a harbour gate, a greenhouse vent)
appear nowhere in src/."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from src.agents.tools.automation import AutomationTool
from src.core.contracts import Task
from src.manager.ecosystem_capabilities import bind_ecosystem, condition_watch_intent, ecosystem_events_intent
from src.manager.proactive_milestones import (
    ProactiveEvent,
    due_proactive_events,
    proactive_sources,
    register_proactive_source,
)
from src.services.automation.conditions import (
    Condition,
    ConditionOperator,
    ConditionEngine,
    ConditionRegistry,
    EventLog,
    ProposedAction,
    operator_named,
    parse_threshold,
    register_operator,
)
from src.services.automation.tick import BackgroundTicker, register_tick_step, run_ecosystem_tick, tick_steps
from src.services.ecosystem_state import build_ecosystem_state
from src.services.observations.store import Observation, ObservationStore
from tests.gm_support import Device, System, allow_loopback, isolate_owner_state, settings_for  # noqa: F401

T0 = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


def _engine(tmp_path, clock):
    store = ObservationStore(tmp_path)
    registry = ConditionRegistry(tmp_path)
    events = EventLog(tmp_path)
    return store, registry, events, ConditionEngine(store, registry, events, clock=clock)


class _Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


# -- D-2 -----------------------------------------------------------------------

class _Manager:
    memory = None
    own_skills = None


class TestTheProactiveSourcesAreARegistry:
    def test_the_original_five_are_registered_in_their_original_order(self):
        assert proactive_sources()[:5] == ("personal", "temporal", "calendar", "calendar_item", "reminder")

    def test_a_new_source_joins_every_caller_and_a_failing_one_costs_only_itself(self):
        register_proactive_source("gm02_probe", lambda ctx: [ProactiveEvent("probe", "a probe", "probe:1", "gm02_probe")])
        register_proactive_source("gm02_broken", lambda ctx: 1 / 0)
        try:
            events = due_proactive_events(_Manager(), today=date(2026, 10, 1))
            assert [e.source for e in events if e.source.startswith("gm02")] == ["gm02_probe"]
        finally:
            from src.manager import proactive_milestones

            proactive_milestones._SOURCES.pop("gm02_probe", None)
            proactive_milestones._SOURCES.pop("gm02_broken", None)

    def test_fired_conditions_are_a_source_only_when_settings_name_the_store(self, tmp_path):
        import src.manager.proactive_conditions  # noqa: F401

        settings = settings_for(tmp_path)
        store, registry, events, engine = _engine(tmp_path / "ecosystem", _Clock(T0))
        registry.declare(Condition("c", "vent", "angle", "above", 30))
        store.record([Observation("vent", "angle", 45, observed_at=T0)])
        engine.evaluate_all(T0)
        assert [e for e in due_proactive_events(_Manager(), today=date(2026, 10, 1)) if e.source == "condition"] == []
        with_settings = [e for e in due_proactive_events(_Manager(), today=date(2026, 10, 1), settings=settings)
                         if e.source == "condition"]
        assert len(with_settings) == 1 and "angle is 45" in with_settings[0].label


# -- the engine -------------------------------------------------------------------

class TestConditions:
    def test_edge_triggered_once_and_again_after_it_clears(self, tmp_path):
        clock = _Clock(T0)
        store, registry, events, engine = _engine(tmp_path, clock)
        registry.declare(Condition("level_high", "tide gauge", "level_m", "above", 3.5))
        fired = []
        for minute, level in enumerate((3.0, 3.8, 3.9, 3.2, 3.7)):
            clock.at = T0 + timedelta(minutes=minute)
            fired += engine.on_arrival(store.record([Observation("tide gauge", "level_m", level, observed_at=clock.at)]) and
                                       [Observation("tide gauge", "level_m", level, observed_at=clock.at)])
        assert [e.value for e in fired] == [3.8, 3.7]
        assert [e.value for e in events.all()] == [3.8, 3.7]

    def test_a_cooldown_holds_a_refire(self, tmp_path):
        clock = _Clock(T0)
        store, registry, events, engine = _engine(tmp_path, clock)
        registry.declare(Condition("c", "tank", "pressure", "above", 5, cooldown_seconds=600))
        for minute, value in enumerate((6, 4, 6)):
            clock.at = T0 + timedelta(minutes=minute)
            store.record([Observation("tank", "pressure", value, observed_at=clock.at)])
            engine.evaluate_all(clock.at)
        assert len(events.all()) == 1

    def test_a_window_aggregate(self, tmp_path):
        clock = _Clock(T0 + timedelta(minutes=10))
        store, registry, events, engine = _engine(tmp_path, clock)
        store.record(Observation("line", "throughput", v, observed_at=T0 + timedelta(minutes=i)) for i, v in enumerate((10, 12, 14, 30)))
        registry.declare(Condition("c", "line", "throughput", "above", 15, window_seconds=600, aggregate="mean"))
        assert engine.evaluate(registry.get("c")) is not None, "mean 16.5 over the window is above 15"

    def test_silence_is_noticed_by_evaluation_not_arrival(self, tmp_path):
        clock = _Clock(T0)
        store, registry, events, engine = _engine(tmp_path, clock)
        store.record([Observation("beacon", "rssi", -60, observed_at=T0)])
        registry.declare(Condition("quiet", "beacon", "rssi", "stale", 300))
        assert engine.evaluate_all(T0 + timedelta(minutes=2)) == []
        fired = engine.evaluate_all(T0 + timedelta(minutes=6))
        assert len(fired) == 1 and "no rssi for 360 s" in fired[0].detail

    def test_text_and_change_tests(self, tmp_path):
        clock = _Clock(T0)
        store, registry, events, engine = _engine(tmp_path, clock)
        registry.declare(Condition("door", "barn", "door", "equals", "open"))
        registry.declare(Condition("mode", "barn", "mode", "changes"))
        for i, (door, mode) in enumerate((("closed", "day"), ("open", "day"), ("open", "night"))):
            store.record([Observation("barn", "door", door, observed_at=T0 + timedelta(minutes=i)),
                          Observation("barn", "mode", mode, observed_at=T0 + timedelta(minutes=i))])
            engine.evaluate_all(T0 + timedelta(minutes=i))
        assert sorted(e.condition_id for e in events.all()) == ["door", "mode"]

    def test_operators_are_an_open_registry(self, tmp_path):
        register_operator(ConditionOperator("divisible_by", ("divisible by",), "number",
                                            lambda c, t, ctx: float(c) % float(t) == 0, "divisible by"))
        clock = _Clock(T0)
        store, registry, events, engine = _engine(tmp_path, clock)
        registry.declare(Condition("c", "counter", "n", "divisible_by", 7))
        store.record([Observation("counter", "n", 21, observed_at=T0)])
        assert len(engine.evaluate_all(T0)) == 1

    def test_a_declaration_is_validated(self):
        with pytest.raises(ValueError):
            Condition("c", "x", "y", "no_such_test", 1)
        with pytest.raises(ValueError):
            Condition("c", "x", "y", "above", "not a number")
        with pytest.raises(ValueError):
            Condition("c", "x", "y", "above", 1, response="propose")
        assert parse_threshold(operator_named("stale"), "for 2 minutes") == 120.0
        assert parse_threshold(operator_named("above"), "3.5 metres") == 3.5

    def test_subscribers_hear_each_event_and_the_log_is_bounded(self, tmp_path, monkeypatch):
        import src.services.automation.conditions as conditions

        monkeypatch.setattr(conditions, "MAX_EVENTS", 3)
        clock = _Clock(T0)
        store, registry, events, engine = _engine(tmp_path, clock)
        heard = []
        engine.subscribe(heard.append)
        registry.declare(Condition("c", "x", "y", "above", 0))
        for i in range(5):
            clock.at = T0 + timedelta(minutes=i)
            store.record([Observation("x", "y", -1 if i % 2 else 1, observed_at=clock.at)])
            engine.evaluate_all(clock.at)
        assert len(heard) == 3 and len(events.all()) == 3


# -- the tool ---------------------------------------------------------------------

class _Gate:
    name = "harbour_gate"
    supported_operations = ("state", "close")

    def __init__(self):
        self.closed = False

    def requires_confirmation_for(self, operation):
        return operation == "close"

    def execute(self, operation, **params):
        from src.providers.connection import ConnectionResult, ConnectionStatus

        if operation == "close":
            self.closed = True
        return ConnectionResult(operation, ConnectionStatus.SUCCEEDED, detail="closed" if self.closed else "open")

    def parameters_for(self, operation):
        return ()


class _Resolver:
    def __init__(self, *connections):
        self._c = {c.name: c for c in connections}
        self.names = tuple(self._c)
        self.default_name = self.names[0]

    def get(self, name=None):
        return self._c.get(name) if name else self._c[self.default_name]


class TestTheAutomationTool:
    def test_declare_propose_approve_and_dismiss(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        gate = _Gate()
        clock = _Clock(T0)
        tool = AutomationTool(ecosystem, _Resolver(gate), owner_agent="Manager", clock=clock)
        declared = json.loads(tool.run(operation="declare", content=json.dumps({
            "entity": "tide gauge", "attribute": "level_m", "operator": "above", "threshold": "4",
            "action": {"connection": "harbour_gate", "operation": "close"}})))
        assert declared["condition"]["response"] == "propose" and declared["fired"] is None
        ecosystem.observe([Observation("tide gauge", "level_m", 4.3, observed_at=T0)])
        proposal = ecosystem.events.pending()[0]
        assert proposal.status == "proposed" and not gate.closed
        executed = json.loads(tool.run(operation="approve", content=json.dumps({"event_id": proposal.event_id})))
        assert executed["executed"] and gate.closed and ecosystem.events.get(proposal.event_id).status == "executed"
        with pytest.raises(ValueError):
            tool.run(operation="approve", content=json.dumps({"event_id": proposal.event_id}))
        with pytest.raises(ValueError):
            tool.run(operation="declare", content=json.dumps({"entity": "x", "attribute": "y", "operator": "above",
                                                               "threshold": 1, "action": {"connection": "nowhere", "operation": "go"}}))

    def test_an_already_true_condition_fires_when_declared(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.observe([Observation("vent", "angle", 50, observed_at=T0)])
        tool = AutomationTool(ecosystem, _Resolver(_Gate()), owner_agent="Manager", clock=_Clock(T0 + timedelta(seconds=5)))
        declared = json.loads(tool.run(operation="declare", content=json.dumps(
            {"entity": "vent", "attribute": "angle", "operator": "above", "threshold": 40})))
        assert declared["fired"]["value"] == 50


# -- the tick ---------------------------------------------------------------------

class TestTheTick:
    def test_steps_are_a_registry_and_a_failing_step_costs_only_itself(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        assert tick_steps()[:2] == ("watches", "conditions")
        register_tick_step("gm02_probe", lambda eco, conn, now: "probe ran")
        register_tick_step("gm02_broken", lambda eco, conn, now: 1 / 0)
        try:
            report = run_ecosystem_tick(ecosystem, None, now=T0)
            assert "probe ran" in report.lines and any("gm02_broken" in f for f in report.failures)
        finally:
            from src.services.automation import tick

            tick._STEPS.pop("gm02_probe", None)
            tick._STEPS.pop("gm02_broken", None)

    def test_the_background_ticker_runs_the_tick(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ran = threading.Event()
        register_tick_step("gm02_ping", lambda eco, conn, now: ran.set() or "ping")
        try:
            runtime = type("R", (), {"ecosystem": ecosystem, "connections": None})()
            ticker = BackgroundTicker(lambda: runtime, interval=1).start()
            assert ran.wait(5)
            ticker.stop()
        finally:
            from src.services.automation import tick

            tick._STEPS.pop("gm02_ping", None)


# -- claims and the real doors -----------------------------------------------------

class TestThroughTheRealDoors:
    @pytest.fixture
    def rig(self, tmp_path, allow_loopback):
        gauge = Device(get={"/level": lambda d, p: {"level_m": d.state.get("level", 2.0)}})
        gate = Device(get={"/state": lambda d, p: {"position": d.state.get("position", "open")}},
                      post={"/close": lambda d, p: d.state.__setitem__("position", "closed") or {"position": "closed"}})
        system = System(tmp_path)
        system.cc(f"services tide_gauge declare {gauge.url} level:GET:/level:open")
        system.cc(f"services harbour_gate declare {gate.url} state:GET:/state:open,close:POST:/close:open")
        yield system, gauge, gate
        gauge.close()
        gate.close()

    def test_claims_need_a_real_subject_and_a_real_test(self, rig):
        system, _, _ = rig
        assert condition_watch_intent(Task(description="tell me when the tide gauge level goes above 3.5"))
        assert not condition_watch_intent(Task(description="tell me when the sun goes above the horizon"))
        assert not condition_watch_intent(Task(description="tell me about the tide gauge"))
        assert ecosystem_events_intent(Task(description="have any alerts fired?"))

    def test_notify_fires_once_propose_waits_and_a_yes_runs_it(self, rig):
        from scripts.serve_ui import LiveSystem, deliver_condition_event

        system, gauge, gate = rig
        assert system.say("tell me when the tide gauge level goes above 3.5").capability == "condition_watch"
        turn = system.say("if the tide gauge level rises above 4, close the harbour gate")
        assert turn.capability == "condition_watch" and "wait for your yes" in turn.outcome
        for level in (3.0, 3.8, 3.9, 4.2):
            gauge.state["level"] = level
            system.cc("observe sample")
        events = system.ecosystem.events.all()
        assert [e.response for e in events] == ["notify", "propose"] and gate.state.get("position", "open") == "open"
        live = LiveSystem(runtime=system.runtime, manager=system.manager, channel=system.channel,
                          conversation=system.conversation, centre=system.centre, lock=threading.Lock())
        deliver_condition_event(live, events[1])
        assert "Shall I run 'close'" in system.conversation.utterances[-1].text
        turn = system.say("yes")
        assert turn.capability == "connected_action" and gate.state["position"] == "closed"
        assert system.ecosystem.events.get(events[1].event_id).status == "delivered"

    def test_state_survives_a_rebuild_and_a_true_condition_does_not_refire(self, rig):
        system, gauge, _ = rig
        system.say("tell me when the tide gauge level goes above 3.5")
        gauge.state["level"] = 3.9
        system.cc("observe sample")
        system.rebuild()
        gauge.state["level"] = 4.0
        system.cc("observe sample")
        assert len([e for e in system.ecosystem.events.all() if e.response == "notify"]) == 1
        assert "tide_gauge_level_m_above" in system.cc("conditions")

    def test_control_center_declare_approve_and_audit(self, rig):
        system, gauge, gate = rig
        system.cc("observe tide_gauge level every 5s")
        out = system.cc("conditions add tide_gauge level_m above 4 then harbour_gate close")
        assert out.startswith("Condition set:"), out
        gauge.state["level"] = 4.4
        system.cc("observe sample")
        proposal = system.ecosystem.events.pending()[0]
        assert system.cc(f"events approve {proposal.event_id}").startswith("Executed:") and gate.state["position"] == "closed"
        assert ("admin.conditions", True) in system.audit() and ("admin.events", True) in system.audit()
        assert "no conditions" not in system.cc("conditions")
        assert "nothing was due" in system.cc("tick") or "ecosystem tick" in system.cc("tick")

    def test_the_heartbeat_runs_the_tick_and_delivers_through_the_proactive_pipeline(self, rig):
        import scripts.heartbeat as heartbeat

        from src.services.observations.store import utc_now

        system, gauge, _ = rig
        system.say("tell me when the tide gauge level goes above 3.5")
        gauge.state["level"] = 3.7
        assert "nothing was due" in heartbeat.check_ecosystem(system.settings), "the composed watch just read"
        report = heartbeat.check_ecosystem(system.settings, clock=lambda: utc_now() + timedelta(minutes=2))
        assert "watches: 1/1 read" in report and "conditions:" in report
        delivered = heartbeat.check_proactive_events(system.settings, dry_run=True)
        assert "1 due" in delivered
        assert "already reported" in heartbeat.check_proactive_events(system.settings, dry_run=True)
