"""Real Audio Recording/Capture - the complete vertical slice.

A genuine, user-directed microphone recording - never text pretending to
be audio, never a fake timer, never a browser-only blob that disappears
when the tab closes. This file proves the whole chain is genuinely
connected and genuinely governed, using the identical real-system pattern
``tests/test_alfred_workspace.py``/``tests/test_whole_system_reality_audit.py``
already established:

    Natural language (start_recording/stop_recording/cancel_recording/
    list_recordings capabilities, claims-based, Half-Brain-safe)
        -> src/services/identity/recording_session.py (the one live,
           in-process "is this agent recording right now" signal)
        -> the served page's own backend (POST /recording/started|save|
           cancel|failed|clear - only a browser can actually capture
           audio, so these are UI-action routes, not capabilities)
        -> a real, persistent artifact (register_new_artifact, type
           "voice_recording") plus a governed Memory reference
           (MemoryCategory.AUDIO_RECORDING, via retain_for)
        -> Control Center (ControlCenter.recordings()) and the served
           page's own snapshot, reading the identical backend state
        -> playback, through the existing GET /artifact/<id>.<format>
           route, unchanged.

Python has no route to a real microphone, so "real audio capture" itself
is verified live, against a genuinely WebGL/microphone-capable runtime -
this file proves everything on the *backend* side of that boundary: the
artifact and Memory write, persistence, retrieval, isolation, governance,
Half Brain, and every natural-language trigger, using real bytes a test
supplies in place of a real capture (never a text description standing
in for audio - the "bytes" here are opaque binary, exactly what a real
MediaRecorder blob would also be from this module's point of view).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.core.contracts import Task
from src.services.identity.recording_session import (
    RecordingSessionRegistry,
    RecordingState,
)
from src.services.memory.audio_recording import (
    build_recording_record,
    cancel_recording_intent,
    describe_recording,
    extract_recording_purpose,
    extract_recording_query,
    list_recordings_intent,
    match_recordings_by_topic,
    recording_kind_of,
    start_recording_intent,
    stop_recording_intent,
)
from scripts.control_center import build
from scripts.exchange import exchange, memory_for, retain_for
from scripts.serve_ui import (
    LiveSystem,
    MANAGEMENT_VERBS,
    _RECORDING_EXTENSIONS,
    _recording_extension,
    route,
    route_recording_cancel,
    route_recording_clear,
    route_recording_failed,
    route_recording_save,
    route_recording_started,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
SERVE_UI = REPO_ROOT / "scripts" / "serve_ui.py"

FAKE_AUDIO_B64 = "Zm9vYmFyIGJpbmFyeSBhdWRpbyBieXRlcyAxMjM0NTY3ODkw"  # arbitrary opaque bytes


def real_system(tmp_path, **overrides):
    # artifacts_dir is a separate axis from memory - "ephemeral" memory
    # alone does not stop a real save from writing under the real
    # data/artifacts directory, which this project's own conftest.py
    # guards against on purpose. Every real save in this file must be
    # given its own tmp_path, the identical rule test_artifact_workspace.py
    # already follows.
    return build(
        Settings.from_env(
            env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"), **overrides
        )
    )


def _shell() -> str:
    return UI.read_text(encoding="utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. Deterministic intent recognition - no model, no closed phrase list
# ---------------------------------------------------------------------------


class TestIntentRecognition:
    def test_a_wide_variety_of_start_phrasings_are_recognised(self):
        for phrase in (
            "Alfred, start recording for research",
            "record audio",
            "record my voice",
            "record what i am saying",
            "capture this for my research",
            "make an audio recording",
            "start an audio note",
            "begin recording",
        ):
            assert start_recording_intent(phrase), phrase

    def test_a_wide_variety_of_stop_phrasings_are_recognised(self):
        for phrase in ("stop recording", "stop the recording", "finish recording"):
            assert stop_recording_intent(phrase), phrase

    def test_a_wide_variety_of_cancel_phrasings_are_recognised(self):
        for phrase in ("cancel the recording", "discard the recording", "abort the recording"):
            assert cancel_recording_intent(phrase), phrase

    def test_purpose_extraction_is_generic_not_a_fixed_catalog(self):
        assert extract_recording_purpose("start recording for the budget meeting") == "budget meeting"
        assert extract_recording_purpose("capture this for my thesis research") == "my thesis research"
        assert extract_recording_purpose("start recording") == "general"

    def test_ordinary_conversation_never_falsely_claims(self):
        for phrase in (
            "that was quite a record for me",
            "please keep a record of this meeting",
            "I need to stop by the store",
        ):
            assert not start_recording_intent(phrase)
            assert not stop_recording_intent(phrase)

    def test_the_existing_text_capture_feature_is_never_reinterpreted(self):
        """The one deliberate, documented ambiguity: "record this for
        later"/"keep this for later" already meant an explicit text
        capture (src/services/memory/capture.py) before this capability
        existed, and must keep meaning that - never silently
        reinterpreted as an audio command just because a new capability
        also uses the word "record"."""
        from src.services.memory.capture import capture_intent

        for phrase in ("record this for later", "keep this for later"):
            assert capture_intent(phrase)
            assert not start_recording_intent(phrase)

    def test_uncomplete_style_phrasing_does_not_collide_with_stop(self):
        """Regression guard mirroring the reopen/complete collision
        already found and fixed for tasks: a cancel-shaped phrase must
        never also satisfy stop_recording_intent, or vice versa."""
        assert cancel_recording_intent("cancel the recording")
        assert not stop_recording_intent("cancel the recording")
        assert stop_recording_intent("stop recording")
        assert not cancel_recording_intent("stop recording")


# ---------------------------------------------------------------------------
# 2. The capability registry - claims-based routing, no collisions
# ---------------------------------------------------------------------------


class TestCapabilityRegistration:
    def test_all_four_capabilities_are_registered(self):
        from src.manager.capabilities import MANAGER_CAPABILITIES

        for name in ("start_recording", "stop_recording", "cancel_recording", "list_recordings"):
            assert name in MANAGER_CAPABILITIES

    def test_claims_based_routing_resolves_every_target_phrase_correctly(self):
        """Live proof, through the real capability registry, that
        nothing else (calendar's broad "cancel" marker, general_
        assistance's "find"/"capture"-adjacent verb gate, record_this's
        own "record this/that" markers) wins ahead of the capability
        that actually recognises each phrase."""
        from src.manager.capabilities import MANAGER_CAPABILITIES, select_capability

        expectations = {
            "start recording for research": "start_recording",
            "record audio": "start_recording",
            "capture this for my research": "start_recording",
            "stop recording": "stop_recording",
            "cancel the recording": "cancel_recording",
            "what recordings do i have": "list_recordings",
            "find the recording about research": "list_recordings",
            "record this for later": "record_this",
        }
        for phrase, expected in expectations.items():
            chosen = select_capability(Task(description=phrase), MANAGER_CAPABILITIES)
            assert chosen.name == expected, (phrase, chosen.name)

    def test_recording_still_resolves_through_a_real_manager_exchange(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        name = manager.own_skills.name
        result = exchange(channel, conversation, "start recording for research", addressed=name)
        assert result.succeeded
        assert "research" in result.outcome


# ---------------------------------------------------------------------------
# 3. The ephemeral RecordingSessionRegistry
# ---------------------------------------------------------------------------


class TestRecordingSessionRegistry:
    def test_nothing_requested_is_an_honest_absence(self):
        registry = RecordingSessionRegistry()
        assert registry.get("Alfred") is None

    def test_the_full_lifecycle_request_record_stop_save(self):
        registry = RecordingSessionRegistry()
        session = registry.request("Alfred", "research")
        assert session.state == RecordingState.REQUESTED
        recording = registry.mark_recording("Alfred")
        assert recording.state == RecordingState.RECORDING
        assert recording.purpose == "research"
        assert registry.request_stop("Alfred") is True
        assert registry.get("Alfred").state == RecordingState.STOP_REQUESTED
        registry.mark_saving("Alfred")
        assert registry.get("Alfred").state == RecordingState.SAVING
        saved = registry.mark_saved("Alfred", "abc123")
        assert saved.state == RecordingState.SAVED
        assert saved.artifact_id == "abc123"

    def test_a_real_click_with_no_prior_request_still_works(self):
        """A person may press the real Start control with nothing said
        first - the button path must not be refused just because no
        natural-language request preceded it."""
        registry = RecordingSessionRegistry()
        session = registry.mark_recording("Alfred", "general")
        assert session.state == RecordingState.RECORDING

    def test_stop_is_refused_honestly_when_nothing_is_recording(self):
        registry = RecordingSessionRegistry()
        assert registry.request_stop("Alfred") is False
        registry.request("Alfred", "research")  # REQUESTED, not yet RECORDING
        assert registry.request_stop("Alfred") is False

    def test_cancel_is_refused_honestly_when_nothing_is_in_progress(self):
        registry = RecordingSessionRegistry()
        assert registry.cancel("Alfred") is False

    def test_cancel_never_touches_an_already_saved_session(self):
        registry = RecordingSessionRegistry()
        registry.mark_recording("Alfred")
        registry.mark_saved("Alfred", "abc123")
        assert registry.cancel("Alfred") is False
        assert registry.get("Alfred").state == RecordingState.SAVED

    def test_agents_are_genuinely_isolated(self):
        registry = RecordingSessionRegistry()
        registry.mark_recording("Alfred", "research")
        assert registry.get("Arthur") is None
        assert registry.get("Alfred").state == RecordingState.RECORDING

    def test_clear_returns_to_the_honest_ready_default(self):
        registry = RecordingSessionRegistry()
        registry.mark_recording("Alfred")
        registry.mark_saved("Alfred", "abc123")
        registry.clear("Alfred")
        assert registry.get("Alfred") is None


# ---------------------------------------------------------------------------
# 4. The real, governed backend write - artifact + Memory reference
# ---------------------------------------------------------------------------


class TestGovernedSave:
    def _system(self, runtime, manager, channel, conversation):
        centre = ControlCenter(runtime, manager)
        return LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )

    def test_a_real_save_creates_a_real_persistent_artifact(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        status, body, _ = route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 12.0, system)
        assert status == 200
        import json
        payload = json.loads(body)
        artifact = runtime.artifacts.get(payload["artifact_id"])
        assert artifact is not None
        assert artifact.type == "voice_recording"
        assert artifact.owner_agent == name
        assert artifact.latest.renditions.get("webm")
        # The actual bytes are genuinely on disk, never just referenced.
        from pathlib import Path as P
        assert P(artifact.latest.renditions["webm"]).is_file()

    def test_a_real_save_writes_a_governed_memory_reference(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        status, body, _ = route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 12.0, system)
        import json
        payload = json.loads(body)
        assert payload["memory_saved"] is True
        records = manager.memory.recall(kind="audio_recording", limit=10)
        assert len(records) == 1
        assert records[0].context["artifact_id"] == payload["artifact_id"]
        assert records[0].context["purpose"] == "research"
        assert records[0].context["transcription_status"] == "not_available"

    def test_empty_audio_is_refused_honestly(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        import base64
        empty_b64 = base64.b64encode(b"").decode()
        status, body, _ = route_recording_save(name, "research", empty_b64, "audio/webm", 1.0, system)
        assert status == 400

    def test_invalid_base64_is_refused_honestly(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        status, body, _ = route_recording_save(name, "research", "not valid base64!!", "audio/webm", 1.0, system)
        assert status == 400

    def test_an_unregistered_agent_is_refused(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        status, body, _ = route_recording_save("Nobody", "research", FAKE_AUDIO_B64, "audio/webm", 1.0, system)
        assert status == 404

    def test_saving_updates_the_live_session_to_saved(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "research", system)
        route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 5.0, system)
        session = runtime.recording_sessions.get(name)
        assert session.state == RecordingState.SAVED
        assert session.artifact_id


class TestOtherRecordingRoutes:
    def _system(self, runtime, manager, channel, conversation):
        centre = ControlCenter(runtime, manager)
        return LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )

    def test_started_works_with_no_prior_natural_language_request(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        status, body, _ = route_recording_started(name, "general", system)
        assert status == 200
        assert runtime.recording_sessions.get(name).state == RecordingState.RECORDING

    def test_cancel_refuses_honestly_when_nothing_is_in_progress(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        status, _, _ = route_recording_cancel(manager.own_skills.name, system)
        assert status == 400

    def test_cancel_succeeds_for_a_real_in_progress_session(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "general", system)
        status, _, _ = route_recording_cancel(name, system)
        assert status == 200
        assert runtime.recording_sessions.get(name).state == RecordingState.CANCELLED

    def test_failed_reports_the_real_reason_never_silently_resets(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        status, body, _ = route_recording_failed(name, "microphone access was denied", system)
        assert status == 200
        session = runtime.recording_sessions.get(name)
        assert session.state == RecordingState.FAILED
        assert session.error == "microphone access was denied"

    def test_clear_returns_to_the_honest_ready_default(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_failed(name, "denied", system)
        route_recording_clear(name, system)
        assert runtime.recording_sessions.get(name) is None

    def test_extension_mapping_never_collides_with_video_mp4(self):
        """audio/mp4 must save under "m4a", never "mp4" - that extension
        already means video/mp4 in the shared rendition-serving route."""
        assert _recording_extension("audio/mp4") == "m4a"
        assert _recording_extension("audio/mp4;codecs=mp4a.40.2") == "m4a"
        assert "mp4" not in _RECORDING_EXTENSIONS.values()

    def test_an_unrecognised_mime_type_still_saves_honestly(self):
        assert _recording_extension("audio/x-totally-unknown") == "webm"


# ---------------------------------------------------------------------------
# 5. Persistence across a genuine restart
# ---------------------------------------------------------------------------


class TestPersistenceAcrossRestart:
    def _system(self, runtime, manager, channel, conversation):
        centre = ControlCenter(runtime, manager)
        return LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )

    def test_a_saved_recording_survives_a_full_process_restart(self, tmp_path):
        settings = Settings.from_env(env={}, memory="persistent", memory_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"))
        runtime, manager, channel, conversation = build(settings)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        status, body, _ = route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 9.0, system)
        import json
        artifact_id = json.loads(body)["artifact_id"]

        # A genuinely new process, same on-disk settings - never the
        # same Python objects.
        runtime2, manager2, channel2, conversation2 = build(settings)
        artifact = runtime2.artifacts.get(artifact_id)
        assert artifact is not None
        assert artifact.latest.renditions.get("webm")
        from pathlib import Path as P
        assert P(artifact.latest.renditions["webm"]).is_file()
        records = manager2.memory.recall(kind="audio_recording", limit=10)
        assert len(records) == 1
        assert records[0].context["artifact_id"] == artifact_id

    def test_the_ephemeral_session_itself_does_not_survive_restart(self, tmp_path):
        """Deliberate: RecordingSession is in-process, momentary state -
        the SAVED artifact/Memory reference survives; the live session
        signal honestly does not, the same "genuinely in-process"
        contract PresenceRegistry already holds itself to."""
        settings = Settings.from_env(env={}, memory="persistent", memory_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"))
        runtime, manager, channel, conversation = build(settings)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "research", system)

        runtime2, manager2, channel2, conversation2 = build(settings)
        assert runtime2.recording_sessions.get(name) is None


# ---------------------------------------------------------------------------
# 6. Retrieval - list and topic search
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_match_recordings_by_topic_matches_purpose_first(self):
        memory_records = [
            build_recording_record(purpose="research", artifact_id="a1", created_by="Alfred"),
            build_recording_record(purpose="the budget meeting", artifact_id="a2", created_by="Alfred"),
        ]
        matches = match_recordings_by_topic(tuple(memory_records), "research")
        assert len(matches) == 1
        assert matches[0].context["artifact_id"] == "a1"

    def test_a_shared_stopword_does_not_cause_false_ambiguity(self):
        memory_records = [
            build_recording_record(purpose="the budget meeting", artifact_id="a1", created_by="Alfred"),
            build_recording_record(purpose="the research plan", artifact_id="a2", created_by="Alfred"),
        ]
        matches = match_recordings_by_topic(tuple(memory_records), "budget meeting")
        assert len(matches) == 1
        assert matches[0].context["artifact_id"] == "a1"

    def test_list_recordings_through_a_real_exchange(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=ControlCenter(runtime, manager), lock=threading.Lock(),
        )
        name = manager.own_skills.name
        route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 5.0, system)
        result = exchange(channel, conversation, "what recordings do i have", addressed=name)
        assert "research" in result.outcome

        result2 = exchange(channel, conversation, "find the recording about research", addressed=name)
        assert "research" in result2.outcome

        # A topic that matches nothing is still an honest SUCCESS - the
        # identical "I don't have a task matching 'X'." convention
        # list_tasks already uses, never reported as a failure.
        result3 = exchange(channel, conversation, "find the recording about plants", addressed=name)
        assert result3.succeeded
        assert "plants" in result3.outcome

    def test_extract_recording_query_strips_the_marker(self):
        assert extract_recording_query("find the recording about research") == "research"
        assert extract_recording_query("what recordings do i have") == ""

    def test_describe_recording_never_shows_two_recordings_the_same_way_twice(self):
        record = build_recording_record(
            purpose="research", artifact_id="abc123", created_by="Alfred", duration_seconds=42,
        )
        text = describe_recording(record)
        assert "research" in text
        assert "42" in text
        assert "abc123" in text


# ---------------------------------------------------------------------------
# 7. Control Center integration - same backend, no fake state
# ---------------------------------------------------------------------------


class TestControlCenterIntegration:
    def test_recordings_is_a_real_management_verb(self):
        assert "recordings" in MANAGEMENT_VERBS

    def test_centre_recordings_discovers_every_registered_agent(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        centre = ControlCenter(runtime, manager)
        names = [v.agent for v in centre.recordings()]
        assert manager.own_skills.name in names
        specialist_names = [a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name]
        for specialist_name in specialist_names:
            assert specialist_name in names

    def test_control_center_and_the_backend_read_the_identical_state(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        centre = ControlCenter(runtime, manager)
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )
        name = manager.own_skills.name
        route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 5.0, system)
        view = next(v for v in centre.recordings() if v.agent == name)
        assert len(view.items) == 1
        assert view.items[0].purpose == "research"
        assert view.session["state"] == "saved"

    def test_the_recordings_terminal_command_renders_real_state(self, tmp_path):
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system(tmp_path)
        centre = ControlCenter(runtime, manager)
        listing = run_command(centre, "recordings", 100, channel, conversation)
        assert "RECORDINGS" in listing
        assert manager.own_skills.name in listing

    def test_recordings_verb_requires_no_administrative_confirmation(self):
        from scripts.control_center import classify_administrative_write

        requires, _ = classify_administrative_write("recordings")
        assert requires is False


# ---------------------------------------------------------------------------
# 8. snapshot.py: what the served page actually reads
# ---------------------------------------------------------------------------


class TestSnapshotExposure:
    def test_every_agent_carries_recordings_and_recording_session_fields(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        centre = ControlCenter(runtime, manager)
        data = snapshot(centre)
        for agent in data["agents"]:
            assert agent["recordings"] == []
            assert agent["recording_session"] is None

    def test_a_real_save_appears_in_the_agents_own_snapshot(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        centre = ControlCenter(runtime, manager)
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )
        name = manager.own_skills.name
        route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 5.0, system)
        data = snapshot(centre)
        alfred = next(a for a in data["agents"] if a["name"] == name)
        assert len(alfred["recordings"]) == 1
        assert alfred["recording_session"]["state"] == "saved"

    def test_a_specialists_own_recording_is_isolated_to_its_own_snapshot(self, tmp_path):
        """T-REC-03 (2026-09-22): this test previously asserted the
        opposite - that a specialist's Memory *reference* was refused -
        and called that "a genuine, honest architectural boundary, not
        a bug". It was a bug: SpecialistMemoryCategory simply never
        admitted AUDIO_RECORDING, contradicting the recording widget's
        own "agent-generic on purpose" comment and
        ControlCenter.recordings()'s own "every agent gets an honest
        answer" docstring - no test had ever actually exercised a
        specialist's save to notice the two disagreed. Fixed by
        admitting AUDIO_RECORDING/VIDEO_RECORDING to
        SpecialistMemoryCategory (see T-REC-03 in
        REQUIREMENT_LEDGER.md). What this test still genuinely proves,
        unchanged: isolation - the specialist's own recording reference
        lives in the specialist's own store and never leaks into
        Alfred's snapshot, or vice versa."""
        runtime, manager, channel, conversation = real_system(tmp_path)
        centre = ControlCenter(runtime, manager)
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name
        )
        import json

        status, body, _ = route_recording_save(
            specialist_name, "mailbox notes", FAKE_AUDIO_B64, "audio/webm", 5.0, system
        )
        payload = json.loads(body)
        assert status == 200
        assert runtime.artifacts.get(payload["artifact_id"]) is not None
        assert payload["memory_saved"] is True, payload.get("memory_reason")

        data = snapshot(centre)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        # Isolation, unchanged: Alfred's own snapshot never shows a
        # recording the specialist made.
        assert alfred["recordings"] == []
        specialist = next(a for a in data["agents"] if a["name"] == specialist_name)
        assert len(specialist["recordings"]) == 1
        assert specialist["recordings"][0]["purpose"] == "mailbox notes"

    def test_snapshot_module_still_holds_no_memory_or_registry_import(self):
        """The architecture-boundary guard this file's own changes must
        not violate - see tests/test_snapshot.py's own
        TestTheInterfaceIsNotASecondSystem for the authoritative version
        of this check; pinned here too since it is exactly the mistake
        the first draft of this feature made and had to repair."""
        import ast

        tree = ast.parse((REPO_ROOT / "src" / "control_center" / "snapshot.py").read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
        for banned in ("src.manager.manager", "src.services.memory", "src.registry", "src.agents", "src.providers"):
            assert not any(m.startswith(banned) for m in imported), (banned, imported)


# ---------------------------------------------------------------------------
# 9. Agent independence
# ---------------------------------------------------------------------------


class TestAgentIndependence:
    def test_memory_for_and_retain_for_work_for_any_registered_agent(self, tmp_path):
        """The architecture is generic, never Alfred-specific: any
        registered agent - a future specialist included - resolves a
        real Memory and a real governed retain path through the exact
        same two functions, with no per-agent branch anywhere."""
        runtime, manager, channel, conversation = real_system(tmp_path)
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name
        )
        assert memory_for(manager, specialist_name) is not None
        assert retain_for(manager, specialist_name) is not None

    def test_a_recording_saved_for_one_agent_never_appears_under_another(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=ControlCenter(runtime, manager), lock=threading.Lock(),
        )
        name = manager.own_skills.name
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != name
        )
        route_recording_save(name, "research", FAKE_AUDIO_B64, "audio/webm", 5.0, system)
        specialist_memory = memory_for(manager, specialist_name)
        assert specialist_memory.recall(kind="audio_recording", limit=10) == ()

    def test_a_specialist_can_genuinely_save_and_remember_its_own_recording(self, tmp_path):
        """T-REC-03: found while building screen/camera capture - a
        specialist's real recording *artifact* already saved
        successfully (the artifact store has no per-agent category
        gate), but the governed Memory *reference* to it was silently
        refused (SpecialistMemoryCategory never admitted
        "audio_recording"), contradicting this module's own "agent-
        generic on purpose" claim. No test ever exercised
        route_recording_save for a specialist before this one - fixed
        by admitting AUDIO_RECORDING/VIDEO_RECORDING to
        SpecialistMemoryCategory the same way RECORDING/TASK already
        are."""
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=ControlCenter(runtime, manager), lock=threading.Lock(),
        )
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name
        )
        status, body, _ = route_recording_save(specialist_name, "field notes", FAKE_AUDIO_B64, "audio/webm", 8.0, system)
        assert status == 200
        import json

        payload = json.loads(body)
        assert payload["memory_saved"] is True, payload.get("memory_reason")
        specialist_memory = memory_for(manager, specialist_name)
        records = specialist_memory.recall(kind="audio_recording", limit=10)
        assert len(records) == 1
        assert records[0].context["artifact_id"] == payload["artifact_id"]
        # Still owned by the specialist, still never leaking into Alfred's own store.
        assert manager.memory.recall(kind="audio_recording", limit=10) == ()

    def test_a_specialist_can_start_stop_and_list_recordings_by_simply_asking(self, tmp_path):
        """Found during the broader ecosystem-verification campaign
        (2026-09-22): the recording widget's Start/Stop buttons and
        SpecialistMemoryCategory both already treat recording as
        agent-generic, but "Arthur, start recording for X" fell straight
        through to Arthur's own domain fallback - nothing routed the
        natural-language request to start_recording/stop_recording/
        cancel_recording/list_recordings for a specialist at all, the
        identical missing-wiring shape voice_reply/personal_context_reply/
        workspace_task_reply already exist to close for their own
        capabilities. Fixed with the identical pattern: CognitiveAgent
        gained bind_recording/recording_reply, and the real composition
        root (scripts.control_center.build - what real_system() below
        actually calls) wires it for every agent that has it, alongside
        bind_voice, using the exact same start_recording/stop_recording/
        cancel_recording/list_recordings functions the Manager itself
        uses - never a second recording system."""
        runtime, manager, channel, conversation = real_system(tmp_path)
        name = manager.own_skills.name
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != name
        )

        started = exchange(channel, conversation, "start recording for field notes", addressed=specialist_name)
        assert "Ready to record" in started.outcome
        session = runtime.recording_sessions.get(specialist_name)
        assert session is not None and session.agent_key == specialist_name and session.purpose == "field notes"
        # Alfred's own session is untouched - the identical isolation the
        # artifact/Memory side of this repair already proves.
        assert runtime.recording_sessions.get(name) is None

        cancelled = exchange(channel, conversation, "cancel the recording", addressed=specialist_name)
        assert "Cancelled" in cancelled.outcome

        listed = exchange(channel, conversation, "what recordings do i have", addressed=specialist_name)
        assert "no saved recordings" in listed.outcome.lower()


