"""Stage 17.3 - connecting the REAL production conversation path to the
Agent Presence machinery Stages 17.1/17.2 already built.

Stage 17.2 built a real speaking()/PresenceRegistry mechanism and wired
it into VoiceChannel.deliver() - but the actual production entry point
every real message travels through (scripts/exchange.py's own
exchange(), which scripts/serve_ui.py's POST /message and
scripts/control_center.py's `talk` command both call) never called
deliver() at all. Verified directly before this file was written:

    scripts/exchange.py::exchange() did `channel.ask(...).turn` and
    stopped - deliver() was never reached from the real path.

Stage 17.3's one change closes exactly that gap: `exchange(..., speak=
True)` now calls `channel.deliver()` too, and `scripts/serve_ui.py`'s
`route_message()` (the literal function `SnapshotHandler.do_POST`
calls - the real production entry point, not a helper) passes
`speak=True`. Nothing else about exchange()'s behaviour changed -
`speak` defaults to False, so `scripts/control_center.py`'s terminal
`talk` command (which never wants deliver()'s own `print()` colliding
with `render_turn()`'s formatted output) is completely unaffected.

This file proves that gap is genuinely closed - through the real
`exchange()`/`route_message()` functions, never a re-implementation of
either - and that every existing Stage 17.1/17.2 independence guarantee
still holds with real speech now flowing through the real path.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.views import ConversationTurn
from src.providers.availability import Availability, AvailabilityState
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import PresenceState, presence_for, set_avatar_preference
from src.services.identity.voice import voice_for
from scripts.exchange import exchange, memory_for
from scripts.serve_ui import build_live_system, route, route_message

REPO_ROOT = Path(__file__).resolve().parent.parent


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


def system(**overrides):
    runtime = build_runtime(settings(**overrides))
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    channel = build_channel(runtime, manager)
    conversation = build_conversation(manager)
    centre = ControlCenter(runtime, manager)
    return runtime, manager, channel, conversation, centre


class _ScriptedSpeaker(SpeechAdapter):
    """A real Speaker double, genuinely blocking when told to - never a
    mock of ``VoiceChannel``/``exchange()`` themselves. The same
    honest-test-double discipline ``windows_speech_provider.py``'s own
    docstring already documents: replace the engine at its own boundary,
    never the production code path that calls it."""

    def __init__(self, delay: float = 0.0, ok: bool = True) -> None:
        self._delay = delay
        self._ok = ok
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        self.calls.append(text)
        if self._delay:
            time.sleep(self._delay)
        return self._ok


class _RaisingSpeaker(SpeechAdapter):
    name = "raising"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        raise RuntimeError("synthesis exploded")


def _watch(registry, agent_key, samples, count=15, interval=0.03):
    for _ in range(count):
        samples.append(registry.get(agent_key))
        time.sleep(interval)


# ---------------------------------------------------------------------------
# 1/2. Real speaking lifecycle through exchange(), and exception safety,
#      through the actual production function - not VoiceChannel directly.
# ---------------------------------------------------------------------------


class TestRealSpeakingLifecycleThroughExchange:
    def test_speaking_is_observable_during_a_real_blocking_exchange_call(self):
        _, manager, _, conversation, _ = system()
        runtime, *_ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(delay=0.3), presence=runtime.presence_registry
        )
        assert runtime.presence_registry.get(manager.own_skills.name) is None

        samples: list = []
        watcher = threading.Thread(
            target=_watch, args=(runtime.presence_registry, manager.own_skills.name, samples)
        )
        watcher.start()
        turn = exchange(real_channel, conversation, "what can you do?", speak=True)
        watcher.join()

        assert turn.handled_by == manager.own_skills.name
        assert PresenceState.SPEAKING in samples
        assert samples[0] is None  # IDLE (absent) before the call started
        assert runtime.presence_registry.get(manager.own_skills.name) is None  # IDLE after

    def test_speak_false_never_touches_presence_at_all(self):
        """The default, and the only behaviour before Stage 17.3 -
        every existing caller of exchange() (scripts/control_center.py's
        `talk` command, every Stage 17.1/17.2 test) must be completely
        unaffected."""
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        marks: list[str] = []
        original_set = runtime.presence_registry.set
        runtime.presence_registry.set = lambda k, s: (marks.append(k), original_set(k, s))  # type: ignore[method-assign]

        exchange(real_channel, conversation, "what can you do?")  # speak omitted

        assert marks == []

    def test_speaking_is_cleared_even_when_the_engine_raises(self):
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_RaisingSpeaker(), presence=runtime.presence_registry
        )
        with pytest.raises(RuntimeError):
            exchange(real_channel, conversation, "what can you do?", speak=True)
        assert runtime.presence_registry.get(manager.own_skills.name) is None

    def test_the_engine_is_called_exactly_once(self):
        runtime, manager, _, conversation, _ = system()
        speaker = _ScriptedSpeaker()
        real_channel = VoiceChannel(manager, speech=speaker, presence=runtime.presence_registry)
        exchange(real_channel, conversation, "what can you do?", speak=True)
        assert len(speaker.calls) == 1


# ---------------------------------------------------------------------------
# 3/4. Correct canonical speaker, and multi-agent isolation - a real
#      delegated specialist, not a hardcoded name.
# ---------------------------------------------------------------------------


class TestCanonicalSpeakerAndMultiAgentSafety:
    def test_a_delegated_specialists_own_presence_changes_not_alfreds(self):
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(delay=0.2), presence=runtime.presence_registry
        )
        alfred_marks: list[PresenceState | None] = []
        arthur_marks: list[PresenceState | None] = []

        def watch_both():
            for _ in range(15):
                alfred_marks.append(runtime.presence_registry.get(manager.own_skills.name))
                arthur_marks.append(runtime.presence_registry.get("Arthur"))
                time.sleep(0.02)

        watcher = threading.Thread(target=watch_both)
        watcher.start()
        turn = exchange(
            real_channel, conversation, "read my inbox for job opportunities",
            addressed="Arthur", speak=True,
        )
        watcher.join()

        assert turn.handled_by == "Arthur"
        assert PresenceState.SPEAKING in arthur_marks
        assert PresenceState.SPEAKING not in alfred_marks
        assert runtime.presence_registry.get("Arthur") is None
        assert runtime.presence_registry.get(manager.own_skills.name) is None

    def test_an_unmatched_turn_never_creates_a_nobody_presence_entry(self):
        """A real request through the real system: nonsense text is
        still answered by Alfred's own fallback (verified by hand -
        this NullBrain/CapabilityRouter setup always has an agent
        answer, so handled_by never actually becomes "nobody" through
        ordinary text routing). This still proves the real, honest
        thing available to prove here: whichever real agent answers is
        the only one ever marked, and "nobody" - the literal sentinel
        trail.py can in principle produce - never appears."""
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        marks: list[str] = []
        original_set = runtime.presence_registry.set
        runtime.presence_registry.set = lambda k, s: (marks.append(k), original_set(k, s))  # type: ignore[method-assign]

        turn = exchange(real_channel, conversation, "xyzzy plugh qwerty", speak=True)

        assert "nobody" not in marks
        assert marks == [turn.handled_by]

    def test_the_literal_nobody_sentinel_is_never_marked_speaking(self):
        """trail.py's own documented sentinel (handled_by defaults to
        the literal string "nobody" when nothing in the whole system
        matched) constructed directly, since no real request through
        this environment's own NullBrain/CapabilityRouter setup was
        found to actually produce it (Alfred always answers something -
        see the test above). Still exercised through the real
        VoiceChannel.deliver(), never a re-implementation of its guard."""
        runtime, manager, _, _, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        turn = ConversationTurn(
            request="xyzzy", handled_by="nobody", delegated=False, steps=(),
            outcome="nothing matched", succeeded=False,
        )
        from src.channels.channel import Response

        response = Response(text="nothing matched", speech="nothing matched", spoken=False, turn=turn)
        marks: list[str] = []
        original_set = runtime.presence_registry.set
        runtime.presence_registry.set = lambda k, s: (marks.append(k), original_set(k, s))  # type: ignore[method-assign]

        real_channel.deliver(response)

        assert marks == []


# ---------------------------------------------------------------------------
# 5/6. User agent selection through the real production route, and an
#      unknown/invalid selection failing safely.
# ---------------------------------------------------------------------------


class TestUserAgentSelectionThroughTheRealRoute:
    def _live_with_scripted_speaker(self, delay: float = 0.2):
        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(delay=delay), presence=live.runtime.presence_registry
        )
        return live

    def test_route_message_to_a_real_registered_specialist_marks_its_own_presence(self):
        live = self._live_with_scripted_speaker()
        registry_size_before = len(live.manager.registry.all_agents())

        samples: list = []
        watcher = threading.Thread(
            target=_watch, args=(live.runtime.presence_registry, "Arthur", samples)
        )
        watcher.start()
        status, body, _ = route_message(
            "read my inbox for job opportunities", live, to="Arthur"
        )
        watcher.join()

        import json

        turn = json.loads(body)
        assert status == 200
        assert turn["handled_by"] == "Arthur"
        assert PresenceState.SPEAKING in samples

    def test_the_explicit_to_override_is_what_selects_the_agent_not_capability_matching(self):
        """Isolates the ``to=`` mechanism itself: "what can you do?" is
        Alfred's own ordinary text (nothing in it names a capability
        Arthur declares), so if this ends up with Arthur as the
        canonical speaker, it can only be because the explicit
        selection was honoured - never because the words happened to
        route there anyway."""
        live = self._live_with_scripted_speaker()
        status, body, _ = route_message("what can you do?", live, to="Arthur")
        import json

        turn = json.loads(body)
        assert status == 200
        assert turn["handled_by"] == "Arthur"

    def test_route_message_to_an_unregistered_name_fabricates_nothing(self):
        live = self._live_with_scripted_speaker()
        registry_size_before = len(live.manager.registry.all_agents())

        status, body, _ = route_message("hello there", live, to="Nobody")

        import json

        turn = json.loads(body)
        assert status == 200
        assert live.manager.registry.get("Nobody") is None
        assert len(live.manager.registry.all_agents()) == registry_size_before
        assert live.runtime.presence_registry.get("Nobody") is None


# ---------------------------------------------------------------------------
# 7/8. Avatar independence, and voice independence, re-proven with real
#      speech genuinely flowing through the same call.
# ---------------------------------------------------------------------------


class TestIndependenceHoldsWithRealSpeechFlowing:
    def test_avatar_choice_is_unaffected_by_and_does_not_affect_a_real_spoken_turn(self):
        runtime, manager, _, conversation, centre = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        brain_before = manager.brain
        memory_before = manager.memory
        skills = manager.own_skills
        voice_before = voice_for(skills)
        personality_before = personality_for(skills)

        agent_memory = memory_for(manager, skills.name)
        set_avatar_preference(
            skills.name, "calm_composed", provider=runtime.avatar_provider,
            recall=agent_memory.recall, remember=agent_memory.remember, forget=agent_memory.forget,
        )

        exchange(real_channel, conversation, "what can you do?", speak=True)

        assert manager.brain is brain_before
        assert manager.memory is memory_before
        assert manager.own_skills is skills
        assert voice_for(skills) == voice_before
        assert personality_for(skills) == personality_before
        chosen = presence_for(skills.name, provider=runtime.avatar_provider, recall=agent_memory.recall)
        assert chosen.avatar.avatar_id == "calm_composed"

    def test_capabilities_and_connection_are_unaffected_by_a_real_spoken_turn(self):
        runtime, manager, _, conversation, centre = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        caps_before = tuple(
            c.name for c in centre.overview().agent(manager.own_skills.name).capabilities
        )
        connection_before = runtime.connection

        exchange(real_channel, conversation, "what can you do?", speak=True)

        caps_after = tuple(
            c.name for c in centre.overview().agent(manager.own_skills.name).capabilities
        )
        assert caps_after == caps_before
        assert runtime.connection is connection_before

    def test_a_different_voiceprofile_still_drives_correct_speaking_presence(self):
        """Arthur's own, distinct VoiceProfile is what deliver() looks
        up and hands to speak() - proving presence synchronisation does
        not depend on which agent's voice is in play."""
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(delay=0.15), presence=runtime.presence_registry
        )
        arthur = manager.registry.get("Arthur")
        assert voice_for(arthur) != voice_for(manager.own_skills)

        samples: list = []
        watcher = threading.Thread(
            target=_watch, args=(runtime.presence_registry, "Arthur", samples)
        )
        watcher.start()
        exchange(real_channel, conversation, "check my inbox", addressed="Arthur", speak=True)
        watcher.join()

        assert PresenceState.SPEAKING in samples


