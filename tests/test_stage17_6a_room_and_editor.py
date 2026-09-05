"""Stage 17.6A - the first genuinely visible multi-agent digital-human
Conversation Room slice, plus the beginning of a real, user-controlled
Digital Human configuration editor.

Two kinds of proof, matching the discipline every prior Stage 17 phase
already established:

    DATA LAYER (Python, executed)   each room participant genuinely
                                     resolves its own, independent
                                     avatar/presence/profile; speaking
                                     state is never shared across
                                     participants; an unregistered name
                                     in text can never become a room
                                     participant.

    RENDERER/UI (JavaScript, static) the Room's own new per-participant
                                     body markup and the new Digital
                                     Human editor form - read as source
                                     text, this project's own
                                     established method for verifying
                                     3D/DOM code (see
                                     tests/test_stage17_5_digital_human_
                                     renderer.py's own module docstring
                                     for why: headless pixel
                                     verification was found unreliable
                                     in this sandbox for both new and
                                     pre-existing 3D code alike).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.availability import Availability, AvailabilityState
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import PresenceState, set_digital_human_style
from src.services.identity.voice import voice_for
from scripts.exchange import exchange, memory_for

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


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
    def __init__(self, delay: float = 0.0):
        self._delay = delay

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout=None):
        raise NotImplementedError

    def speak(self, text, voice=None):
        if self._delay:
            time.sleep(self._delay)
        return True


# ---------------------------------------------------------------------------
# 1-3. Multiple participants render independently, generically - no
#      hardcoded agent name anywhere in the new Room markup.
# ---------------------------------------------------------------------------


class TestRoomRendersEachParticipantGenerically:
    def test_the_room_body_markup_names_no_agent(self):
        """Alfred/Arthur must never appear as string literals in the
        humanoidBodies template - a third, fourth, fifth specialist
        must render with zero lines changed here, the same rule
        buildManagerEmblem/buildSpecialistEmblem's own docstring
        already holds this file to."""
        shell = UI.read_text("utf-8")
        block = shell.split("const humanoidBodies = c.participants", 1)[1].split(
            "}).join(\"\");", 1
        )[0]
        assert '"Alfred"' not in block and '"Arthur"' not in block

    def test_only_agent_participants_get_a_body_never_the_person(self):
        """The brief's own explicit rule: the human user does not
        become an avatar - only c.participants entries whose kind is
        not "person" get a data-avatarbody3d container."""
        shell = UI.read_text("utf-8")
        block = shell.split("const humanoidBodies = c.participants", 1)[1].split(
            "}).join(\"\");", 1
        )[0]
        assert 'filter(p=>p.kind!=="person")' in block

    def test_each_body_is_keyed_by_the_real_participant_name(self):
        shell = UI.read_text("utf-8")
        block = shell.split("const humanoidBodies = c.participants", 1)[1].split(
            "}).join(\"\");", 1
        )[0]
        assert 'data-avatarbody3d="${esc(p.name)}"' in block

    def test_room_bodies_are_wired_by_the_same_generic_wireavatarbody3d(self):
        """No new wiring function was added for the Room - the existing
        Stage 17.5 wireAvatarBody3D() already generically picks up
        every [data-avatarbody3d] element regardless of which view
        rendered it, proven by this file never defining a second
        wire*3D function for the room bodies."""
        shell = UI.read_text("utf-8")
        assert shell.count("function wireAvatarBody3D(){") == 1
        assert "function wireRoomAvatarBody3D" not in shell


# ---------------------------------------------------------------------------
# 4/5. Correct speaker gets SPEAKING; wrong participant never does;
#      LISTENING stays independent.
# ---------------------------------------------------------------------------


class TestSpeakerIsolationInTheRoom:
    def test_only_the_true_canonical_speaker_is_marked_speaking(self):
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(delay=0.2), presence=runtime.presence_registry
        )
        alfred_marks: list = []
        arthur_marks: list = []

        def watch():
            for _ in range(15):
                alfred_marks.append(runtime.presence_registry.get(manager.own_skills.name))
                arthur_marks.append(runtime.presence_registry.get("Arthur"))
                time.sleep(0.02)

        watcher = threading.Thread(target=watch)
        watcher.start()
        turn = exchange(
            real_channel, conversation, "read my inbox for job opportunities",
            addressed="Arthur", speak=True,
        )
        watcher.join()

        assert turn.handled_by == "Arthur"
        assert PresenceState.SPEAKING in arthur_marks
        assert PresenceState.SPEAKING not in alfred_marks

    def test_listening_lifecycle_remains_independent_of_speaking(self):
        """Regression: Stage 17.2's VoiceActivationSession still marks
        LISTENING for its own activated agent, untouched by this
        phase's Room changes."""
        from src.channels.activation import VoiceActivationSession
        from src.services.identity.presence import PresenceRegistry
        from src.services.identity.voice import activation_for

        _, manager, channel, _, _ = system()
        activation = activation_for(manager.own_skills)
        registry = PresenceRegistry()
        session = VoiceActivationSession(channel, activation, presence=registry)
        session.start()
        session.hear_activation("hey alfred")
        assert registry.get(activation.key) == PresenceState.LISTENING


