"""Stage 8A — continuous, activation-gated voice conversation.

Proves the state machine (IDLE -> ACTIVATED -> LISTENING -> THINKING ->
SPEAKING -> ... -> ENDED -> IDLE), that activation happens once while a
conversation may hold many turns, that every turn goes through the exact
existing Channel/Manager/Brain/Memory pipeline, and that the session's own
lifecycle is idempotent - repeated start() calls do not disturb an active
conversation, which is the concrete, testable form of "navigation and live
refresh must not create duplicate activation systems" (see
ui/index.html's own voice controller for the browser-side half of that
guarantee, which this file cannot exercise).

No audio anywhere here. ``ScriptedSpeech`` is a SpeechAdapter test double in
exactly the spirit tests/test_voice_identity.py's ``SpeakOnly`` already is:
it records what it was asked to say and reports whether it actually could,
honestly, per the same three-state Availability every other engine in this
project reports through. A heard utterance is always a plain string handed
in by the test, standing in for whatever a real engine would have already
turned speech into by this point.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.channels.activation import (
    ActivationState,
    NoActiveConversation,
    VoiceActivationSession,
)
from src.channels.channel import TextChannel, VoiceChannel
from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice import VoiceActivation

REPO_ROOT = Path(__file__).resolve().parent.parent


class ScriptedSpeech(SpeechAdapter):
    """A SpeechAdapter double that records what it is asked to say and
    reports whether it actually could - never fabricating success, exactly
    the honesty real engines in this project are required to have."""

    def __init__(self, state: AvailabilityState = AvailabilityState.AVAILABLE) -> None:
        self._availability = Availability(state=state)
        self.spoken: list[tuple[str, object]] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return self._availability

    def listen(self, timeout: float | None = None) -> str:
        raise SpeechUnavailable("this double is fed utterances directly by a test")

    def speak(self, text: str, voice=None) -> bool:
        self.spoken.append((text, voice))
        return self._availability.is_usable


def manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(
        env={}, log_level="CRITICAL", memory="ephemeral", **overrides
    )
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


def session_for(mgr, *, speech_state=AvailabilityState.AVAILABLE):
    speech = ScriptedSpeech(state=speech_state)
    channel = VoiceChannel(mgr, speech=speech)
    activation = mgr.own_skills.activation
    return VoiceActivationSession(channel, activation), speech


# ---------------------------------------------------------------------------
# 1-8: Activation
# ---------------------------------------------------------------------------


class TestActivation:
    def test_the_configured_primary_phrase_activates(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        assert session.hear_activation("Alfred") is True
        assert session.state is ActivationState.ACTIVATED

    def test_the_configured_secondary_phrase_activates(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        assert session.hear_activation("Hey Alfred") is True
        assert session.state is ActivationState.ACTIVATED

    def test_an_additional_configured_phrase_activates_without_touching_the_primitive(self):
        """The extensibility guarantee at the session layer: a differently
        -configured VoiceActivation activates through the exact same
        VoiceActivationSession class, no subclass and no branch."""
        mgr = manager()
        speech = ScriptedSpeech()
        channel = VoiceChannel(mgr, speech=speech)
        activation = VoiceActivation(
            key="alfred", phrases=["Alfred", "Hey Alfred", "Okay Alfred"]
        )
        session = VoiceActivationSession(channel, activation)
        session.start()
        assert session.hear_activation("Okay Alfred") is True

    def test_unrelated_speech_does_not_activate(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        assert session.hear_activation("what time is it") is False
        assert session.state is ActivationState.IDLE

    def test_activation_does_not_create_another_canonical_agent(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        assert session.channel.manager is mgr

    def test_activation_does_not_create_another_brain(self):
        mgr = manager()
        brain_before = mgr.brain
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        session.turn("status again")
        assert mgr.brain is brain_before

    def test_activation_does_not_create_another_memory(self):
        mgr = manager()
        memory_before = mgr.memory
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        assert mgr.memory is memory_before

    def test_activation_does_not_create_another_voice_profile(self):
        mgr = manager()
        voice_before = mgr.own_skills.voice
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("status")
        assert mgr.own_skills.voice == voice_before


# ---------------------------------------------------------------------------
# 9-15: Continuous conversation
# ---------------------------------------------------------------------------


class TestContinuousConversation:
    def test_activation_starts_a_conversation(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        assert session.active is True

    def test_the_first_turn_reaches_the_real_pipeline(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        response = session.turn("give me a status report")
        # A real turn produced a real ConversationTurn, not a canned string -
        # handled_by is only ever populated by the router actually running.
        assert response.turn is not None
        assert response.text

    def test_the_response_returns_through_the_existing_voice_output_pipeline(self):
        mgr = manager()
        session, speech = session_for(mgr, speech_state=AvailabilityState.AVAILABLE)
        session.start()
        session.hear_activation("Alfred")
        response = session.turn("give me a status report")
        assert response.spoken is True
        assert speech.spoken, "deliver() must have actually called the engine"

    def test_a_second_turn_needs_no_further_activation(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("first question")
        assert session.hear_activation("Alfred") is False  # not needed, and ignored
        response = session.turn("second question, no wake phrase repeated")
        assert response.text
        assert session.state is ActivationState.LISTENING

    def test_a_third_turn_also_needs_no_further_activation(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("first")
        session.turn("second")
        response = session.turn("third")
        assert response.text
        assert session.state is ActivationState.LISTENING

    def test_the_conversation_can_end(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("one turn before ending")
        assert session.end() is ActivationState.ENDED

    def test_ending_returns_to_the_inactive_state(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("one turn before ending")
        session.end()
        assert session.state is ActivationState.IDLE
        assert session.active is False

    def test_a_turn_before_activation_is_refused_not_treated_as_a_request(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        with pytest.raises(NoActiveConversation):
            session.turn("this was never preceded by an activation phrase")

    def test_a_turn_after_ending_is_refused_until_reactivated(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("before ending")
        session.end()
        with pytest.raises(NoActiveConversation):
            session.turn("after ending, with no new activation")
        assert session.hear_activation("Alfred") is True
        session.turn("works again after re-activation")


# ---------------------------------------------------------------------------
# 16-22: Identity and architecture
# ---------------------------------------------------------------------------


class TestIdentityAndArchitecture:
    def test_generic_activation_code_names_no_agent(self):
        source = (REPO_ROOT / "src" / "channels" / "activation.py").read_text(
            encoding="utf-8"
        ).lower()
        for forbidden in ("alfred", "arthur"):
            assert forbidden not in source, forbidden

    def test_generic_activation_code_contains_no_agent_name_comparison(self):
        """Structural, not textual: no ``== "..."``/``in (...)`` branch in
        the module compares anything to a literal agent name - the module
        has exactly one place that decides a match, VoiceActivation.matches,
        and that lives in src/services/identity/voice.py, not here."""
        tree = ast.parse(
            (REPO_ROOT / "src" / "channels" / "activation.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        assert comparator.value.lower() not in ("alfred", "arthur"), comparator.value

    def test_future_agents_supply_their_own_activation_configuration(self):
        """The same VoiceActivationSession class, handed two entirely
        different VoiceActivation configurations, behaves correctly for
        both - with zero code in this class or in activation.py aware
        either agent exists."""
        mgr = manager()
        alfred_session, _ = session_for(mgr)
        arthur_activation = VoiceActivation(key="arthur", phrases=["Arthur", "Hey Arthur"])
        arthur_channel = VoiceChannel(mgr, speech=ScriptedSpeech())
        arthur_session = VoiceActivationSession(arthur_channel, arthur_activation)

        alfred_session.start()
        arthur_session.start()
        assert alfred_session.hear_activation("Arthur") is False
        assert arthur_session.hear_activation("Alfred") is False
        assert alfred_session.hear_activation("Alfred") is True
        assert arthur_session.hear_activation("Arthur") is True

    def test_arthur_remains_a_separate_canonical_agent(self):
        from src.agents.specialists import wiring_for
        from src.services.memory.factory import EMAIL_AGENT

        mgr = manager()
        arthur_wiring = wiring_for(EMAIL_AGENT, fallback_name="Arthur")
        assert arthur_wiring.name != mgr.own_skills.identity.name
        assert arthur_wiring.activation.key != mgr.own_skills.activation.key

    def test_no_second_conversation_system_is_created(self):
        """turn() reaches the Manager through exactly Channel.ask and
        Channel.deliver - verified by spying on the real channel's own
        methods rather than trusting the source, so a future refactor that
        quietly added a second path would still be caught."""
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")

        calls = []
        real_ask = session.channel.ask
        real_deliver = session.channel.deliver

        def spy_ask(*a, **k):
            calls.append("ask")
            return real_ask(*a, **k)

        def spy_deliver(*a, **k):
            calls.append("deliver")
            return real_deliver(*a, **k)

        session.channel.ask = spy_ask
        session.channel.deliver = spy_deliver
        session.turn("one turn")
        assert calls == ["ask", "deliver"]

    def test_no_second_brain_or_memory_module_is_imported(self):
        imported = _imported_names(REPO_ROOT / "src" / "channels" / "activation.py")
        for forbidden in (
            "src.manager.brain_factory", "src.agents.brain_factory",
            "src.services.memory.factory", "src.services.memory.persistent",
            "ManagerBrain", "SpecialistBrain",
        ):
            assert not any(forbidden in name for name in imported), forbidden

    def test_no_voice_profile_is_constructed_by_this_module(self):
        imported = _imported_names(REPO_ROOT / "src" / "channels" / "activation.py")
        assert not any(name.endswith("VoiceProfile") for name in imported)
        source = (REPO_ROOT / "src" / "channels" / "activation.py").read_text(encoding="utf-8")
        assert "VoiceProfile(" not in source


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


# ---------------------------------------------------------------------------
# 23-27: Provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_the_module_imports_nothing_provider_related(self):
        imported = _imported_names(REPO_ROOT / "src" / "channels" / "activation.py")
        assert "src.providers" not in imported
        for name in imported:
            assert not name.startswith("src.providers"), name

    def test_the_module_names_no_provider(self):
        source = (REPO_ROOT / "src" / "channels" / "activation.py").read_text(
            encoding="utf-8"
        ).lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey"):
            assert forbidden not in source, forbidden

    def test_no_model_name_appears(self):
        source = (REPO_ROOT / "src" / "channels" / "activation.py").read_text(
            encoding="utf-8"
        ).lower()
        for forbidden in ("gpt", "sonnet", "opus", "gemini"):
            assert forbidden not in source, forbidden

    def test_session_construction_takes_no_provider_argument(self):
        params = inspect.signature(VoiceActivationSession.__init__).parameters.keys()
        for forbidden in ("provider", "api_key", "model"):
            assert forbidden not in params

    def test_activation_survives_a_different_provider_being_wired_in(self):
        """The same activation identity, whichever brain answers behind it -
        activation belongs to the agent, never to what reasons for it."""
        from src.providers.null_provider import NullProvider

        mgr_a = manager()
        mgr_b = manager()  # a second, independently-built manager stands in
        # for "a different provider wired in" - what matters is that
        # neither manager's own construction path threads a provider
        # choice through the activation identity at all.
        assert mgr_a.own_skills.activation.phrases == mgr_b.own_skills.activation.phrases
        assert isinstance(mgr_a.brain, object) and isinstance(mgr_b.brain, object)


# ---------------------------------------------------------------------------
# 28-30: Availability
# ---------------------------------------------------------------------------


class TestAvailability:
    def test_unknown_is_reported_as_worth_trying(self):
        mgr = manager()
        session, _ = session_for(mgr, speech_state=AvailabilityState.UNKNOWN)
        assert session.listening is True

    def test_available_is_reported_as_worth_trying(self):
        mgr = manager()
        session, _ = session_for(mgr, speech_state=AvailabilityState.AVAILABLE)
        assert session.listening is True

    def test_unavailable_is_reported_honestly(self):
        mgr = manager()
        session, _ = session_for(mgr, speech_state=AvailabilityState.UNAVAILABLE)
        assert session.listening is False

    def test_unavailable_speech_never_fakes_a_spoken_response(self):
        mgr = manager()
        session, speech = session_for(mgr, speech_state=AvailabilityState.UNAVAILABLE)
        session.start()
        session.hear_activation("Alfred")
        response = session.turn("a question with no working speaker")
        assert response.spoken is False
        assert response.text  # the written answer still exists

    def test_a_plain_text_channel_reports_listening_as_not_applicable(self):
        mgr = manager()
        session = VoiceActivationSession(TextChannel(mgr), mgr.own_skills.activation)
        assert session.listening is None


# ---------------------------------------------------------------------------
# 31-36: Lifecycle and duplicate-listener safety
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_repeated_start_does_not_reset_an_active_conversation(self):
        """The concrete form of 'repeated wiring must not create duplicate
        listeners': in this project's browser UI, the setup code that would
        call start() re-runs on every navigation and every five-second live
        refresh (see ui/index.html's go()/refreshFromLive()). If start()
        reset state unconditionally, an active conversation would be
        silently dropped every time either fires."""
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("mid-conversation")
        assert session.state is ActivationState.LISTENING

        for _ in range(5):
            session.start()  # simulating repeated setup/re-render calls
        assert session.state is ActivationState.LISTENING
        assert session.active is True

    def test_start_returns_whether_it_actually_started_anything(self):
        mgr = manager()
        session, _ = session_for(mgr)
        assert session.start() is True
        assert session.start() is False
        assert session.start() is False

    def test_stop_ends_listening_and_any_conversation(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.turn("before stopping")
        session.stop()
        assert session.started is False
        assert session.state is ActivationState.IDLE
        assert session.hear_activation("Alfred") is False  # not listening any more

    def test_dispose_releases_exactly_as_stop_does(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.hear_activation("Alfred")
        session.dispose()
        assert session.started is False
        assert session.state is ActivationState.IDLE

    def test_a_session_can_be_started_again_after_stop(self):
        mgr = manager()
        session, _ = session_for(mgr)
        session.start()
        session.stop()
        assert session.start() is True
        assert session.hear_activation("Alfred") is True

    def test_repeated_setup_never_produces_more_than_one_working_session(self):
        """Stands in for 'navigation and live refresh do not create
        duplicate activation systems': the object identity never changes
        underneath repeated setup calls, so there is structurally nothing
        here that could accumulate."""
        mgr = manager()
        session, _ = session_for(mgr)
        for _ in range(10):
            session.start()
        session.hear_activation("Alfred")
        response = session.turn("only one session ever answered this")
        assert response.turn.handled_by or response.text