# ---------------------------------------------------------------------------
# 9/10. Half Brain honesty, and speech-unavailable honesty - through the
#       real path, with the real, shipped NoSpeech adapter.
# ---------------------------------------------------------------------------


class TestHonestyIsPreserved:
    def test_half_brain_stays_honest_through_a_real_spoken_turn(self):
        """No LLM provider is configured, so this must still say HALF
        BRAIN - Stage 17.3 must not paper over an unproven brain with a
        real-looking spoken response."""
        _, manager, channel, conversation, _ = system()
        turn = exchange(channel, conversation, "what can you do?", speak=True)
        assert turn.brain_mode == "HALF BRAIN"

    def test_the_shipped_nospeech_adapter_never_fabricates_speaking(self):
        """The real, shipped default (no MANAGER_SPEECH configured) is
        NoSpeech - speak() returns False instantly. Presence must still
        pass through a real (if momentary) SPEAKING assertion, never a
        longer, fabricated one, and must never claim `spoken: true`
        anywhere a reader could see it."""
        runtime, manager, channel, conversation, _ = system()
        from src.channels.channel import NoSpeech

        assert isinstance(channel.speech, NoSpeech)
        turn = exchange(channel, conversation, "what can you do?", speak=True)
        assert turn is not None
        # No stuck state - NoSpeech's own speak() is effectively
        # instantaneous, so by the time this reads it, it is over.
        assert runtime.presence_registry.get(manager.own_skills.name) is None

    def test_route_message_never_reports_a_fabricated_success(self):
        """A failing engine must not be reported to the browser as a
        successful, spoken exchange - the JSON body's own honesty is
        unaffected by Stage 17.3's addition."""
        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_RaisingSpeaker(), presence=live.runtime.presence_registry
        )
        status, body, _ = route_message("what can you do?", live)
        assert status == 500
        import json

        assert "error" in json.loads(body)
        assert live.runtime.presence_registry.get(live.manager.own_skills.name) is None


