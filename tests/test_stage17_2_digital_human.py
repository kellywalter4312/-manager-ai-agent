"""Stage 17.2 - Digital Human / Agent Presence, the next coherent slice
after 17.1's foundation.

What this phase actually adds, proved here rather than merely claimed:

    PresenceRegistry + speaking()   a real, live, in-process SPEAKING
                                     signal, written only around the one
                                     real blocking speak() call this
                                     codebase has
                                     (VoiceChannel.deliver())
    VoiceActivationSession          now writes a real LISTENING signal
                                     for the agent it was activated for
    presence_for(live=...)          a live signal overrides the static
                                     default, never invented, never
                                     stuck
    POST /avatar + a Control        an appearance picker distinct from
    Center appearance picker        the Room's WHO-to-address picker
    PresentationIntent              a deliberately unwired gesture seam

Nothing in this file exercises rendering, lip-sync or 3D geometry -
none of that exists yet, and this file's own cross-device/renderer
tests pin that it still does not, honestly.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.channels.activation import ActivationState, VoiceActivationSession
from src.channels.channel import Response, VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.providers.availability import Availability, AvailabilityState
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import (
    PresenceRegistry,
    PresenceState,
    PresentationIntent,
    presence_for,
    set_avatar_preference,
    speaking,
)
from src.services.identity.voice import activation_for, voice_for
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
    """A real, genuinely blocking (if told to be) Speaker - the only
    honest way to prove a live SPEAKING window without a real audio
    device. ``delay`` mirrors what WindowsSpeechAdapter/
    ElevenLabsSpeechAdapter both genuinely do: block until playback is
    done."""

    def __init__(self, delay: float = 0.0, ok: bool = True) -> None:
        self._delay = delay
        self._ok = ok
        self.calls: list[tuple[str, object]] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        self.calls.append((text, voice))
        if self._delay:
            time.sleep(self._delay)
        return self._ok


class _RaisingSpeaker(SpeechAdapter):
    """Speaks by raising - proves speaking() clears its assertion even
    when the wrapped call fails, so SPEAKING can never get stuck."""

    name = "raising"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        raise RuntimeError("synthesis exploded")


# ---------------------------------------------------------------------------
# 1. PresenceRegistry: a real, minimal, in-process live-state store.
# ---------------------------------------------------------------------------


class TestPresenceRegistry:
    def test_set_get_clear(self):
        reg = PresenceRegistry()
        assert reg.get("Alfred") is None
        reg.set("Alfred", PresenceState.SPEAKING)
        assert reg.get("Alfred") == PresenceState.SPEAKING
        reg.clear("Alfred")
        assert reg.get("Alfred") is None

    def test_agents_are_independent(self):
        reg = PresenceRegistry()
        reg.set("Alfred", PresenceState.SPEAKING)
        assert reg.get("Arthur") is None
        reg.set("Arthur", PresenceState.LISTENING)
        assert reg.get("Alfred") == PresenceState.SPEAKING
        assert reg.get("Arthur") == PresenceState.LISTENING

    def test_clearing_an_unset_agent_is_a_safe_no_op(self):
        reg = PresenceRegistry()
        reg.clear("Nobody")  # must not raise
        assert reg.get("Nobody") is None


class TestSpeakingContextManager:
    def test_sets_and_clears_around_the_call(self):
        reg = PresenceRegistry()
        assert reg.get("Alfred") is None
        with speaking(reg, "Alfred"):
            assert reg.get("Alfred") == PresenceState.SPEAKING
        assert reg.get("Alfred") is None

    def test_clears_even_when_the_wrapped_call_raises(self):
        """SPEAKING must never get permanently stuck - the exact
        failure mode a bare set()/clear() pair (no try/finally) would
        have the moment delivery raised."""
        reg = PresenceRegistry()
        with pytest.raises(RuntimeError):
            with speaking(reg, "Alfred"):
                raise RuntimeError("boom")
        assert reg.get("Alfred") is None

    def test_a_none_registry_is_a_safe_no_op(self):
        with speaking(None, "Alfred"):
            pass  # must not raise

    def test_an_empty_agent_key_is_a_safe_no_op(self):
        reg = PresenceRegistry()
        with speaking(reg, ""):
            pass
        assert reg.get("") is None


# ---------------------------------------------------------------------------
# 2. presence_for(live=...): a real signal overrides the static default,
#    and only while it is actually true.
# ---------------------------------------------------------------------------


class TestPresenceForLiveSignal:
    def test_no_live_registry_behaves_exactly_as_stage_17_1(self):
        p = presence_for("Alfred", provider=DefaultAvatarProvider())
        assert p.state == PresenceState.IDLE

    def test_an_asserted_live_state_wins_over_the_default(self):
        reg = PresenceRegistry()
        reg.set("Alfred", PresenceState.SPEAKING)
        p = presence_for("Alfred", provider=DefaultAvatarProvider(), live=reg)
        assert p.state == PresenceState.SPEAKING

    def test_an_asserted_live_state_wins_over_an_explicit_state_too(self):
        """A real, currently-true signal is not merely a fallback for
        the default - it is more current than a stale explicit claim."""
        reg = PresenceRegistry()
        reg.set("Alfred", PresenceState.SPEAKING)
        p = presence_for(
            "Alfred", provider=DefaultAvatarProvider(), state=PresenceState.LISTENING, live=reg
        )
        assert p.state == PresenceState.SPEAKING

    def test_clearing_the_live_signal_falls_back_to_the_default(self):
        reg = PresenceRegistry()
        reg.set("Alfred", PresenceState.SPEAKING)
        reg.clear("Alfred")
        p = presence_for("Alfred", provider=DefaultAvatarProvider(), live=reg)
        assert p.state == PresenceState.IDLE

    def test_the_avatar_itself_is_unaffected_by_the_live_signal(self):
        """live changes state only - never which AvatarProfile is
        resolved, keeping this an orthogonal fact exactly like every
        other Stage 17 independence guarantee."""
        reg = PresenceRegistry()
        p_idle = presence_for("Alfred", provider=DefaultAvatarProvider())
        reg.set("Alfred", PresenceState.SPEAKING)
        p_speaking = presence_for("Alfred", provider=DefaultAvatarProvider(), live=reg)
        assert p_idle.avatar == p_speaking.avatar


# ---------------------------------------------------------------------------
# 3. Real speaking synchronization through VoiceChannel.deliver() - the
#    one real, blocking, already-existing call site.
# ---------------------------------------------------------------------------


class TestRealSpeakingSynchronization:
    def test_speaking_is_genuinely_observable_while_delivery_blocks(self):
        """The load-bearing proof: a concurrent reader of the shared
        registry actually sees SPEAKING while a real (scripted,
        deliberately slow) blocking speak() call is still running -
        not merely set-then-immediately-cleared in the same statement,
        which a fake synchronous test could pass without anything
        being real."""
        _, manager, channel, conversation, _ = system()
        registry = PresenceRegistry()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(delay=0.3), presence=registry
        )
        response = real_channel.ask("what can you do?")

        observed: list[PresenceState | None] = []

        def watch():
            for _ in range(15):
                observed.append(registry.get(manager.own_skills.name))
                time.sleep(0.03)

        watcher = threading.Thread(target=watch)
        watcher.start()
        real_channel.deliver(response)
        watcher.join()

        assert PresenceState.SPEAKING in observed
        assert registry.get(manager.own_skills.name) is None

    def test_speaking_marks_the_true_canonical_speaker_not_the_activated_name(self):
        """The Room's own rule, extended to presence: the visual
        speaker must correspond to the actual canonical speaker the
        conversation system recorded (turn.handled_by), never an
        assumption about who was addressed."""
        _, manager, _, conversation, _ = system()
        registry = PresenceRegistry()
        speaker = _ScriptedSpeaker()
        real_channel = VoiceChannel(manager, speech=speaker, presence=registry)

        response = real_channel.ask("read my inbox for job opportunities", addressed="Arthur")
        assert response.turn.handled_by == "Arthur"

        marks: list[str] = []
        original_set = registry.set

        def spying_set(agent_key, state):
            marks.append(agent_key)
            original_set(agent_key, state)

        registry.set = spying_set  # type: ignore[method-assign]
        real_channel.deliver(response)

        assert marks == ["Arthur"]
        assert registry.get("Arthur") is None
        assert registry.get(manager.own_skills.name) is None

    def test_speech_completion_returns_presence_to_idle(self):
        _, manager, _, _, centre = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        response = channel.ask("what can you do?")
        channel.deliver(response)

        view = centre.overview().agent(manager.own_skills.name)
        # presence_for isn't wired with this ad-hoc registry through
        # centre, so read it directly the same way observer.py would.
        p = presence_for(
            manager.own_skills.name, provider=DefaultAvatarProvider(), live=registry
        )
        assert p.state == PresenceState.IDLE

    def test_speaking_state_is_not_permanently_stuck_when_speak_raises(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_RaisingSpeaker(), presence=registry)
        response = channel.ask("what can you do?")

        with pytest.raises(RuntimeError):
            channel.deliver(response)

        assert registry.get(manager.own_skills.name) is None

    def test_a_channel_built_with_no_presence_registry_behaves_exactly_as_stage_17_1(self):
        """Backward compatibility: every existing caller/test that
        constructs a VoiceChannel without ``presence=`` must be
        completely unaffected."""
        _, manager, _, _, _ = system()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker())
        response = channel.ask("what can you do?")
        delivered = channel.deliver(response)  # must not raise
        assert delivered.spoken is True

    def test_deliver_calls_the_engine_exactly_once(self):
        """speaking() must never cause a second, duplicate speak()
        call - the exact bug VoiceChannel.deliver()'s own docstring
        already refuses."""
        _, manager, _, _, _ = system()
        speaker = _ScriptedSpeaker()
        channel = VoiceChannel(manager, speech=speaker, presence=PresenceRegistry())
        response = channel.ask("what can you do?")
        channel.deliver(response)
        assert len(speaker.calls) == 1

    def test_an_unmatched_request_never_marks_nobody_as_speaking(self):
        """trail.py's own sentinel - handled_by defaults to the literal
        string "nobody" when nothing matched - must never become a
        presence entry, not even transiently. "nobody" is not a
        registered agent, and presence_for() is never even asked about
        it, but a stray write would be exactly the kind of fabricated
        signal this whole phase refuses elsewhere.

        Checked by spying on set() itself, not by reading the registry
        after deliver() returns: speaking()'s own cleanup clears
        whatever was asserted regardless of whether it should have
        been, which would make a post-hoc read pass even if "nobody"
        genuinely was marked SPEAKING for the duration of the call.
        """
        from src.control_center.views import ConversationTurn

        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        turn = ConversationTurn(
            request="xyzzy", handled_by="nobody", delegated=False, steps=(),
            outcome="nothing matched", succeeded=False,
        )
        response = Response(text="nothing matched", speech="nothing matched", spoken=False, turn=turn)

        marks: list[str] = []
        original_set = registry.set
        registry.set = lambda agent_key, state: (marks.append(agent_key), original_set(agent_key, state))  # type: ignore[method-assign]

        channel.deliver(response)

        assert marks == []
        assert registry.get("nobody") is None

    def test_the_real_wired_composition_root_shares_one_registry(self):
        """build_channel() and the Control Center must read the exact
        same PresenceRegistry instance - two separate ones would make
        a real SPEAKING assertion invisible to the snapshot that is
        supposed to show it."""
        runtime, manager, channel, _, centre = system()
        assert isinstance(channel, VoiceChannel)
        assert channel._presence is runtime.presence_registry  # noqa: SLF001
        assert centre.runtime.presence_registry is runtime.presence_registry


# ---------------------------------------------------------------------------
# 4. VoiceActivationSession: a real LISTENING signal tied to its own,
#    already-correct lifecycle - never a fabricated timer.
# ---------------------------------------------------------------------------


class TestVoiceActivationPresence:
    def _session(self, manager, channel):
        activation = activation_for(manager.own_skills)
        assert activation is not None
        registry = PresenceRegistry()
        session = VoiceActivationSession(channel, activation, presence=registry)
        return session, registry, activation

    def test_activation_asserts_listening_for_the_activated_agent(self):
        _, manager, channel, _, _ = system()
        session, registry, activation = self._session(manager, channel)
        session.start()
        assert registry.get(activation.key) is None
        session.hear_activation("hey alfred")
        assert registry.get(activation.key) == PresenceState.LISTENING

    def test_end_clears_the_listening_signal(self):
        _, manager, channel, _, _ = system()
        session, registry, activation = self._session(manager, channel)
        session.start()
        session.hear_activation("hey alfred")
        session.end()
        assert registry.get(activation.key) is None

    def test_stop_clears_the_listening_signal_too(self):
        _, manager, channel, _, _ = system()
        session, registry, activation = self._session(manager, channel)
        session.start()
        session.hear_activation("hey alfred")
        session.stop()
        assert registry.get(activation.key) is None

    def test_a_turn_returns_to_listening_after_speaking_completes(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        real_channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        activation = activation_for(manager.own_skills)
        session = VoiceActivationSession(real_channel, activation, presence=registry)
        session.start()
        session.hear_activation("hey alfred")

        session.turn("what can you do?")

        assert session.state == ActivationState.LISTENING
        assert registry.get(activation.key) == PresenceState.LISTENING

    def test_a_session_with_no_presence_registry_behaves_unchanged(self):
        """Backward compatibility: every existing caller of
        VoiceActivationSession (this class already had tests before
        Stage 17.2) keeps working with presence entirely omitted."""
        _, manager, channel, _, _ = system()
        activation = activation_for(manager.own_skills)
        session = VoiceActivationSession(channel, activation)
        session.start()
        assert session.hear_activation("hey alfred") is True
        session.turn("what can you do?")  # must not raise
        assert session.state == ActivationState.LISTENING


# ---------------------------------------------------------------------------
# 5. Avatar changes leave capabilities and connections untouched too -
#    closing the two guarantees Stage 17.1's own test list did not yet
#    name explicitly.
# ---------------------------------------------------------------------------


class TestAvatarChangeLeavesCapabilitiesAndConnectionsAlone:
    def test_capabilities_are_unchanged_by_an_avatar_choice(self):
        runtime, manager, _, _, centre = system()
        before = tuple(c.name for c in centre.overview().agent(manager.own_skills.name).capabilities)

        memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            manager.own_skills.name,
            "calm_composed",
            provider=DefaultAvatarProvider(),
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

        after = tuple(c.name for c in centre.overview().agent(manager.own_skills.name).capabilities)
        assert after == before

    def test_the_runtimes_connection_object_is_unchanged_by_an_avatar_choice(self):
        runtime, manager, _, _, _ = system()
        connection_before = runtime.connections

        memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            manager.own_skills.name,
            "warm_formal",
            provider=DefaultAvatarProvider(),
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

        assert runtime.connections is connection_before


# ---------------------------------------------------------------------------
# 6. PresentationIntent: a real, tested, honestly-unwired seam.
# ---------------------------------------------------------------------------


class TestPresentationIntentIsAnHonestSeam:
    def test_construction_and_validation(self):
        intent = PresentationIntent(gesture="  Acknowledge  ")
        assert intent.gesture == "acknowledge"
        assert intent.intensity == 1.0

    def test_an_empty_gesture_is_rejected(self):
        with pytest.raises(ValueError):
            PresentationIntent(gesture="   ")

    def test_is_frozen(self):
        intent = PresentationIntent(gesture="nod")
        with pytest.raises(Exception):
            intent.gesture = "wave"  # type: ignore[misc]

    def test_nothing_in_the_manager_or_brain_layer_constructs_one(self):
        """The brief's own rule made impossible to violate silently:
        the Brain must never manipulate a gesture/animation primitive
        directly. This is a seam with zero production callers today."""
        import ast

        for rel in ("src/manager", "src/agents"):
            for path in (REPO_ROOT / rel).rglob("*.py"):
                if "PresentationIntent" in path.read_text("utf-8"):
                    pytest.fail(f"{path} references PresentationIntent - the seam is unwired by design")


# ---------------------------------------------------------------------------
# 7. Appearance picker: the snapshot carries real choices, and it is a
#    genuinely distinct control from the Room's WHO-to-address picker.
# ---------------------------------------------------------------------------


class TestAppearancePicker:
    def test_snapshot_carries_the_real_avatar_catalog(self):
        """Stage 17.13 added a fourth real catalog entry
        (sharp_technical, a genuine cyan accent) - updated in step, not
        weakened."""
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        ids = {p["avatar_id"] for p in data["avatar_catalog"]}
        assert ids == {"warm_formal", "calm_composed", "bright_approachable", "sharp_technical"}

    def test_workspace_appearance_picker_is_distinct_from_the_room_who_picker(self):
        shell = UI.read_text("utf-8")
        assert "avatar-picker" in shell
        assert "who-picker" in shell
        # Two different data attributes drive them - proof they are not
        # the same control wearing two class names.
        assert "data-avatar-agent=" in shell
        assert "data-pick=" in shell

    def test_the_appearance_picker_never_touches_selectedagent(self):
        """Choosing an appearance must never change WHO the composer
        addresses - the exact confusion the brief explicitly warns
        against (WHO vs APPEARANCE)."""
        shell = UI.read_text("utf-8")
        fn = shell.split("function wireAvatarPicker(){", 1)[1].split("\n  }", 1)[0]
        assert "selectedAgent" not in fn

    def test_the_who_picker_never_posts_to_avatar(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function wireComposer(){", 1)[1].split("\n  }", 1)[0]
        assert '"avatar"' not in fn and "'avatar'" not in fn

    def test_the_workspace_view_no_longer_stringifies_the_avatar_object(self):
        """A real regression this phase found and fixed: a.avatar
        became a real object in Stage 17.1, and the Workspace view's
        old `${a.avatar ?? "..."}` would have rendered
        "[object Object]" the moment a real snapshot reached it."""
        shell = UI.read_text("utf-8")
        assert "${a.avatar ?? " not in shell
        assert "reserved — not implemented" not in shell


# ---------------------------------------------------------------------------
# 8. Cross-device / performance honesty, re-affirmed after this phase's
#    own additions.
# ---------------------------------------------------------------------------


class TestCrossDeviceAndHonestyStillHold:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_device_name_anywhere_in_this_phases_backend_additions(self):
        for rel in (
            "src/services/identity/presence.py",
            "src/channels/channel.py",
            "src/channels/activation.py",
            "src/composition.py",
        ):
            lowered = (REPO_ROOT / rel).read_text("utf-8").lower()
            for device in self.DEVICE_NAMES:
                assert device not in lowered, f"{rel} names a device: {device}"

    def test_the_interface_shell_still_names_no_device(self):
        lowered = UI.read_text("utf-8").lower()
        for device in self.DEVICE_NAMES:
            assert device not in lowered, f"ui/index.html names a device: {device}"

    def test_the_new_breathing_animation_is_covered_by_the_one_global_reduced_motion_rule(self):
        """No second prefers-reduced-motion block was added for the
        idle breathing animation - the same "one global rule" discipline
        identityGlyph()'s own spin already established."""
        shell = UI.read_text("utf-8")
        # Exactly the pre-existing global CSS block, still the only one -
        # a second @media(prefers-reduced-motion) block anywhere would be
        # the "second motion system" this project's own tests already
        # refuse for identityGlyph()'s spin.
        assert shell.count("@media (prefers-reduced-motion:reduce){*{") == 1
        assert shell.count("@media (prefers-reduced-motion") == 1

    def test_lip_sync_is_honestly_not_implemented(self):
        """No viseme/phoneme/lip-sync vocabulary appears anywhere in
        the speech or presence layers - both real speech providers
        were inspected and neither exposes timing data, so no seam was
        invented for data that cannot exist yet. See this test suite's
        own module docstring and the Stage 17.2 final report."""
        for rel in (
            "src/channels/speech.py",
            "src/providers/windows_speech_provider.py",
            "src/providers/elevenlabs_speech_provider.py",
            "src/services/identity/presence.py",
        ):
            lowered = (REPO_ROOT / rel).read_text("utf-8").lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
                assert word not in lowered, f"{rel} claims lip-sync vocabulary it cannot honestly support"

    def test_no_3d_rendering_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text
