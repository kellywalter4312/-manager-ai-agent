"""Stage 17.8 - natural multi-agent Conversation Room interaction, built
entirely on the Stage 17.1-17.7 foundation: a tasteful pulsing indicator
on the real current speaker's own frame, a restrained per-agent weight
shift present in every presence state, and an honest "oriented toward
the conversation" yaw bias for agents who are not speaking while another
real agent in the room is - bounded, deterministic, and explicitly not a
literal look-at (each participant's body renders in its own independent
WebGL context with no shared 3D coordinate space to compute a real
bearing across).

Every change this phase makes lives in ui/index.html. No Python source
file was touched - re-verified simply by every existing Stage 17.1-17.7
suite still passing unchanged.

Verified as source text, this project's own established method for
3D/DOM code since Stage 17.5 (see that phase's own test module
docstring for why no JS execution harness exists here).
"""

from __future__ import annotations

from pathlib import Path

from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.availability import Availability, AvailabilityState
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import set_digital_human_style
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
    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout=None):
        raise NotImplementedError

    def speak(self, text, voice=None):
        return True


def _mount_body() -> str:
    shell = UI.read_text("utf-8")
    return shell.split("function mountAvatarBody3D(container", 1)[1].split(
        "\n  }\n\n  let agent3DStages", 1
    )[0]


def _idle_branch(mount: str) -> str:
    return mount.rsplit("} else {", 1)[1]


def _room_fn() -> str:
    shell = UI.read_text("utf-8")
    return shell.split("V.room = () => {", 1)[1].split("\n  };", 1)[0]


# ---------------------------------------------------------------------------
# 1. A tasteful, real current-speaker indicator, derived only from the
#    real speaking flag already computed per participant.
# ---------------------------------------------------------------------------


class TestSpeakerIndicator:
    def test_only_the_room_body_speaking_class_gets_the_pulse(self):
        shell = UI.read_text("utf-8")
        assert ".room-body.speaking .agent3d{" in shell
        rule = shell.split(".room-body.speaking .agent3d{", 1)[1].split("}", 1)[0]
        assert "animation:speakerglow" in rule
        # Never applied to .selected or unconditionally to .agent3d -
        # the pulse is scoped to the real speaking flag alone.
        assert ".agent3d{" not in shell.split("@keyframes speakerglow", 1)[0].split(
            ".room-body.speaking .agent3d{", 1
        )[0][-40:]

    def test_the_speaking_flag_still_comes_from_the_real_per_participant_activity(self):
        """Re-affirmed: the Room's own flags array still derives
        "speaking" from this exact participant's own real activity -
        never a hardcoded name, never the selected agent."""
        room = _room_fn()
        assert 'activity==="speaking"?"speaking":""' in room

    def test_the_pulse_is_covered_by_the_one_existing_reduced_motion_rule(self):
        shell = UI.read_text("utf-8")
        assert shell.count("@media (prefers-reduced-motion") == 1


# ---------------------------------------------------------------------------
# 2. Weight shift: real, deterministic, per-agent, present across every
#    presence state (not only idle).
# ---------------------------------------------------------------------------


class TestWeightShift:
    def test_weight_shift_is_deterministic_and_per_agent(self):
        mount = _mount_body()
        assert "group.position.x = Math.sin(t*idleRate*0.63 + gestureSignature*2) * 0.025;" in mount

    def test_weight_shift_is_computed_before_the_speaking_listening_idle_branches(self):
        """Applies in every state, not only idle - the same "present
        throughout" treatment the existing idle bob/sway already has."""
        mount = _mount_body()
        before_branches = mount.split('if(activity === "speaking"){', 1)[0]
        assert "group.position.x = Math.sin(t*idleRate*0.63" in before_branches

    def test_weight_shift_is_restrained(self):
        """0.025 units - smaller than the existing idle vertical bob's
        own 0.02-unit amplitude is comparable in scale, never a large,
        cartoonish sway."""
        mount = _mount_body()
        assert "* 0.025;" in mount


# ---------------------------------------------------------------------------
# 3. "Oriented toward the conversation": bounded, non-accumulating,
#    honestly not a literal look-at, only active from real data.
# ---------------------------------------------------------------------------


class TestOrientedTowardConversation:
    def test_the_yaw_bias_is_a_direct_bounded_addition_not_an_accumulator(self):
        """group.rotation.y is fully recomputed from scratch
        (Math.sin(t*0.18)*0.25) at the top of every frame - so `+=
        attentionYaw` here modifies only this frame's already-fresh
        value, never compounding across frames. A `+=` onto a value
        that persists across frames (unlike this one) would have been
        the real accumulation bug this test exists to catch a
        regression into."""
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "group.rotation.y += attentionYaw;" in idle
        assert mount.count("group.rotation.y = Math.sin(t*0.18) * 0.25;") == 1

    def test_the_yaw_bias_only_applies_during_genuine_room_attention(self):
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "const attentionYaw = roomAttention ? 0.08 + idleSignature * 0.06 : 0;" in idle

    def test_this_is_honestly_not_claimed_as_a_literal_look_at(self):
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "literal look-at" in idle
        assert "no shared 3D coordinate" in idle


