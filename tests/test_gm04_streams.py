"""GM-04: streaming and binary transport on the Connection axis - events,
JSON lines and frame streams read by what the bytes are, bounded, and kept
as observations the moment they arrive. Subjects (a beehive entrance, a test
rig, a dock camera) appear nowhere in src/."""

from __future__ import annotations

import io
import json
import time

import pytest

from src.providers.connection import StreamItem
from src.providers.declared_connection import DeclaredConnection
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation
from src.providers.service_discovery import _openapi_mode
from src.providers.stream_readers import read_stream
from src.services.automation.tick import (
    StreamListeners,
    hand_over_frames,
    register_frame_consumer,
    run_ecosystem_tick,
)
from src.services.ecosystem_state import build_ecosystem_state
from src.services.observations.watches import MODE_STREAM, Watch, sample
from tests.gm_support import Device, System, allow_loopback, isolate_owner_state, settings_for  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


class _Slow(io.BytesIO):
    """A response that waits between lines, like a live stream."""

    def __init__(self, data: bytes, pause: float = 0.0):
        super().__init__(data)
        self.pause = pause

    def readline(self, *args):
        time.sleep(self.pause)
        return super().readline(*args)


SSE = (b": hello\n\nevent: traffic\ndata: {\"in\": 1}\n\n"
       b"event: traffic\ndata: {\"in\": 2}\n\ndata: plain words\n\n")
NDJSON = b"{\"rms\": 0.2}\n\n\x1e{\"rms\": 0.3}\nnot json\n"


def _multipart(with_length: bool) -> bytes:
    parts = []
    for body in (b"\xff\xd8one", b"\xff\xd8two"):
        headers = b"Content-Type: image/jpeg\r\n" + (b"Content-Length: " + str(len(body)).encode() + b"\r\n" if with_length else b"")
        parts.append(b"--frame\r\n" + headers + b"\r\n" + body + b"\r\n")
    return b"".join(parts) + b"--frame--\r\n"


class TestTheReadersReadWhatTheBytesAre:
    def test_server_sent_events(self):
        items, ended = read_stream(io.BytesIO(SSE), "text/event-stream", max_items=10, max_seconds=5)
        assert [i.data for i in items[:2]] == [{"in": 1}, {"in": 2}] and items[0].event == "traffic"
        assert items[2].text == "plain words" and ended == "closed"

    def test_json_lines_tolerate_separators_and_keep_text(self):
        items, _ = read_stream(io.BytesIO(NDJSON), "application/x-ndjson", max_items=10, max_seconds=5)
        assert [i.data for i in items] == [{"rms": 0.2}, {"rms": 0.3}, None] and items[2].text == "not json"

    @pytest.mark.parametrize("with_length", [True, False])
    def test_multipart_frames_with_and_without_a_length(self, with_length):
        items, ended = read_stream(io.BytesIO(_multipart(with_length)), "multipart/x-mixed-replace; boundary=frame",
                                   max_items=10, max_seconds=5)
        assert [i.payload for i in items] == [b"\xff\xd8one", b"\xff\xd8two"]
        assert all(i.content_type == "image/jpeg" for i in items) and ended == "closed"

    def test_raw_binary_arrives_in_chunks(self):
        items, _ = read_stream(io.BytesIO(b"\x00" * 70000), "application/octet-stream", max_items=10, max_seconds=5)
        assert sum(len(i.payload) for i in items) == 70000

    def test_limits_stop_and_deadline(self):
        items, ended = read_stream(io.BytesIO(SSE), "text/event-stream", max_items=1, max_seconds=5)
        assert len(items) == 1 and ended == "limit"
        items, ended = read_stream(io.BytesIO(SSE), "text/event-stream", max_items=10, max_seconds=5,
                                   on_item=lambda item: False)
        assert len(items) == 1 and ended == "stopped"
        items, ended = read_stream(_Slow(NDJSON * 20, pause=0.05), "application/x-ndjson", max_items=1000, max_seconds=0.3)
        assert ended == "time" and 0 < len(items) < 60