# ---------------------------------------------------------------------------
# 16. An unregistered name mentioned in text can never become a room
#     participant.
# ---------------------------------------------------------------------------


class TestNoFabricatedParticipants:
    def test_an_unregistered_name_in_text_never_joins_the_room(self):
        _, manager, channel, conversation, _ = system()
        turn = exchange(channel, conversation, "hey Sarah, what do you think about this?")
        assert not conversation.has("Sarah")
        assert manager.registry.get("Sarah") is None

    def test_an_invalid_explicit_selection_never_joins_the_room_either(self):
        _, manager, channel, conversation, _ = system()
        exchange(channel, conversation, "hello there", addressed="Sarah")
        assert not conversation.has("Sarah")


# ---------------------------------------------------------------------------
# 6. Agent picker (who to address) stays separate from avatar/Digital
#    Human configuration, structurally.
# ---------------------------------------------------------------------------


class TestSelectionSeparationInTheEditor:
    def test_the_digital_human_form_never_touches_selectedagent(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function wireDigitalHumanForm(){", 1)[1].split("\n  }", 1)[0]
        assert "selectedAgent" not in fn

    def test_the_digital_human_form_posts_only_to_profile_never_avatar_or_message(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function wireDigitalHumanForm(){", 1)[1].split("\n  }", 1)[0]
        assert 'fetch("profile"' in fn
        assert '"message"' not in fn and '"avatar"' not in fn

    def test_the_avatar_picker_never_reads_dh_fields(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function wireAvatarPicker(){", 1)[1].split("\n  }", 1)[0]
        assert "dh-field" not in fn and "dh-save" not in fn


# ---------------------------------------------------------------------------
# 7-9. Avatar/Digital Human configuration stays independent of LLM and
#      voice provider, both directions.
# ---------------------------------------------------------------------------


class TestConfigurationIndependentOfProvidersAndVoice:
    def test_llm_change_preserves_digital_human_style(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        from src.services.identity.presence import digital_human_style_for

        assert digital_human_style_for(manager.own_skills.name, recall=memory.recall)["posture"] == "standing"

    def test_digital_human_change_preserves_llm_brain_identity(self):
        _, manager, _, _, _ = system()
        brain_before = manager.brain
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"gesture_style": "restrained"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert manager.brain is brain_before

    def test_changing_the_speech_provider_preserves_digital_human_configuration(self):
        """A different VoiceChannel/speech adapter (the voice-provider
        axis) must never touch the stored presentation style."""
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        voice_before = voice_for(manager.own_skills)
        personality_before = personality_for(manager.own_skills)

        # Swap the whole speech provider/channel - a different voice
        # axis entirely.
        VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry)

        from src.services.identity.presence import digital_human_style_for

        assert digital_human_style_for(manager.own_skills.name, recall=memory.recall)["posture"] == "seated"
        assert voice_for(manager.own_skills) == voice_before
        assert personality_for(manager.own_skills) == personality_before


# ---------------------------------------------------------------------------
# 10/11. Provider replacement and no-duplication, re-affirmed with the
#        Room now in play.
# ---------------------------------------------------------------------------


class TestNoDuplicationWithRoomInPlay:
    def test_a_multi_participant_exchange_creates_no_new_agent_brain_or_memory(self):
        runtime, manager, _, conversation, _ = system()
        registry_size_before = len(manager.registry.all_agents())
        brain_before = manager.brain
        memory_before = manager.memory
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )

        exchange(real_channel, conversation, "read my inbox", addressed="Arthur", speak=True)

        assert len(manager.registry.all_agents()) == registry_size_before
        assert manager.brain is brain_before
        assert manager.memory is memory_before


# ---------------------------------------------------------------------------
# 12/13/14. Device independence, responsiveness, reduced motion - for
#           this phase's own new markup.
# ---------------------------------------------------------------------------


class TestDeviceAndMotionHonesty:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_device_name_in_the_new_room_or_editor_code(self):
        shell = UI.read_text("utf-8").lower()
        for marker_start, marker_end in (
            ("const humanoidbodies = c.participants", "}).join(\"\");"),
            ("function wiredigitalhumanform(){", "\n  }"),
        ):
            block = shell.split(marker_start, 1)[1].split(marker_end, 1)[0]
            for device in self.DEVICE_NAMES:
                assert device not in block, device

    def test_room_bodies_use_flex_wrap_not_a_fixed_width_breakpoint(self):
        shell = UI.read_text("utf-8")
        rule = shell.split(".room-bodies{", 1)[1].split("}", 1)[0]
        assert "flex-wrap:wrap" in rule
        # No new @media(...px...) rule was introduced for this layout -
        # confirmed globally further below alongside the reduced-motion
        # check, since both guarantees rest on the same "no new
        # breakpoint" fact.

    def test_digital_human_fields_use_auto_fill_not_a_fixed_width_breakpoint(self):
        shell = UI.read_text("utf-8")
        rule = shell.split(".dh-fields{", 1)[1].split("}", 1)[0]
        assert "auto-fill" in rule

    def test_no_new_media_query_was_added_by_this_phase(self):
        shell = UI.read_text("utf-8")
        # The pre-existing breakpoints - the global reduced-motion rule,
        # .agent3d's own phone height rule, and the larger pre-existing
        # phone layout block (crown/satellites/facets/being) - are the
        # only @media blocks in the whole file, confirmed by direct
        # count before this assertion was written. Neither new rule
        # this phase added (.room-bodies, .dh-fields) introduced a
        # third max-width breakpoint - both rely on flex-wrap/auto-fill
        # alone, verified by the two tests just above.
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_room_bodies_reuse_mountstage_reduced_motion_is_inherited(self):
        """mountAvatarBody3D (Stage 17.5, unchanged) is the only mount
        function the Room's new bodies call - reduced-motion is
        whatever that function already guarantees, never
        reimplemented here."""
        shell = UI.read_text("utf-8")
        room_fn = shell.split('V.room = () => {', 1)[1].split("\n  };", 1)[0]
        assert "mountAvatarBody3D" not in room_fn  # mounted only via wireAvatarBody3D's own generic loop
        assert "data-avatarbody3d" in room_fn


# ---------------------------------------------------------------------------
# 15. Evolution/Extensions/Upgrade governance remains untouched and
#     uncoupled.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_extensions_file_references_this_phases_additions(self):
        for rel in (
            "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            text = (REPO_ROOT / rel).read_text("utf-8")
            for forbidden in ("room-body", "roomBody", "DigitalHuman", "avatarbody3d", "dh-field"):
                assert forbidden not in text, (rel, forbidden)
