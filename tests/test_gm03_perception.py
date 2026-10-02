"""GM-03: structured perception - what is where in a still or a recording,
followed over time, counted, and kept as observations - through a provider
axis, a governed tool, a capability, the Control Center and a watch's frames.

Every scene here is drawn by the test and described only by its ground truth;
nothing in src/ knows what any of them depicts."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from src.core.contracts import Task
from src.manager.capabilities import image_analysis_intent, knowledge_intent, perceive_media_intent, select_capability
from src.providers.perception.contract import Detection, Frame, FramePerception, PerceptionError
from src.providers.perception.factory import available_perception_backends, build_perception
from src.providers.perception.motion import MotionPerception
from src.providers.perception.regions import RegionPerception
from src.services.ecosystem_state import build_ecosystem_state
from src.services.perception.analysis import analyse, observations_of
from src.services.perception.frames import look_path, perceive_frames
from src.services.perception.tracking import Track, link_fragments, track
from tests.gm_support import Device, System, allow_loopback, isolate_owner_state, settings_for  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


GROUND = (96, 98, 102)


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _scene(t: float, things, *, ground=GROUND, dim: float = 1.0, size=(240, 140)) -> bytes:
    """``things``: (colour, box_at(t) or None) - drawn over plain ground."""
    image = Image.new("RGB", size, tuple(int(c * dim) for c in ground))
    draw = ImageDraw.Draw(image)
    for colour, box_at in things:
        box = box_at(t)
        if box is not None:
            draw.rectangle(box, fill=colour)
    return _png(image)


def _frames(things, *, seconds: float = 10.0, count: int = 21, **kwargs) -> "list[Frame]":
    return [Frame(i * seconds / (count - 1), _scene(i * seconds / (count - 1), things, **kwargs)) for i in range(count)]


def _slide(x0, x1, t0, t1, y, w=30, h=18, *, stay_after=True):
    def box(t):
        if t < t0:
            return None
        if t > t1 and not stay_after:
            return None
        k = min(1.0, (t - t0) / (t1 - t0)) if t1 > t0 else 1.0
        x = x0 + (x1 - x0) * k
        return [x, y, x + w, y + h]
    return box


# -- the provider axis -----------------------------------------------------------

class TestTheAxis:
    def test_backends_are_named_and_selected_by_settings(self):
        assert available_perception_backends() == ["motion", "regions", "local_vision", "null"]
        assert build_perception(SimpleNamespace(perception="motion")).name == "motion"
        assert build_perception(SimpleNamespace(perception="regions")).name == "regions"
        with pytest.raises(ValueError, match="Unknown perception backend"):
            build_perception(SimpleNamespace(perception="x-ray"))

    def test_null_refuses_honestly(self):
        with pytest.raises(PerceptionError, match="no perception is configured"):
            build_perception(SimpleNamespace(perception="null")).perceive([Frame(0, _scene(0, []))])

    def test_local_vision_without_its_model_says_so_and_never_names(self, tmp_path):
        backend = build_perception(SimpleNamespace(perception="local_vision", local_vision_model_path=str(tmp_path / "none"),
                                                   local_vision_projector_path=str(tmp_path / "none")))
        assert not backend.labels_objects and "not installed" in backend.describes
        with pytest.raises(PerceptionError, match="not installed"):
            backend.label(_scene(0, []), (0.1, 0.1, 0.5, 0.5))

    def test_settings_field_is_validated_and_defaults_to_motion(self, tmp_path):
        assert settings_for(tmp_path).perception == "motion"
        assert settings_for(tmp_path, perception=" Regions ").perception == "regions"
        with pytest.raises(ValueError, match="perception must not be empty"):
            settings_for(tmp_path, perception=" ")


# -- seeing ---------------------------------------------------------------------

class TestMotion:
    def test_a_thing_that_arrives_stops_and_leaves(self):
        def box(t):
            if t < 2:
                return None
            x = 250 - 40 * (t - 2) if t < 5 else (130 if t < 8 else 130 - 50 * (t - 8))
            return [x, 60, x + 34, 84]
        report = analyse(_frames([((220, 60, 50), box)]), MotionPerception(), source="the yard")
        assert len(report.tracks) == 1
        events = [e["what"] for e in report.events]
        assert events[:1] == ["came into view"] and "stopped" in events and "moved off" in events
        assert report.tracks[0].heading() == "right to left"

    def test_a_change_of_light_is_not_a_thing(self):
        frames = [Frame(i * 0.5, _scene(i * 0.5, [], ground=(70, 128, 58), dim=0.75 if 4 <= i < 12 else 1.0))
                  for i in range(20)]
        report = analyse(frames, MotionPerception(), source="a lawn")
        assert report.tracks == [] and report.peak == 0

    def test_two_things_crossing_keep_who_they_are(self):
        a = ((30, 30, 30), lambda t: [10 + 20 * t, 55, 40 + 20 * t, 75])
        b = ((230, 200, 40), lambda t: [200 - 20 * t, 60, 230 - 20 * t, 80])
        report = analyse(_frames([a, b]), MotionPerception(), source="a corridor")
        headings = sorted(t.heading() for t in report.tracks)
        assert len(report.tracks) == 2 and headings == ["left to right", "right to left"]

    def test_a_small_fast_thing_is_followed_not_lost(self):
        # Moves ~0.2 of the width between sampled moments - further than it is wide.
        fast = ((40, 120, 200), lambda t: [-20 + 48 * t, 110, -8 + 48 * t, 118] if 0.5 <= t <= 5 else None)
        report = analyse(_frames([fast], seconds=6, count=13), MotionPerception(), source="a lane")
        assert len(report.tracks) == 1 and len(report.tracks[0].points) >= 4
        assert report.tracks[0].heading() == "left to right"

    def test_needs_a_sequence(self):
        with pytest.raises(PerceptionError, match="at least three"):
            MotionPerception().perceive([Frame(0, _scene(0, []))])


class TestRegionsAndTracking:
    def test_a_still_counts_separate_regions_with_their_look(self):
        things = [((200, 30, 30), lambda t: [20, 20, 60, 50]), ((30, 90, 160), lambda t: [120, 30, 170, 60]),
                  ((240, 220, 40), lambda t: [60, 90, 100, 120])]
        perception = RegionPerception().perceive([Frame(0, _scene(0, things))])[0]
        assert len(perception.detections) == 3
        assert all(len(d.signature) == 3 for d in perception.detections)

    def test_a_merged_moment_does_not_break_a_stop(self):
        still = Detection((0.5, 0.5, 0.6, 0.6), 0.9)
        merged = Detection((0.45, 0.5, 0.6, 0.8), 0.9)
        followed = Track(1, [(0, still), (1, still), (2, merged), (3, still), (4, still)])
        assert followed.still_spans() == [(0, 4)]

    def test_fragments_join_only_along_their_own_motion(self):
        d = lambda x: Detection((x, 0.4, x + 0.05, 0.45), 0.9, signature=(200, 50, 50))  # noqa: E731
        a = Track(1, [(0.0, d(0.0)), (1.0, d(0.1))])
        b = Track(2, [(3.0, d(0.3)), (4.0, d(0.4))])          # where a's motion puts it
        c = Track(3, [(3.0, d(0.8)), (4.0, d(0.9))])          # somewhere else
        joined = link_fragments([a, b, c], max_gap_seconds=3)
        assert len(joined) == 2 and len(joined[0].points) == 4

    def test_appearance_keeps_unlike_things_apart(self):
        red = Detection((0.4, 0.4, 0.5, 0.5), 0.9, signature=(220, 40, 40))
        blue = Detection((0.4, 0.4, 0.5, 0.5), 0.9, signature=(30, 60, 220))
        assert red.looks_like(red) == 1.0 and red.looks_like(blue) < 0.2
        assert Detection((0.4, 0.4, 0.5, 0.5), 0.9).looks_like(red) == 1.0  # no evidence either way


class TestReportAndObservations:
    def test_the_report_is_honest_about_what_it_cannot_say(self):
        report = analyse(_frames([((220, 60, 50), _slide(0, 200, 1, 8, 50))]), MotionPerception(), source="the bay")
        text = report.describe(asked_about="forklifts")
        assert "not whether they are forklifts" in text and "1 distinct thing" in text
        assert report.picture[:8] == b"\x89PNG\r\n\x1a\n"

    def test_observations_carry_counts_positions_and_totals(self):
        report = analyse(_frames([((220, 60, 50), _slide(0, 200, 1, 8, 50))]), MotionPerception(), source="the bay")
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        found = observations_of(report, started=start, sensitivity="personal")
        attributes = {(o.entity, o.attribute) for o in found}
        assert ("the bay", "in_view") in attributes and ("the bay", "distinct_seen") in attributes
        assert ("the bay #1", "position") in attributes
        assert all(o.sensitivity == "personal" for o in found)


# -- routing ---------------------------------------------------------------------

class TestTheAskIsRecognisedByItsShape:
    @pytest.mark.parametrize("text", [
        "how many cows are in this video?", "count the vehicles in the turnaround footage",
        "track the moving things in this video", "where did each boat go in this clip?",
        "when did the cart stop in the video?", "how many parcels passed in dock.mp4",
        "detect the objects in this picture", "which way did the crane move in the footage",
    ])
    def test_structured_asks_about_a_medium(self, text):
        assert perceive_media_intent(Task(description=text))
        assert select_capability(Task(description=text)).name == "perceive_media"

    @pytest.mark.parametrize("text", [
        "how many seconds long is this video?", "count me in for the video call tomorrow",
        "follow the instructions in this video", "how many times does he say hello in this video",
        "I watched the video, how many episodes are left?", "track my order", "follow up about the photo I sent",
        "what is in this video?", "make a video of a cat and count to ten",
    ])
    def test_everything_else_is_left_alone(self, text):
        assert not perceive_media_intent(Task(description=text))

    def test_describing_stays_with_image_analysis_and_counting_is_not_knowledge(self):
        assert image_analysis_intent(Task(description="describe this video"))
        assert not image_analysis_intent(Task(description="tell me how many cows are in this video"))
        assert not knowledge_intent(Task(description="when did the aircraft stop in the video?"))


# -- frames from a watch ---------------------------------------------------------

class TestFramesFromAWatch:
    def test_frames_are_seen_counted_kept_and_never_double_counted(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.bind_provider("perception", build_perception(SimpleNamespace(perception="motion")))
        watch = SimpleNamespace(watch_id="yard_cam_feed", entity="yard cam", sensitivity="")
        start = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        thing = ((220, 60, 50), _slide(10, 200, 0, 9, 60))
        items = [SimpleNamespace(received_at=start + timedelta(seconds=i * 0.5), content_type="image/jpeg",
                                 payload=_scene(i * 0.5, [thing])) for i in range(12)]
        said = perceive_frames(watch, ecosystem, items[:8])
        assert "perception (motion) looked at 8 frame(s)" in said
        first = len(ecosystem.observations.series("yard cam", "in_view"))
        perceive_frames(watch, ecosystem, items[8:])
        counts = ecosystem.observations.series("yard cam", "in_view")
        assert first == 8 and len(counts) == 12          # the second hand-over added only what was new
        assert look_path(ecosystem, "yard_cam_feed").is_file()

    def test_without_a_bound_backend_it_says_so(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        watch = SimpleNamespace(watch_id="w", entity="e", sensitivity="")
        item = SimpleNamespace(received_at=datetime.now(timezone.utc), content_type="image/png", payload=b"x")
        assert "no perception backend is bound" in perceive_frames(watch, ecosystem, [item])


# -- through the real system -----------------------------------------------------

def _mp4(tmp_path, things, *, seconds=8, fps=8) -> bytes:
    from src.providers.media.video import resolve_ffmpeg

    folder = tmp_path / "frames"
    folder.mkdir(exist_ok=True)
    for i in range(seconds * fps):
        (folder / f"f{i:04d}.png").write_bytes(_scene(i / fps, things))
    out = tmp_path / "clip.mp4"
    subprocess.run([resolve_ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(folder / "f%04d.png"),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(out)], check=True)
    return out.read_bytes()


def _share(system, name: str, data: bytes, mime: str) -> str:
    from scripts.serve_ui import LiveSystem, route_upload

    live = LiveSystem(runtime=system.runtime, manager=system.manager, channel=system.channel,
                      conversation=system.conversation, centre=system.centre, lock=threading.Lock())
    status, body, _ = route_upload(system.runtime.identity.name, name, mime, base64.b64encode(data).decode(),
                                   "conversation", live)
    assert status == 200, body
    return json.loads(body)["artifact_id"]


class TestThroughTheRealSystem:
    def test_a_shared_recording_asked_about_in_conversation(self, tmp_path):
        system = System(tmp_path)
        clip = _mp4(tmp_path, [((220, 60, 50), _slide(250, 20, 0.5, 6, 50)), ((40, 120, 200), _slide(-30, 120, 2, 5, 100))])
        source = _share(system, "delivery bay.mp4", clip, "video/mp4")
        turn = system.say("how many trolleys are in this video and where did each one go?")
        assert turn.capability == "perceive_media", turn.outcome
        assert "2 distinct things" in turn.outcome and "not whether they are trolleys" in turn.outcome
        picture = [a for a in system.runtime.artifacts.all() if a.latest.metadata.get("technique") == "perception"]
        assert len(picture) == 1 and source in picture[0].latest.related_artifact_ids
        assert system.ecosystem.observations.resolve_entity("delivery bay.mp4")
        # "this video" still means the person's video, never the saved view of it.
        again = system.say("count the things in this video")
        assert "of delivery bay.mp4" in again.outcome

    def test_the_axis_is_governed_live_and_persisted(self, tmp_path):
        system = System(tmp_path)
        image = Image.new("RGB", (200, 120), (182, 150, 110))
        ImageDraw.Draw(image).rectangle([20, 20, 60, 60], fill=(40, 40, 160))
        _share(system, "shelf.png", _png(image), "image/png")
        assert "Applied live" in system.cc("provider perception_provider use null")
        refused = system.say("how many boxes are on the shelf in this picture?")
        assert refused.capability == "perceive_media" and "no perception is configured" in refused.outcome
        system.rebuild()
        assert system.runtime.perception.name == "null"
        _share(system, "shelf.png", _png(image), "image/png")  # a new start is a new room
        system.cc("provider perception_provider use regions")
        assert "1 distinct thing" in system.say("how many boxes are on the shelf in this picture?").outcome

    def test_the_control_center_verb_is_a_governed_write(self, tmp_path):
        system = System(tmp_path)
        clip = _mp4(tmp_path, [((220, 60, 50), _slide(250, 20, 0.5, 6, 50))])
        source = _share(system, "gate.mp4", clip, "video/mp4")
        out = system.cc(f"perceive {source}")
        assert out.startswith("Seen:") and "kept:" in out
        assert ("admin.perceive", True) in system.audit()
        assert system.cc("perceive").startswith("perception backend: motion")

    def test_a_camera_watch_hands_its_frames_to_perception(self, tmp_path, allow_loopback):
        def feed(handler, device):
            handler.send_response(200)
            handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            handler.end_headers()
            thing = ((220, 60, 50), _slide(0, 200, 0, 3, 50))
            for i in range(16):
                buffer = io.BytesIO()
                Image.open(io.BytesIO(_scene(i * 0.2, [thing]))).save(buffer, "JPEG")
                data = buffer.getvalue()
                handler.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode()
                                    + b"\r\n\r\n" + data + b"\r\n")
                handler.wfile.flush()
                time.sleep(0.02)
            handler.wfile.write(b"--frame--\r\n")
            handler.close_connection = True

        camera = Device(raw={"/feed": feed})
        try:
            system = System(tmp_path)
            system.cc(f"services yard_cam declare {camera.url} feed:GET:/feed:open+stream")
            turn = system.say("keep an eye on the yard cam every 30 seconds as the yard and count what's in view")
            assert turn.capability == "observation_watch" and "perception (motion) looked at" in turn.outcome, turn.outcome
            assert system.ecosystem.watches.all()[0].frames_to == "perception"
            assert system.ecosystem.observations.latest("the yard")["in_view"].value >= 1
        finally:
            camera.close()
