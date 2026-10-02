"""D-1 + GM-01: a connection's answer kept as a value, and observation history.

Every subject here is deliberately one no module in src/ names - a proofing
cabinet, a tide gauge, a kiln - because the mechanism must not care.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.tools.observation import ObservationTool
from src.core.contracts import Task
from src.manager.ecosystem_capabilities import (
    attributes_mentioned,
    bind_ecosystem,
    observation_history_intent,
    observation_watch_intent,
    parse_every,
)
from src.providers.connection import ConnectionResult, ConnectionStatus, StreamResult, is_json_value
from src.providers.declared_connection import DeclaredConnection, structured_answer
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation
from src.services.ecosystem_state import build_ecosystem_state
from src.services.observations.extract import flatten, observations_from, position_of
from src.services.observations.store import Observation, ObservationStore, parse_duration, parse_window
from src.services.observations.watches import Watch, WatchRegistry, sample, watchable
from tests.gm_support import Device, System, allow_loopback, isolate_owner_state, settings_for  # noqa: F401

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


# -- D-1 -----------------------------------------------------------------------

class TestAConnectionAnswerKeepsItsValue:
    def test_structured_data_is_validated_as_json(self):
        result = ConnectionResult("read", ConnectionStatus.SUCCEEDED, data={"a": [1, 2.5, True, None, "x"]})
        assert result.has_structure and result.data["a"][1] == 2.5
        with pytest.raises(ValueError):
            ConnectionResult("read", ConnectionStatus.SUCCEEDED, data={"a": object()})
        with pytest.raises(ValueError):
            ConnectionResult("read", ConnectionStatus.SUCCEEDED, data={1: "not a string key"})

    def test_a_payload_is_bytes_and_the_content_type_is_normalised(self):
        result = ConnectionResult("frame", ConnectionStatus.SUCCEEDED, payload=bytearray(b"\x89PNG"),
                                  content_type="Image/PNG; charset=binary")
        assert result.payload == b"\x89PNG" and result.content_type == "image/png"
        with pytest.raises(ValueError):
            ConnectionResult("frame", ConnectionStatus.SUCCEEDED, payload="text")

    def test_an_old_style_result_is_unchanged(self):
        result = ConnectionResult("read", ConnectionStatus.SUCCEEDED, detail="done")
        assert result.data is None and result.payload is None and not result.has_structure

    def test_answers_are_decided_by_what_the_bytes_are(self):
        long_json = {"values": list(range(2000))}
        answer = structured_answer(json.dumps(long_json).encode(), "application/json")
        assert answer["data"] == long_json, "the value is kept whole even when its words are cut"
        assert len(answer["detail"]) <= 2000
        text = structured_answer(b"plain words", "text/plain")
        assert text == {"detail": "plain words", "content_type": "text/plain"}
        binary = structured_answer(b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
        assert binary["payload"] == b"\xff\xd8\xff\xe0jpeg" and binary["content_type"] == "image/jpeg"
        assert "data" not in binary

    def test_is_json_value_bounds_depth(self):
        deep: list = []
        node = deep
        for _ in range(80):
            node.append([])
            node = node[0]
        assert not is_json_value(deep)

    def test_the_default_stream_is_honestly_unsupported(self):
        from src.providers.null_connection import NullConnection

        result = NullConnection().stream("anything")
        assert isinstance(result, StreamResult) and result.status is ConnectionStatus.UNSUPPORTED
        assert not NullConnection().supports_streaming("anything")

    def test_a_real_device_answer_arrives_whole_and_binary_stays_binary(self, allow_loopback):
        frame = bytes(range(256)) * 4
        device = Device(get={"/state": lambda d, p: {"gauge": {"level_m": 2.41}},
                             "/frame": lambda d, p: ("image/jpeg", frame)})
        try:
            connection = DeclaredConnection(ServiceDescriptor(name="tide_gauge", base_url=device.url, operations=(
                ServiceOperation(name="state", method="GET", path="/state", requires_auth=False),
                ServiceOperation(name="frame", method="GET", path="/frame", requires_auth=False),
            )), account=None)
            state = connection.execute("state")
            assert state.ok and state.data == {"gauge": {"level_m": 2.41}}
            picture = connection.execute("frame")
            assert picture.ok and picture.payload == frame and picture.content_type == "image/jpeg"
        finally:
            device.close()


# -- extraction ------------------------------------------------------------------

class TestAnyAnswerBecomesObservations:
    def test_leaves_positions_and_named_members(self):
        leaves = dict(flatten({
            "chamber": {"temp_c": 871.5, "door": "closed"},
            "fans": [{"id": "a", "rpm": 1200}, {"id": "b", "rpm": 1180}],
            "where": {"lat": 6.45, "lon": 3.39},
            "notes": "x" * 500,
            "flags": [True, False],
        }))
        assert leaves["chamber.temp_c"] == 871.5 and leaves["chamber.door"] == "closed"
        assert leaves["fans.a.rpm"] == 1200 and leaves["fans.b.rpm"] == 1180
        assert leaves["where"] == {"lat": 6.45, "lon": 3.39}, "a position stays one value"
        assert "notes" not in leaves, "long text is not a reading"
        assert leaves["flags.0"] is True

    def test_position_shapes(self):
        assert position_of({"latitude": 1, "longitude": 2, "altitude": 3}) == {"lat": 1.0, "lon": 2.0, "alt": 3.0}
        assert position_of({"x": 1, "y": 2}) == {"x": 1.0, "y": 2.0}
        assert position_of({"x": 1, "y": 2, "speed": 3}) is None

    def test_a_scalar_answer_and_keep(self):
        observations = observations_from(
            {"a": {"b": 1, "c": 2}, "d": 3}, entity="kiln", source="t", observed_at=T0, keep=("a.b", "d"),
        )
        assert sorted(o.attribute for o in observations) == ["a.b", "d"]


# -- the store -------------------------------------------------------------------

class TestTheObservationStore:
    def _filled(self, tmp_path):
        store = ObservationStore(tmp_path)
        store.record(Observation("Tide Gauge", "level_m", 1.0 + i * 0.1, observed_at=T0 + timedelta(minutes=10 * i),
                                 unit="m") for i in range(10))
        store.record([Observation("tide gauge", "state", "rising", observed_at=T0)])
        return store

    def test_series_summary_latest(self, tmp_path):
        store = self._filled(tmp_path)
        assert store.resolve_entity("TIDE_GAUGE") == "Tide Gauge"
        summary = store.summarize("tide gauge", "level_m", window=timedelta(minutes=30), now=T0 + timedelta(minutes=90))
        assert summary.count == 4 and summary.minimum == pytest.approx(1.6) and summary.maximum == pytest.approx(1.9)
        assert summary.rate_per_hour == pytest.approx(0.6)
        assert "4 readings" in summary.describe() and " m" in summary.describe()
        assert store.latest("tide gauge")["state"].value == "rising"

    def test_non_numeric_values_are_counted(self, tmp_path):
        store = ObservationStore(tmp_path)
        store.record(Observation("door", "state", s, observed_at=T0 + timedelta(minutes=i))
                     for i, s in enumerate(["open", "closed", "open", "open"]))
        summary = store.summarize("door", "state", now=T0 + timedelta(hours=1))
        assert not summary.numeric and summary.most_frequent == "open" and summary.distinct == 2

    def test_persisted_and_rebuilt(self, tmp_path):
        self._filled(tmp_path)
        (tmp_path / "observations" / "catalog.json").unlink()
        store = ObservationStore(tmp_path)
        assert store.resolve_entity("tide gauge") == "Tide Gauge", "the catalog is rebuilt from the files"
        assert len(store.series("tide gauge", "level_m")) == 10

    def test_a_torn_line_costs_only_itself(self, tmp_path):
        store = self._filled(tmp_path)
        path = next((tmp_path / "observations").glob("*.jsonl"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"a":"level_m","v":')
        assert len(ObservationStore(tmp_path).series("tide gauge", "level_m")) == 10

    def test_compaction_keeps_the_newest(self, tmp_path):
        store = ObservationStore(tmp_path, max_per_entity=5)
        store.record(Observation("pump", "rpm", i, observed_at=T0 + timedelta(seconds=i)) for i in range(12))
        values = [o.value for o in store.series("pump", "rpm")]
        assert values == [7, 8, 9, 10, 11]

    def test_sensitivity_is_a_ceiling_and_forget_removes(self, tmp_path):
        store = ObservationStore(tmp_path)
        store.record([Observation("sleep sensor", "hours", 7.5, observed_at=T0, sensitivity="sensitive")])
        assert store.series("sleep sensor", "hours", max_sensitivity="personal") == []
        assert store.entities(max_sensitivity="personal") == []
        assert len(store.series("sleep sensor", "hours", max_sensitivity="sensitive")) == 1
        assert store.forget("sleep sensor") == 1 and store.series("sleep sensor") == []

    def test_an_observation_refuses_what_is_not_a_reading(self):
        with pytest.raises(ValueError):
            Observation("x", "y", [1, 2], observed_at=T0)
        with pytest.raises(ValueError):
            Observation("x", "y", float("nan"), observed_at=T0)
        with pytest.raises(ValueError):
            Observation("x", "y", 1, observed_at=datetime(2026, 1, 1))

    def test_windows_and_durations(self):
        assert parse_window("over the last 2 hours") == timedelta(hours=2)
        assert parse_window("in the past thirty minutes") is None or parse_window("past 30 minutes") == timedelta(minutes=30)
        assert parse_window("today") == timedelta(hours=24)
        assert parse_window("what is it") is None
        assert parse_duration("5m") == timedelta(minutes=5) and parse_duration("90s") == timedelta(seconds=90)
        assert parse_every("every five minutes") == 300 and parse_every("hourly") == 3600


# -- watches ---------------------------------------------------------------------

class _Reading:
    """A connection with one safe read and one consequential write."""

    name = "kiln"
    supported_operations = ("read_state", "fire")

    def __init__(self):
        self.value = 900.0
        self.fail = False

    def requires_confirmation_for(self, operation):
        return operation == "fire"

    def supports_streaming(self, operation):
        return False

    def operation_description_for(self, operation):
        return ""

    def execute(self, operation, **params):
        if self.fail:
            return ConnectionResult(operation, ConnectionStatus.FAILED, detail="unreachable")
        return ConnectionResult(operation, ConnectionStatus.SUCCEEDED, detail="ok",
                                data={"chamber": {"temp_c": self.value}}, executed_at=T0)


class TestWatches:
    def test_only_a_safe_read_can_be_watched(self):
        connection = _Reading()
        assert watchable(connection, "read_state") is None
        assert "only a safe read" in watchable(connection, "fire")
        assert "has no operation" in watchable(connection, "nope")

    def test_sampling_records_through_the_recorder_and_notes_failure(self, tmp_path):
        registry = WatchRegistry(tmp_path)
        watch = Watch("kiln_read_state", "kiln", "read_state", "kiln", interval_seconds=30)
        registry.declare(watch)
        seen = []
        connection = _Reading()
        outcome = sample(watch, connection, lambda obs: seen.extend(obs) or len(obs))
        assert outcome.ok and outcome.recorded == 1 and seen[0].attribute == "chamber.temp_c"
        registry.note(outcome, T0)
        connection.fail = True
        failed = sample(watch, connection, lambda obs: 0)
        assert not failed.ok and "unreachable" in failed.detail
        registry.note(failed, T0 + timedelta(seconds=10))
        state = registry.state()["kiln_read_state"]
        assert state["samples"] == 1 and state["failures"] == 1

    def test_due_respects_the_interval_and_pause(self, tmp_path):
        registry = WatchRegistry(tmp_path)
        registry.declare(Watch("w", "kiln", "read_state", "kiln", interval_seconds=60))
        assert [w.watch_id for w in registry.due(T0)] == ["w"]
        registry.note(sample(registry.get("w"), _Reading(), lambda obs: len(obs)), T0)
        assert registry.due(T0 + timedelta(seconds=30)) == []
        assert [w.watch_id for w in registry.due(T0 + timedelta(seconds=61))] == ["w"]
        registry.set_enabled("w", False)
        assert registry.due(T0 + timedelta(hours=1)) == []

    def test_an_interval_below_the_floor_is_refused(self):
        with pytest.raises(ValueError):
            Watch("w", "kiln", "read_state", "kiln", interval_seconds=1)


# -- the tool and the claims -----------------------------------------------------

class _Resolver:
    def __init__(self, connection):
        self._connection = connection
        self.names = (connection.name,)
        self.default_name = connection.name

    def get(self, name=None):
        return self._connection if name in (None, "", self._connection.name) else None


class TestTheToolAndTheClaims:
    def test_the_tool_declares_samples_and_summarises(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        connection = _Reading()
        tool = ObservationTool(ecosystem, _Resolver(connection), owner_agent="Manager", clock=lambda: T0)
        declared = json.loads(tool.run(operation="watch", content=json.dumps(
            {"connection": "kiln", "operation": "read_state", "entity": "Kiln", "interval_seconds": 30})))
        assert declared["watch"]["watch_id"] == "kiln_read_state"
        with pytest.raises(ValueError):
            tool.run(operation="watch", content=json.dumps({"connection": "kiln", "operation": "fire"}))
        for value in (900, 950, 1000):
            connection.value = value
            tool.run(operation="sample", content="{}")
        summary = json.loads(tool.run(operation="summary", content=json.dumps({"entity": "kiln", "attribute": "chamber.temp_c"})))
        assert summary["summary"]["max"] == 1000 and "3 readings" in summary["text"]

    def test_a_listener_hears_every_arrival(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        heard = []
        ecosystem.listen(lambda batch: heard.extend(batch))
        ecosystem.listen(lambda batch: 1 / 0)  # a failing listener costs only itself
        assert ecosystem.observe([Observation("x", "y", 1, observed_at=T0)]) == 1
        assert len(heard) == 1 and ecosystem.observations.latest("x")["y"].value == 1

    def test_claims_recognise_only_what_is_really_observed(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.observe([Observation("proofing cabinet", "cabinet.temp_c", 27.0, observed_at=T0)])
        bind_ecosystem(ecosystem, _Resolver(_Reading()))
        assert observation_history_intent(Task(description="what has the proofing cabinet temperature been over the last hour?"))
        assert not observation_history_intent(Task(description="what was the highest temperature ever recorded on Earth?"))
        assert not observation_history_intent(Task(description="is the proofing cabinet on?")), "no history shape"
        assert observation_watch_intent(Task(description="keep an eye on the kiln every 5 minutes"))
        assert not observation_watch_intent(Task(description="keep an eye on the weather"))

    def test_attribute_words_exclude_the_entitys_own_name(self):
        attributes = ["cabinet.humidity_pct", "cabinet.temp_c", "door"]
        assert attributes_mentioned("proofing cabinet temperature over the last hour", attributes,
                                    entity="proofing cabinet")[0] == "cabinet.temp_c"
        assert attributes_mentioned("how humid has the proofing cabinet been", attributes,
                                    entity="proofing cabinet")[0] == "cabinet.humidity_pct"


# -- the real system -------------------------------------------------------------

def _status(device, path):
    temp, humidity = device.state.get("now", (25.0, 70))
    return {"cabinet": {"temp_c": temp, "humidity_pct": humidity}, "door": "closed"}


class TestThroughTheRealDoors:
    @pytest.fixture
    def rig(self, tmp_path, allow_loopback):
        device = Device(get={"/status": _status}, post={"/set": lambda d, p: {"ok": True}})
        system = System(tmp_path)
        system.cc(f"services proofer declare {device.url} status:GET:/status:open,set_temp:POST:/set:open:target:body:required")
        yield system, device
        device.close()

    def test_watch_sample_query_and_audit(self, rig):
        system, device = rig
        assert system.cc("observe proofer status every 5s as proofing cabinet").startswith("Watching:")
        assert "only a safe read" in system.cc("observe proofer set_temp")
        for reading in ((26.0, 71), (29.0, 76), (31.5, 78)):
            device.state["now"] = reading
            system.cc("observe sample")
        assert "max 31.5" in system.cc("observations proofing cabinet cabinet.temp_c last 1h")
        turn = system.say("what has the proofing cabinet temperature been over the last hour?")
        assert turn.capability == "observation_history" and "max 31.5" in turn.outcome
        assert ("admin.observe", True) in system.audit() and ("admin.observe", False) in system.audit()

    def test_a_watch_declared_in_conversation_is_not_an_operation(self, rig):
        system, device = rig
        turn = system.say("keep an eye on the proofer every 10 seconds")
        assert turn.capability == "observation_watch" and turn.succeeded
        assert device.calls and all(call[0] == "GET" for call in device.calls), "nothing was set"
        assert ("observation_watch", True) in system.audit()
        turn = system.say("stop watching the proofer")
        assert turn.capability == "observation_watch" and "Stopped" in turn.outcome

    def test_a_requested_read_joins_the_watched_history_and_everything_survives_a_rebuild(self, rig):
        system, device = rig
        system.cc("observe proofer status every 1m as proofing cabinet")
        device.state["now"] = (33.0, 80)
        turn = system.say("proofer: status")
        assert turn.capability == "connected_action"
        assert system.ecosystem.observations.latest("proofing cabinet")["cabinet.temp_c"].value == 33.0
        system.rebuild()
        assert system.ecosystem.observations.latest("proofing cabinet")["cabinet.temp_c"].value == 33.0
        assert "proofing_cabinet_status" in system.cc("observe")
