"""0.2 Reality Audit - ambient wake-word activation, completed.

The previous audit pass found `VoiceActivationSession` real, tested, and
wired to nothing - zero production callers, no loop that actually calls
`Listener.listen()` and feeds it in. This closes that gap with the
smallest correct addition: `src.channels.activation.run_ambient_loop`,
which reuses the existing session, the existing bounded `listen()`
primitive, and the existing `Channel.ask`/`deliver` conversation path -
no new dependency, no second voice architecture.

Every test drives the loop with a scripted `Listener`/`Speaker` double
standing in for a microphone - the loop's own logic (state transitions,
routing, honest failure) is exercised for real and deterministically;
a live human uttering a real wake phrase into a real microphone is the
one thing nothing in an automated suite can supply, and is not claimed
here. A separate, explicit real-hardware check (calling the real
Windows engine directly, bounded) is exercised outside this file, in
the turn's own practical-verification report.
"""

from __future__ import annotations

from src.channels.activation import (
    ActivationState,
    MicrophoneUnavailable,
    VoiceActivationSession,
    run_ambient_loop,
)
from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.core.contracts import Task
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.identity import load_identity
from src.services.identity.voice import VoiceActivation, VoiceProfile

IDENTITY = load_identity("IDENTITY.md")


def alfred_manager():
    manager = ManagerAgent()
    manager.own_skills = ManagerSkills(
        IDENTITY,
        roster=manager.registry.all_agents,
        converse=manager.brain.converse,
        recall=manager.memory.recall,
        retain=manager.retain,
    )
    manager.registry.register(manager.own_skills)
    return manager


class ScriptedMicrophone(SpeechAdapter):
    """A microphone that says exactly what a test tells it to, in order -
    ``None`` in the script means "ordinary silence this window", the
    identical shape a real, empty listening window produces. Recording
    every ``speak()`` call is what lets a test prove a real answer was
    actually delivered, not merely computed."""

    def __init__(self, script: list[str | None], *, fail_with: str | None = None) -> None:
        self._script = list(script)
        self._fail_with = fail_with
        self.spoken: list[str] = []
        self._availability = Availability()

    @property
    def name(self) -> str:
        return "scripted-microphone"

    @property
    def availability(self) -> Availability:
        return self._availability

    def listen(self, timeout: float | None = None) -> str:
        if self._fail_with is not None and not self._script:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=self._fail_with)
            raise SpeechUnavailable(self._fail_with)
        if not self._script:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason="no speech was recognized")
            raise SpeechUnavailable("no speech was recognized")
        heard = self._script.pop(0)
        if heard is None:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason="no speech was recognized")
            raise SpeechUnavailable("no speech was recognized")
        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        return heard

    def speak(self, text: str, voice: VoiceProfile | None = None) -> bool:
        self.spoken.append(text)
        return True


def session_for(script: list[str | None], **kwargs) -> tuple[VoiceActivationSession, ScriptedMicrophone]:
    manager = alfred_manager()
    mic = ScriptedMicrophone(script, **kwargs)
    channel = VoiceChannel(manager, speech=mic)
    activation = manager.own_skills.activation
    session = VoiceActivationSession(channel, activation)
    return session, mic


def _bounded(n: int):
    """A should_continue that stops after ``n`` iterations - so a test's
    scripted microphone running dry raises a clear, immediate honest
    failure (ordinary silence, in these tests) rather than the loop
    trying to run forever against an empty script."""
    remaining = [n]

    def go() -> bool:
        if remaining[0] <= 0:
            return False
        remaining[0] -= 1
        return True

    return go


class TestTheAmbientLoopIsWiredAndReal:
    def test_ordinary_ambient_silence_never_activates_anything(self):
        session, mic = session_for([None, None, None])
        run_ambient_loop(session, should_continue=_bounded(3))
        assert session.state is ActivationState.IDLE
        assert mic.spoken == []

    def test_the_wake_phrase_activates_and_a_request_is_answered_and_spoken(self):
        session, mic = session_for(["Alfred", "who are you?"])
        run_ambient_loop(session, should_continue=_bounded(2))
        assert session.state is ActivationState.LISTENING
        assert len(mic.spoken) == 1
        assert "Alfred" in mic.spoken[0]

    def test_ordinary_speech_before_the_wake_phrase_is_never_treated_as_a_request(self):
        """The exact "no accidental activation" requirement: unrelated
        ambient speech must never reach the conversation path."""
        session, mic = session_for(["what a nice day", "who are you?"])
        run_ambient_loop(session, should_continue=_bounded(2))
        assert session.state is ActivationState.IDLE
        assert mic.spoken == []

    def test_a_full_conversation_then_an_explicit_goodbye_ends_it(self):
        session, mic = session_for(["Alfred", "who are you?", "goodbye"])
        run_ambient_loop(session, should_continue=_bounded(3))
        assert session.state is ActivationState.IDLE
        assert len(mic.spoken) == 1  # goodbye itself is never answered as a request

    def test_prolonged_silence_after_activation_auto_ends_the_conversation(self):
        session, mic = session_for(["Alfred", "who are you?", None, None, None])
        run_ambient_loop(session, should_continue=_bounded(5), idle_turns_before_auto_end=3)
        assert session.state is ActivationState.IDLE
        assert len(mic.spoken) == 1

    def test_can_be_woken_again_after_auto_ending(self):
        session, mic = session_for(
            ["Alfred", "who are you?", None, None, None, "Alfred", "what is your role?"]
        )
        run_ambient_loop(session, should_continue=_bounded(7), idle_turns_before_auto_end=3)
        assert len(mic.spoken) == 2

    def test_a_device_or_permission_failure_stops_the_loop_honestly(self):
        """Never spins silently on a real failure - the exact "clean
        failure handling, permission/device failure handling"
        requirement."""
        session, mic = session_for(["Alfred"], fail_with="microphone access was denied by the operating system")
        try:
            run_ambient_loop(session, should_continue=_bounded(5))
            assert False, "expected MicrophoneUnavailable"
        except MicrophoneUnavailable as exc:
            assert "denied" in str(exc)

    def test_no_speech_engine_at_all_is_reported_immediately(self):
        manager = alfred_manager()
        channel = VoiceChannel(manager)  # defaults to NoSpeech
        session = VoiceActivationSession(channel, manager.own_skills.activation)
        try:
            run_ambient_loop(session, should_continue=_bounded(1))
            assert False, "expected MicrophoneUnavailable"
        except MicrophoneUnavailable:
            pass

    def test_a_recording_request_after_activation_is_genuinely_captured(self):
        """Not a mock of memory - the real record_this capability,
        reached through the real Channel.ask the loop actually calls."""
        session, mic = session_for(["Alfred", "record this for later: water the plants"])
        run_ambient_loop(session, should_continue=_bounded(2))
        manager = session.channel._manager  # the same real ManagerAgent this session wraps
        records = manager.memory.recall(limit=10)
        assert any(r.content == "water the plants" for r in records)

    def test_nothing_is_ever_persisted_from_ambient_speech_that_never_activates(self):
        session, mic = session_for(["record this for later: this should never be stored", "who are you?"])
        run_ambient_loop(session, should_continue=_bounded(2))
        manager = session.channel._manager
        assert manager.memory.recall(limit=10) == ()


class TestVoiceActivationPhraseIsRealAndDeclared:
    def test_alfred_declares_real_activation_phrases(self):
        activation = IDENTITY.activation
        assert isinstance(activation, VoiceActivation)
        assert activation.matches("Alfred")
        assert activation.matches("Hey Alfred")
        assert not activation.matches("what a nice day")