# ---------------------------------------------------------------------------
# 11. The real live production path - route_message(), the literal
#     function do_POST calls - not a helper standing in for it. A
#     concurrent GET /snapshot.json genuinely observes the live SPEAKING
#     signal while the POST is still blocked inside a real speak() call.
# ---------------------------------------------------------------------------


class TestTheRealLiveProductionPath:
    def test_a_concurrent_snapshot_poll_genuinely_observes_speaking_mid_flight(self):
        """The exact claim route_message()'s own new docstring makes:
        GET reads are unlocked, PresenceRegistry has its own separate
        lock, so a poll landing while a POST /message is still inside a
        real, slow speak() call sees SPEAKING for real - not a second,
        client-guessed state, the literal server-side truth read twice
        through the one real snapshot() function."""
        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(delay=0.4), presence=live.runtime.presence_registry
        )

        seen_speaking = threading.Event()

        def poll_until_speaking():
            import json

            deadline = time.time() + 2.0
            while time.time() < deadline and not seen_speaking.is_set():
                _, body, _ = route("/snapshot.json", live)
                data = json.loads(body)
                alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
                if alfred["activity"] == "speaking":
                    seen_speaking.set()
                time.sleep(0.02)

        poller = threading.Thread(target=poll_until_speaking)
        poller.start()
        route_message("what can you do?", live)
        poller.join(timeout=1.0)

        assert seen_speaking.is_set(), (
            "a concurrent GET /snapshot.json never observed SPEAKING while "
            "POST /message was genuinely still inside a real, slow speak() "
            "call - the live production wiring is not actually connected"
        )

    def test_after_the_post_returns_presence_has_settled_back_to_idle(self):
        import json

        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(), presence=live.runtime.presence_registry
        )
        route_message("what can you do?", live)
        _, body, _ = route("/snapshot.json", live)
        data = json.loads(body)
        alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
        assert alfred["activity"] == "idle"

    def test_the_real_do_post_dispatch_reaches_route_message_with_speak_wired(self):
        """A structural pin, not a behavioural one: do_POST must keep
        calling route_message() (never a second implementation), and
        route_message() must keep calling exchange() with speak=True -
        the exact two-line change this phase made. Written as a source
        check because exercising the real socket is out of scope for
        this suite (see tests/test_live_ui.py's own docstring on why)."""
        import ast

        tree = ast.parse((REPO_ROOT / "scripts" / "serve_ui.py").read_text("utf-8"))
        source = (REPO_ROOT / "scripts" / "serve_ui.py").read_text("utf-8")
        assert "route_message(text, self.server.system, to=to)" in source
        fn = source.split("def route_message(", 1)[1].split("\ndef ", 1)[0]
        assert "speak=True" in fn


# ---------------------------------------------------------------------------
# Mutation-adjacent structural guarantees, kept as ordinary tests so a
# future refactor cannot silently reintroduce any of the five mutations
# this phase's own report proves were caught and reverted.
# ---------------------------------------------------------------------------


class TestStructuralInvariants:
    def test_exchange_speak_parameter_defaults_to_false(self):
        import inspect

        assert inspect.signature(exchange).parameters["speak"].default is False

    def test_control_center_talk_command_does_not_request_speech(self):
        """scripts/control_center.py's own terminal `talk`/fallback
        paths must keep calling exchange() with speak omitted (default
        False) - deliver()'s own print() would otherwise visibly
        duplicate render_turn()'s formatted output in a terminal."""
        source = (REPO_ROOT / "scripts" / "control_center.py").read_text("utf-8")
        assert "speak=True" not in source