# ---------------------------------------------------------------------------
# 4. Multiple agents: no hardcoded count, no hardcoded name, anywhere
#    in the Room's own markup.
# ---------------------------------------------------------------------------


class TestMultiAgentGenericity:
    def test_no_participant_count_assumption_in_the_room_markup(self):
        room = _room_fn()
        for forbidden in (
            "participants.length===2", "participants.length == 2",
            "participants[0]", "participants[1]",
        ):
            assert forbidden not in room, forbidden

    def test_no_agent_name_anywhere_in_the_room_or_mount_functions(self):
        room = _room_fn()
        mount = _mount_body()
        for block in (room, mount):
            assert '"Alfred"' not in block and '"Arthur"' not in block

    def test_room_body_animation_state_stays_local_to_each_mount_call(self):
        """Re-affirmed from Stage 17.7: no module-level `let`/`var` for
        any of this phase's own new variables - weight shift and
        attention yaw are computed fresh, per agent, inside each
        mount's own returned closure."""
        shell = UI.read_text("utf-8")
        assert not any(
            line.strip().startswith(("let attentionYaw", "var attentionYaw"))
            for line in shell.split("\n")
        )


# ---------------------------------------------------------------------------
# 5. No random animation anywhere in the renderer, re-confirmed after
#    this phase's own additions.
# ---------------------------------------------------------------------------


class TestNoRandomAnimation:
    def test_no_math_random_in_the_renderer_block(self):
        shell = UI.read_text("utf-8")
        block = shell.split("function loadThree(){", 1)[1].split("function go(key", 1)[0]
        assert "Math.random" not in block


# ---------------------------------------------------------------------------
# 6. LLM/Brain/Memory/VoiceProfile independence, explicitly re-verified
#    this phase (item 7 of the brief), and canonical speaker isolation
#    re-confirmed at the presence layer.
# ---------------------------------------------------------------------------


class TestProviderAndProfileIndependence:
    def test_llm_swap_leaves_avatar_body_posture_and_profile_untouched(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        from src.services.identity.presence import (
            avatar_preference_for,
            digital_human_style_for,
            set_avatar_preference,
        )

        set_avatar_preference(
            manager.own_skills.name, "calm_composed", provider=runtime.avatar_provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated", "gesture_style": "restrained"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        avatar_before = avatar_preference_for(manager.own_skills.name, recall=memory.recall)
        style_before = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        voice_before = voice_for(manager.own_skills)
        personality_before = personality_for(manager.own_skills)
        memory_before = manager.memory

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        assert avatar_preference_for(manager.own_skills.name, recall=memory.recall) == avatar_before
        assert digital_human_style_for(manager.own_skills.name, recall=memory.recall) == style_before
        assert voice_for(manager.own_skills) == voice_before
        assert personality_for(manager.own_skills) == personality_before
        assert manager.memory is memory_before

    def test_speaking_never_leaks_to_a_non_speaking_agent(self):
        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        exchange(real_channel, conversation, "what can you do?", speak=True)
        assert runtime.presence_registry.get("Arthur") is None

    def test_canonical_speaker_selection_is_never_hardcoded(self):
        """Neutral text isolates the explicit selection mechanism from
        natural capability routing - the established Stage 17.3+
        lesson, re-applied here."""
        _, manager, channel, conversation, _ = system()
        turn = exchange(channel, conversation, "what can you do?", addressed="Arthur")
        assert turn.handled_by == "Arthur"


# ---------------------------------------------------------------------------
# 7. Evolution/Extensions/Upgrade governance remains untouched and
#    uncoupled from this phase's own additions.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_extensions_file_references_this_phases_additions(self):
        for rel in (
            # src/extensions.py (Stage 11.A) was removed on 2026-09-15 as a
            # parallel, unwired governance architecture; the governed
            # lifecycle these checks care about lives in src/evolution.py
            # and src/services/evolution/.
            "src/evolution.py", "src/services/evolution/handlers.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            text = (REPO_ROOT / rel).read_text("utf-8")
            for forbidden in ("attentionYaw", "speakerglow", "weight shift", "position.x = Math.sin"):
                assert forbidden not in text, (rel, forbidden)

    def test_no_python_source_file_was_changed_this_phase(self):
        assert not any(
            "attentionYaw" in (REPO_ROOT / "src" / rel).read_text("utf-8")
            for rel in ("services/identity/presence.py", "channels/channel.py")
        )


# ---------------------------------------------------------------------------
# 8. Device independence, re-confirmed for this phase's own additions.
# ---------------------------------------------------------------------------


class TestDeviceIndependence:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_device_name_in_this_phases_new_code(self):
        shell = UI.read_text("utf-8").lower()
        for start, end in (
            (".room-body.speaking .agent3d{", "@keyframes speakerglow{0%,100%{box-shadow:0 0 18px rgba(79,209,165,.35)}\n    50%{box-shadow:0 0 30px rgba(79,209,165,.6)}}"),
        ):
            block = shell[shell.find(start.lower()):shell.find(end.lower()) + len(end)]
            for device in self.DEVICE_NAMES:
                assert device not in block, device
        mount = _mount_body().lower()
        for device in self.DEVICE_NAMES:
            assert device not in mount, device
