"""Stage 10 - production voice wired through composition, end to end.

Stage 8A proved a VoiceActivationSession correctly gates a Channel. This file
proves the one thing that changed underneath it this stage: composition.py
now selects the Channel's speech adapter through build_speech(settings)
rather than constructing NoSpeech() directly - and that this change is
invisible to everything above it. Canonical Alfred, his Brain, his Memory,
his VoiceActivation and his VoiceProfile are exactly as reachable, and
exactly as un-duplicated, as they were before build_speech existed.
"""

from __future__ import annotations

import subprocess

import pytest

import src.providers.windows_speech_provider as windows_speech_provider
from src.channels.activation import ActivationState, VoiceActivationSession
from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.composition import build_channel, build_manager, build_runtime
from src.config.settings import Settings
from src.manager.cognition import assess_cognition
from src.providers.availability import Availability, AvailabilityState
from src.providers.windows_speech_provider import WindowsSpeechAdapter
from src.services.email.in_memory_mailbox import InMemoryMailbox


def system(**overrides):
    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    channel = build_channel(runtime, manager)
    return runtime, manager, channel


class ScriptedSpeech(SpeechAdapter):
    """The same honest test double Stage 8A's own suite uses - records what
    it is asked to say, never fabricates success."""

    def __init__(self, state=AvailabilityState.AVAILABLE, heard: str = "hello alfred"):
        self._availability = Availability(state=state)
        self._heard = heard
        self.spoken: list[tuple[str, object]] = []

    @property
    def name(self):
        return "scripted"

    @property
    def availability(self):
        return self._availability

    def listen(self, timeout=None):
        if self._availability.state is AvailabilityState.UNAVAILABLE:
            raise SpeechUnavailable("scripted engine reports unavailable")
        return self._heard

    def speak(self, text, voice=None):
        self.spoken.append((text, voice))
        return self._availability.is_usable


# ---------------------------------------------------------------------------
# 7-11: Canonical agent preservation, through the new wiring
# ---------------------------------------------------------------------------


class TestCanonicalAgentThroughTheNewWiring:
    def test_runtime_speech_comes_from_build_speech_and_is_a_real_speechadapter(self):
        runtime = build_runtime(Settings.from_env(env={}, log_level="CRITICAL"))
        assert isinstance(runtime.speech, SpeechAdapter)

    def test_the_default_installation_still_reports_none_honestly(self):
        runtime = build_runtime(Settings.from_env(env={}, log_level="CRITICAL"))
        assert runtime.speech.name == "none"
        assert runtime.speech.available is False

    def test_voice_resolves_to_the_one_canonical_alfred(self):
        _, manager, channel = system()
        assert channel.manager is manager
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        assert session.channel.manager is manager

    def test_voice_does_not_create_another_brain(self):
        _, manager, channel = system()
        brain_before = manager.brain
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        assert manager.brain is brain_before

    def test_voice_does_not_create_another_memory(self):
        _, manager, channel = system()
        memory_before = manager.memory
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        assert manager.memory is memory_before

    def test_voice_does_not_create_another_activation_system(self):
        """Two sessions built against the same channel share the same
        activation identity object - nothing here constructs a second
        VoiceActivation or a second kind of activation machinery."""
        _, manager, channel = system()
        first = VoiceActivationSession(channel, manager.own_skills.activation)
        second = VoiceActivationSession(channel, manager.own_skills.activation)
        assert first.activation is second.activation is manager.own_skills.activation


# ---------------------------------------------------------------------------
# 12-14: Brain independence from voice
# ---------------------------------------------------------------------------


class TestBrainIndependentOfVoiceAvailability:
    def test_half_brain_still_answers_a_deterministic_capability_with_voice_unavailable(self):
        _, manager, channel = system(brain="null")
        assert assess_cognition(manager.brain).mode.value == "half_brain"
        response = channel.ask("status")
        assert response.text  # a real, deterministic answer, not silence

    def test_voice_availability_does_not_redefine_brain_mode(self):
        _, manager, _ = system()
        mode_before = assess_cognition(manager.brain).mode
        channel = VoiceChannel(manager, speech=ScriptedSpeech(state=AvailabilityState.AVAILABLE))
        channel.deliver(channel.ask("status"))
        assert assess_cognition(manager.brain).mode == mode_before

    def test_full_brain_does_not_imply_stt_tts_availability(self):
        """A provider being configured (or even genuinely usable) says
        nothing about whether a speech engine is - two separate axes."""
        _, manager, _ = system()
        channel = VoiceChannel(manager, speech=ScriptedSpeech(state=AvailabilityState.UNAVAILABLE))
        assert channel.listening is False  # speech is unavailable
        # ...independent of whatever the brain's own mode happens to be:
        assert assess_cognition(manager.brain).mode is not None


# ---------------------------------------------------------------------------
# 15-19: Failure truthfulness, through a real VoiceChannel
# ---------------------------------------------------------------------------