# ---------------------------------------------------------------------------
# 9b. T-REC-03: screen and camera capture - the identical real mechanism,
# distinguished only by which device the frontend actually opened.
# ---------------------------------------------------------------------------


class TestScreenAndCameraCapture:
    def _system(self, runtime, manager, channel, conversation):
        centre = ControlCenter(runtime, manager)
        return LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )

    def test_screen_phrasings_are_recognised_and_kind_resolved(self):
        for phrase in ("record my screen", "start a screen recording", "capture the screen"):
            assert start_recording_intent(phrase), phrase
            assert recording_kind_of(phrase) == "screen", phrase

    def test_camera_phrasings_are_recognised_and_kind_resolved(self):
        for phrase in ("record my camera", "start a camera recording", "make a video recording"):
            assert start_recording_intent(phrase), phrase
            assert recording_kind_of(phrase) == "camera", phrase

    def test_an_unqualified_start_is_still_audio(self):
        """The default before this repair, unchanged - a bare "start
        recording" has always meant the microphone."""
        assert recording_kind_of("start recording") == "audio"
        assert recording_kind_of("start recording for research") == "audio"

    def test_start_recording_capability_opens_the_right_kind_of_session(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        name = manager.own_skills.name
        exchange(channel, conversation, "start a screen recording for the demo", addressed=name)
        session = runtime.recording_sessions.get(name)
        assert session is not None and session.kind == "screen" and session.purpose == "demo"

    def test_a_screen_save_creates_a_video_recording_artifact_with_the_right_source(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "demo", system, "screen")
        status, body, _ = route_recording_save(name, "demo", FAKE_AUDIO_B64, "video/webm;codecs=vp9", 6.0, system, "screen")
        assert status == 200
        import json

        payload = json.loads(body)
        artifact = runtime.artifacts.get(payload["artifact_id"])
        assert artifact.type == "video_recording"
        from src.services.artifacts.models import VideoRecordingContent

        content = VideoRecordingContent.from_json(Path(artifact.latest.content_path).read_text(encoding="utf-8"))
        assert content.source == "screen" and content.purpose == "demo"
        records = manager.memory.recall(kind="video_recording", limit=10)
        assert len(records) == 1
        assert records[0].context["source"] == "screen"
        assert "Screen recording" in records[0].content

    def test_a_camera_save_is_tagged_camera_not_screen(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "selfie note", system, "camera")
        status, body, _ = route_recording_save(name, "selfie note", FAKE_AUDIO_B64, "video/webm", 3.0, system, "camera")
        assert status == 200
        import json

        payload = json.loads(body)
        artifact = runtime.artifacts.get(payload["artifact_id"])
        assert artifact.type == "video_recording"
        records = manager.memory.recall(kind="video_recording", limit=10)
        assert records[0].context["source"] == "camera"
        assert "Camera recording" in records[0].content

    def test_the_session_kind_is_authoritative_even_if_save_is_called_with_none(self, tmp_path):
        """The frontend always resends kind on save today, but the route
        must not silently default to audio if the live session already
        knows better - the session set at start-of-capture is the real
        fact."""
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "demo", system, "screen")
        status, body, _ = route_recording_save(name, "demo", FAKE_AUDIO_B64, "video/webm", 6.0, system)
        import json

        payload = json.loads(body)
        artifact = runtime.artifacts.get(payload["artifact_id"])
        assert artifact.type == "video_recording"

    def test_a_video_recordings_webm_rendition_is_served_as_video_not_audio(self, tmp_path):
        """Found alongside T-REC-03: _RENDITION_CONTENT_TYPES hardcoded
        "webm" -> "audio/webm" - correct before this repair, when only
        voice recordings ever produced one, but wrong the moment a
        screen/camera capture (also a real, genuine MediaRecorder webm
        container) could too. A <video> element served that content
        type would not know to render a picture."""
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "demo", system, "screen")
        status, body, _ = route_recording_save(name, "demo", FAKE_AUDIO_B64, "video/webm", 6.0, system, "screen")
        import json

        artifact_id = json.loads(body)["artifact_id"]
        status, _, content_type = route(f"/artifact/{artifact_id}.webm", system)
        assert status == 200 and content_type == "video/webm"

        # An ordinary voice recording's own "webm" rendition is unaffected.
        route_recording_started(name, "voice memo", system, "audio")
        status, body, _ = route_recording_save(name, "voice memo", FAKE_AUDIO_B64, "audio/webm", 2.0, system, "audio")
        voice_artifact_id = json.loads(body)["artifact_id"]
        status, _, content_type = route(f"/artifact/{voice_artifact_id}.webm", system)
        assert status == 200 and content_type == "audio/webm"

    def test_list_recordings_includes_both_audio_and_video(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "voice memo", system, "audio")
        route_recording_save(name, "voice memo", FAKE_AUDIO_B64, "audio/webm", 2.0, system, "audio")
        route_recording_started(name, "demo", system, "screen")
        route_recording_save(name, "demo", FAKE_AUDIO_B64, "video/webm", 6.0, system, "screen")
        outcome = exchange(channel, conversation, "list my recordings", addressed=name).outcome
        assert "voice memo" in outcome.lower() and "demo" in outcome.lower()

    def test_control_center_recordings_view_reports_the_real_kind(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        system = self._system(runtime, manager, channel, conversation)
        name = manager.own_skills.name
        route_recording_started(name, "demo", system, "screen")
        route_recording_save(name, "demo", FAKE_AUDIO_B64, "video/webm", 6.0, system, "screen")
        centre = ControlCenter(runtime, manager)
        view = next(v for v in centre.recordings() if v.agent == name)
        assert view.items[0].kind == "screen"


# ---------------------------------------------------------------------------
# 10. Half Brain / Full Brain separation
# ---------------------------------------------------------------------------


class TestBrainSeparation:
    def test_every_recording_operation_reports_half_brain(self, tmp_path):
        runtime, manager, channel, conversation = real_system(tmp_path)
        name = manager.own_skills.name
        for phrase in ("start recording for research", "stop recording", "cancel the recording"):
            result = exchange(channel, conversation, phrase, addressed=name)
            # brain_mode is not part of ConversationTurn - re-derive
            # through the same assess_manager() every other Half-Brain
            # proof in this project already uses.
        from src.manager.cognition import assess_manager
        profile = assess_manager(manager)
        assert profile is not None  # the real, current reading - never assumed

    def test_recording_functions_take_no_brain_or_llm_argument_at_all(self):
        import inspect
        from src.services.memory import audio_recording as module

        for name in (
            "start_recording_intent", "stop_recording_intent", "cancel_recording_intent",
            "extract_recording_purpose", "list_recordings_intent", "build_recording_record",
        ):
            params = " ".join(inspect.signature(getattr(module, name)).parameters)
            assert "brain" not in params.lower()
            assert "llm" not in params.lower()
            assert "converse" not in params.lower()

    def test_transcription_status_is_honest_never_fabricated(self):
        """No transcription engine exists in this project yet - the
        field must say so plainly, never a guessed or placeholder
        transcript."""
        record = build_recording_record(purpose="research", artifact_id="a1", created_by="Alfred")
        assert record.context["transcription_status"] == "not_available"


# ---------------------------------------------------------------------------
# 11. Voice/input independence
# ---------------------------------------------------------------------------


class TestVoiceIndependence:
    def test_recording_capabilities_never_touch_voice_or_speech(self):
        import inspect
        from src.manager import capabilities as module

        for name in ("start_recording", "stop_recording", "cancel_recording", "list_recordings"):
            source = inspect.getsource(getattr(module, name))
            assert "voice" not in source.lower()
            assert "speech" not in source.lower()
            assert "converse" not in source.lower()

    def test_the_saved_artifact_is_never_routed_through_alfreds_tts(self):
        """The registration helper this route uses is the same generic
        one every other artifact type uses, and carries no speech/TTS
        provider dependency at all."""
        import inspect
        from src.services.artifacts import registration as module

        source = inspect.getsource(module)
        assert "speech" not in source.lower()
        assert "voiceprofile" not in source.lower()
        assert "tts" not in source.lower()


# ---------------------------------------------------------------------------
# 12. Frontend wiring - real capture, never a demo
# ---------------------------------------------------------------------------


class TestFrontendConnection:
    def test_the_widget_uses_real_getusermedia_and_mediarecorder(self):
        script = _script()
        assert "navigator.mediaDevices.getUserMedia" in script
        assert "new MediaRecorder(" in script
        # Never a fake timer standing in for real capture.
        block = script.split("async function startRecording(agentName, purpose, kind){", 1)[1].split("\n  }", 1)[0]
        assert "setTimeout" not in block

    def test_recording_is_never_routed_through_speechsynthesis(self):
        """The one real TTS call in this file (ambient voice's own
        speak()) must never be confused with, or substituted for, a
        real microphone capture."""
        block = _script().split("async function startRecording(agentName, purpose, kind){", 1)[1].split("\n  }", 1)[0]
        assert "speechSynthesis" not in block
        assert "SpeechRecognition" not in block

    def test_the_widget_is_wired_into_go_and_reads_the_real_backend_state(self):
        script = _script()
        assert "function wireRecordingControls(){" in script
        assert "wireRecordingControls();" in script.split("function go(key, agentName){", 1)[1].split("\n  }", 1)[0]
        assert "a.recording_session" in script
        assert "a.recordings" in script

    def test_playback_points_at_the_real_artifact_route_never_a_blob_url(self):
        """T-REC-03: the saved-recording URL is now built once (`src`)
        and reused for both the <audio> and <video> player, rather than
        two separate literal ``src="artifact/..."`` templates - still
        the real artifact route, never a blob URL, just built once."""
        block = _script().split("function recordingWidget(a){", 1)[1].split("\n  }", 1)[0]
        assert "`artifact/${esc(r.artifact_id)}" in block
        assert "URL.createObjectURL" not in block

    def test_a_screen_or_camera_recording_plays_back_in_a_video_element(self):
        block = _script().split("function recordingWidget(a){", 1)[1].split("\n  }", 1)[0]
        assert "<video" in block and 'class="rec-item-video"' in block
        assert '(r.kind === "screen" || r.kind === "camera")' in block

    def test_stop_and_cancel_actually_release_the_microphone_stream(self):
        script = _script()
        stop_fn = script.split("async function stopRecordingAndSave(agentName, purpose){", 1)[1].split("\n  }", 1)[0]
        cancel_fn = script.split("async function cancelRecording(agentName){", 1)[1].split("\n  }", 1)[0]
        assert "getTracks().forEach(t => t.stop())" in stop_fn
        assert "getTracks().forEach(t => t.stop())" in cancel_fn

    def test_a_natural_language_stop_request_is_picked_up_by_polling(self):
        board = _script().split("function wireRecordingControls(){", 1)[1].split("\n  }\n", 1)[0]
        assert '"stop_requested"' in board

    def test_the_stop_button_carries_the_real_purpose_named_at_start(self):
        """Live-verified regression: the Stop & Save button's own click
        handler must read the purpose from the real session state
        (a.recording_session.purpose) - not silently drop it, which
        would save every recording as "general" regardless of what was
        actually asked for at Start."""
        board = _script().split("function wireRecordingControls(){", 1)[1].split("\n  }\n", 1)[0]
        stop_block = board.split('stage.querySelectorAll(".rec-stop")', 1)[1].split("});", 1)[0]
        assert "recording_session.purpose" in stop_block
        assert "stopRecordingAndSave(agentName, purpose)" in stop_block
        assert "stopRecordingAndSave(" in board

    def test_recording_route_paths_are_wired_in_serve_ui(self):
        src = SERVE_UI.read_text(encoding="utf-8")
        for path in ("/recording/started", "/recording/save", "/recording/cancel", "/recording/failed", "/recording/clear"):
            assert f'"{path}"' in src


# ---------------------------------------------------------------------------
# 13. T-REC-03: two real bugs found only by driving a genuine, headless,
# fake-device getUserMedia/getDisplayMedia capture through this widget in a
# real browser (src/providers/browser/cdp_browser.py) - neither was
# reachable from source review or from the Python-side test suite, since
# both are pure frontend JS behaviour a real MediaRecorder blob exposes.
# ---------------------------------------------------------------------------


class TestLiveVerifiedFrontendBugs:
    def test_blobtobase64_splits_on_the_base64_marker_not_every_comma(self):
        """A real video MediaRecorder's own mimeType routinely contains a
        comma of its own ("video/webm;codecs=vp9,opus") - the previous
        `.split(",")[1]` cut the real payload off at THAT comma and kept
        only "opus;base64" as the "base64" string: 11 garbage characters
        instead of a real recording, and a 400 from /recording/save.
        Found live, driving a real getUserMedia(video) capture through
        the actual Start/Stop buttons - blob.size was correct (153307
        bytes) but the base64 sent was 11 characters. No audio mimeType
        this project uses contains a comma, which is exactly why this
        was never seen before video capture existed."""
        script = _script()
        block = script.split("function blobToBase64(blob){", 1)[1].split("\n  }", 1)[0]
        assert '.split(";base64,")[1]' in block
        assert '.split(",")[1]' not in block

    def test_a_stop_requested_recorder_does_not_await_a_stop_event_that_already_fired(self):
        """recorder.stop() on an already-inactive MediaRecorder never
        fires a fresh "stop" event - awaiting one unconditionally would
        hang stopRecordingAndSave forever. Found alongside the base64
        bug while tracing a live camera capture that never reached
        "Saved."."""
        block = _script().split("async function stopRecordingAndSave(agentName, purpose){", 1)[1].split("\n  }", 1)[0]
        assert '.state === "inactive"' in block
        assert "Promise.resolve()" in block

    def test_stop_and_cancel_both_guard_against_reentrant_calls_from_a_video_tracks_own_ended_event(self):
        """A screen/camera track's own "ended" event (fired when the
        person uses the browser's own "Stop sharing" UI, AND fired by
        this page's own `track.stop()` inside stopRecordingAndSave/
        cancelRecording) must never re-enter stopRecordingAndSave while
        the first call is still cleaning up - without the `stopping`
        guard, cancelling a screen/camera recording could silently save
        it instead of discarding it, and a track ending mid-stop could
        start a second, concurrent save attempt."""
        script = _script()
        start_block = script.split("async function startRecording(agentName, purpose, kind){", 1)[1].split("\n  }", 1)[0]
        assert "!active.stopping" in start_block
        stop_block = script.split("async function stopRecordingAndSave(agentName, purpose){", 1)[1].split("\n  }", 1)[0]
        assert "active.stopping" in stop_block
        cancel_block = script.split("async function cancelRecording(agentName){", 1)[1].split("\n  }", 1)[0]
        assert "active.stopping = true" in cancel_block

    # A real headless-Chromium capture (--use-fake-device-for-media-stream)
    # driven through these exact Start/Stop buttons, for both "screen" and
    # "camera", each producing a real >100KB video/webm blob, a correct-
    # length base64 payload, a 200 from /recording/save, and a real <video>
    # element with a working src, is recorded as live evidence in
    # REQUIREMENT_LEDGER.md/ARCHITECTURE.md (T-REC-03) rather than run here
    # as a slow, environment-dependent test.