class TestAStreamIsDeclaredData:
    def test_only_a_get_may_stream(self):
        assert ServiceOperation(name="feed", method="GET", path="/f", mode="stream").mode == "stream"
        with pytest.raises(ValueError):
            ServiceOperation(name="feed", method="POST", path="/f", mode="stream")
        with pytest.raises(ValueError):
            ServiceOperation(name="feed", method="GET", path="/f", mode="sometimes")

    def test_openapi_media_types_decide(self):
        stream = {"responses": {"200": {"content": {"text/event-stream": {}}}}}
        once = {"responses": {"200": {"content": {"application/json": {}}}}}
        assert _openapi_mode("get", stream) == "stream" and _openapi_mode("get", once) == "request"
        assert _openapi_mode("post", stream) == "request"
        assert _openapi_mode("get", {"produces": ["multipart/x-mixed-replace"], "responses": {"200": {}}}) == "stream"

    def test_declared_round_trip_and_grammar(self, tmp_path):
        system = System(tmp_path)
        assert system.cc("services rig declare http://127.0.0.1:9 feed:GET:/f:open+stream,state:GET:/s:open").startswith("declared")
        system.rebuild()
        connection = system.runtime.connections.get("rig")
        assert connection.supports_streaming("feed") and not connection.supports_streaming("state")
        assert "a stream" in system.cc("services rig")


def _sse_route(handler, device):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "close")
    handler.end_headers()
    for i in range(1, 6):
        handler.wfile.write(f"data: {json.dumps({'in': device.state.get('base', 40) + i * 5})}\n\n".encode())
        handler.wfile.flush()
        time.sleep(0.05)
    handler.close_connection = True


def _frames_route(handler, device):
    handler.send_response(200)
    handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(_multipart(True))
    handler.close_connection = True


class TestAgainstARealStream:
    @pytest.fixture
    def rig(self, tmp_path, allow_loopback):
        device = Device(raw={"/events": _sse_route, "/frames": _frames_route})
        connection = DeclaredConnection(ServiceDescriptor(name="hive_counter", base_url=device.url, operations=(
            ServiceOperation(name="traffic", method="GET", path="/events", requires_auth=False, mode="stream"),
            ServiceOperation(name="feed", method="GET", path="/frames", requires_auth=False, mode="stream"),
        )), account=None)
        yield device, connection
        device.close()

    def test_stream_and_first_item(self, rig):
        device, connection = rig
        result = connection.stream("traffic", max_items=10, max_seconds=5)
        assert result.ok and [i.data["in"] for i in result.items] == [45, 50, 55, 60, 65]
        first = connection.execute("feed")
        assert first.ok and first.payload == b"\xff\xd8one" and first.content_type == "image/jpeg"

    def test_a_stream_watch_records_on_arrival_and_hands_over_frames(self, rig, tmp_path):
        device, connection = rig
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        heard = []
        ecosystem.listen(lambda batch: heard.append(len(batch)))
        outcome = sample(Watch("hive", "hive_counter", "traffic", "hive entrance", mode=MODE_STREAM, listen_seconds=5),
                         connection, ecosystem.observe)
        assert outcome.ok and outcome.recorded == 5 and heard == [1, 1, 1, 1, 1], "each item arrived on its own"
        frames = sample(Watch("cam", "hive_counter", "feed", "dock", mode=MODE_STREAM, listen_seconds=5,
                              frames_to="gm04_probe"), connection, ecosystem.observe)
        seen = []
        register_frame_consumer("gm04_probe", lambda watch, eco, items: seen.extend(items) or f"{len(items)} frames")
        assert hand_over_frames(Watch("cam", "hive_counter", "feed", "dock", frames_to="gm04_probe"),
                                ecosystem, list(frames.frames)) == "2 frames"
        assert [f.payload for f in seen] == [b"\xff\xd8one", b"\xff\xd8two"]
        assert "not installed" in hand_over_frames(Watch("c", "x", "y", "z", frames_to="nothing"), ecosystem,
                                                   [StreamItem(0, frames.frames[0].received_at, payload=b"x")])

    def test_the_live_listener_reads_continuously_and_the_tick_leaves_it_alone(self, rig, tmp_path):
        device, connection = rig
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.watches.declare(Watch("hive_live", "hive_counter", "traffic", "hive", mode=MODE_STREAM, listen_seconds=2))

        class _Resolver:
            names = ("hive_counter",)

            def get(self, name=None):
                return connection if name == "hive_counter" else None

        runtime = type("R", (), {"ecosystem": ecosystem, "connections": _Resolver()})()
        listeners = StreamListeners(lambda: runtime, gap=0.05)
        assert listeners.refresh() == ["hive_live"]
        time.sleep(1.0)
        report = run_ecosystem_tick(ecosystem, runtime.connections)
        listeners.stop()
        assert "watches:" not in report.describe(), "the tick did not open a second listen"
        assert ecosystem.watches.state()["hive_live"]["recorded"] >= 10