class TestFailureTruthfulness:
    def test_unavailable_stt_falls_back_honestly_never_faking_transcription(self):
        from src.channels.channel import Channel

        class FakeFallback(Channel):
            @property
            def name(self):
                return "fake-fallback"

            def receive(self):
                return "typed instead"

            def deliver(self, response):
                return response

        _, manager, _ = system()
        fallback = FakeFallback(manager)
        channel = VoiceChannel(
            manager,
            speech=ScriptedSpeech(state=AvailabilityState.UNAVAILABLE),
            fallback=fallback,
        )
        heard = channel.receive()
        # SpeechUnavailable was raised and caught; the fallback answered -
        # never a fabricated transcription standing in for a real one.
        assert heard == "typed instead"

    def test_unavailable_tts_never_reports_spoken_true(self):
        _, manager, _ = system()
        channel = VoiceChannel(
            manager, speech=ScriptedSpeech(state=AvailabilityState.UNAVAILABLE)
        )
        response = channel.ask("status")
        delivered = channel.deliver(response)
        assert delivered.spoken is False
        assert delivered.text  # the written answer still exists

    def test_available_tts_only_reports_spoken_true_after_a_real_call(self):
        _, manager, _ = system()
        speech = ScriptedSpeech(state=AvailabilityState.AVAILABLE)
        channel = VoiceChannel(manager, speech=speech)
        response = channel.ask("status")
        assert response.spoken is False  # ask() never speaks
        delivered = channel.deliver(response)
        assert delivered.spoken is True
        assert speech.spoken, "deliver() must have actually called the engine"

    def test_configuration_alone_never_becomes_a_success_claim(self):
        """Selecting 'none' is a valid configuration; it must not be
        confused with a working microphone."""
        from src.providers.speech_factory import build_speech

        speech = build_speech(Settings())
        assert speech.name == "none"
        assert speech.available is False


# ---------------------------------------------------------------------------
# 20-24: Conversation continues correctly, through the new wiring
# ---------------------------------------------------------------------------


class TestVoiceConversationContinuity:
    def test_a_successful_turn_reaches_the_existing_pipeline_and_speaks(self):
        _, manager, channel = system()
        speech = ScriptedSpeech(state=AvailabilityState.AVAILABLE)
        channel = VoiceChannel(manager, speech=speech)
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        assert session.hear_activation("Alfred") is True
        response = session.turn("status")
        assert response.spoken is True
        assert session.state is ActivationState.LISTENING

    def test_conversation_continues_after_a_successful_turn_with_no_reactivation(self):
        _, manager, channel = system()
        channel = VoiceChannel(manager, speech=ScriptedSpeech())
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        session.turn("first turn")
        assert session.hear_activation("Alfred") is False  # not needed, ignored
        second = session.turn("second turn, no wake phrase repeated")
        assert second.text
        assert session.state is ActivationState.LISTENING

    def test_a_failed_turn_does_not_corrupt_conversation_state(self):
        _, manager, channel = system()
        channel = VoiceChannel(manager, speech=ScriptedSpeech(state=AvailabilityState.UNAVAILABLE))
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        session.turn("a turn with no working speaker")
        # The session's own state machine is unaffected by the speaker
        # failing to speak - it is still a live, continuable conversation.
        assert session.state is ActivationState.LISTENING
        assert session.active is True
        session.turn("a further turn still works")


# ---------------------------------------------------------------------------
# The REAL WindowsSpeechAdapter (not a scripted double) through the real
# canonical pipeline - only the subprocess seam is mocked, exactly as
# test_windows_speech_provider.py mocks it; everything above that line here
# is the genuine, unmodified Channel/Manager/Brain/Memory/Activation stack.
# ---------------------------------------------------------------------------


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["powershell"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestTheRealWindowsProviderThroughTheCanonicalPipeline:
    def test_a_real_transcription_reaches_the_existing_alfred_pipeline(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, timeout: _completed(returncode=0, stdout="status\n"),
        )
        _, manager, _ = system()
        channel = VoiceChannel(manager, speech=WindowsSpeechAdapter())
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        heard = session.channel.speech.listen()
        response = session.turn(heard)
        assert heard == "status"
        assert response.turn is not None
        assert session.channel.manager is manager  # still the one canonical Alfred

    def test_alfreds_response_reaches_the_real_tts_boundary(self, monkeypatch):
        calls = []

        def fake_run(script, timeout):
            calls.append(script)
            return _completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        _, manager, _ = system()
        channel = VoiceChannel(manager, speech=WindowsSpeechAdapter())
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        response = session.turn("status")
        assert response.spoken is True
        # Stage 11.2: one call queries this machine's installed voices
        # (cached per adapter instance thereafter), one performs the
        # actual synthesis - exactly one of each per turn, never more.
        speak_calls = [c for c in calls if "Get-Content" in c]
        assert len(speak_calls) == 1

    def test_a_real_stt_failure_is_never_treated_as_understood_speech(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, timeout: _completed(returncode=0, stdout=""),
        )
        adapter = WindowsSpeechAdapter()
        with pytest.raises(SpeechUnavailable):
            adapter.listen()

    def test_a_real_tts_failure_is_never_reported_as_spoken(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, timeout: _completed(returncode=1, stderr="no audio device"),
        )
        _, manager, _ = system()
        channel = VoiceChannel(manager, speech=WindowsSpeechAdapter())
        response = channel.ask("status")
        delivered = channel.deliver(response)
        assert delivered.spoken is False
        assert delivered.text

    def test_the_real_provider_creates_no_second_alfred_brain_or_memory(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, timeout: _completed(returncode=0, stdout="status\n"),
        )
        _, manager, _ = system()
        brain_before, memory_before = manager.brain, manager.memory
        channel = VoiceChannel(manager, speech=WindowsSpeechAdapter())
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        session.start()
        session.hear_activation("Alfred")
        session.turn(session.channel.speech.listen())
        assert manager.brain is brain_before
        assert manager.memory is memory_before
        assert session.channel.manager is manager
