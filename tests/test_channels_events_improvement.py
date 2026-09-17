"""Four foundations, and the boundaries that keep them foundations.

This phase added ways of reaching Alfred, ways of describing what happened,
ways of declaring an interaction surface, and a way of noticing what could be
better. Each is a contract with a small honest implementation behind it, and
each has one thing it must never become:

    a channel        must never become a second Alfred
    an event         must never become a phrase book
    an interface     must never become a permission
    an improvement   must never become a change

Those four sentences are what most of this file tests. The features are easy;
the boundaries are the part that has to still be true in a year.
"""

import ast
from pathlib import Path

import pytest

from src.channels.channel import Response, TextChannel, VoiceChannel, speakable
from src.channels.speech import NoSpeech, SpeechAdapter, SpeechUnavailable
from src.control_center.interfaces import (
    InterfaceDescriptor,
    InterfaceFeature,
    default_descriptor,
    describe_interface,
)
from src.core.contracts import Task
from src.events.events import (
    EventLog,
    Significance,
    SystemEvent,
    announceable_only,
    brain_mode_changed,
    capability_became_available,
    delegation,
    routine,
    run_failed,
    tool_became_unavailable,
    work_completed,
)
from src.events.narrator import announcements, compose, compose_with_brain
from src.improvement.governance import ImprovementBoard, improvement_is_advisory_only
from src.improvement.improvement import (
    ChangeKind,
    ImprovementRecord,
    Observation,
    Proposal,
    RefusedProposal,
    Severity,
)
from src.improvement.observers import observe_system
from src.providers.availability import Availability, AvailabilityState
from src.services.email.in_memory_mailbox import InMemoryMailbox

REPO_ROOT = Path(__file__).resolve().parent.parent
NEW_PACKAGES = (
    "src/channels",
    "src/events",
    "src/improvement",
    # The conversation package joins this list rather than growing its own
    # copy of these rules. A room that could import an agent, a brain or a
    # registry would be the exact leak it exists to prevent, and the three
    # checks below already say so - so isolation costs one line here.
    "src/conversation",
)


def docstring_nodes(tree):
    """Every string Constant that is actually a docstring.

    Identified by position rather than by value: ast.get_docstring cleans and
    dedents what it returns, so comparing text finds nothing. A docstring is
    the first statement of a module, class or function, and that is what this
    matches - so prose may name an agent while code may not.
    """
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


def manager(**overrides):
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class FakeSpeech(SpeechAdapter):
    """A speech engine that exists only here.

    Records what it was asked to say rather than saying it, because a test
    that needed a microphone would be a test nobody could run.

    Publishes an ``Availability`` and inherits ``available`` from the contract
    rather than answering it directly. A double that declared itself usable
    would be modelling the bug the three-state contract exists to prevent, and
    every test written against it would be testing a world that cannot happen.

    **Two different things, kept apart.** ``available=`` says whether the
    pretend engine *works* - it stands in for a microphone being plugged in.
    ``availability`` is the separate record of what calls have actually
    revealed, and it starts UNKNOWN however capable the engine is, because
    nothing has been observed yet. Collapsing those two was the deadlock: an
    engine that had to already be proven before it was allowed to prove itself.

    So the operation is always attempted and the result is recorded, which is
    what a real adapter does and what lets a fresh double travel
    UNKNOWN -> AVAILABLE by being used.
    """

    def __init__(self, hears="", available=True):
        self._hears = hears
        # The pretend hardware, and public so a test can unplug it or plug it
        # back in mid-run. Not evidence, and never read as availability - it is
        # the thing the recorded evidence is *about*.
        self.works = available
        # UNKNOWN until a call happens, exactly as a real engine begins.
        self._availability = Availability()
        self.said: list[str] = []
        # Which voice each utterance was asked for, positionally alongside
        # `said`. None means the caller expressed no preference.
        self.voices: list[object] = []

    @property
    def name(self):
        return "fake"

    @property
    def availability(self):
        return self._availability

    def _succeeded(self):
        self._availability = Availability(
            state=AvailabilityState.AVAILABLE, reason="a call succeeded"
        )

    def _failed(self, exc):
        """Record a failure from the exception itself, as LLMManagerBrain does.

        Deriving ``reason`` and ``error_type`` from a real exception object
        rather than from hand-written strings is what keeps the double honest:
        a fake that invented tidier failure text than a real engine produces
        would let a test pass on evidence the system will never actually see.
        """
        self._availability = Availability(
            state=AvailabilityState.UNAVAILABLE,
            reason=str(exc) or type(exc).__name__,
            error_type=type(exc).__name__,
        )

    def listen(self, timeout=None):
        if not self.works:
            failure = SpeechUnavailable("nothing to listen with")
            self._failed(failure)
            raise failure
        self._succeeded()
        return self._hears

    def speak(self, text, voice=None):
        """Records the voice it was asked for as well as the words.

        This is how per-agent voices are proven without an engine: the double
        cannot make a sound, but it can say truthfully which voice was
        requested, and that is the architectural claim.
        """
        if not self.works:
            self._failed(SpeechUnavailable("nothing to speak with"))
            return False
        self._succeeded()
        self.said.append(text)
        self.voices.append(voice)
        return True


# ---------------------------------------------------------------------------
# Voice is a door, not a second Alfred
# ---------------------------------------------------------------------------


class TestVoiceIsAChannelNotAnAgent:
    def test_text_and_voice_reach_the_same_manager(self):
        alfred = manager()
        text, voice = TextChannel(alfred), VoiceChannel(alfred, FakeSpeech())
        assert text.manager is voice.manager is alfred

    def test_the_same_request_gets_the_same_answer_either_way(self):
        alfred = manager()
        text = TextChannel(alfred).ask("tell me about a subject")
        voice = VoiceChannel(alfred, FakeSpeech()).ask("tell me about a subject")
        assert text.text == voice.text

    def test_voice_does_not_bypass_the_brain(self):
        """The turn a voice request produces carries the same brain mode the
        Manager was in - because it is the same Manager, asked the same way."""
        alfred = manager()
        response = VoiceChannel(alfred, FakeSpeech()).ask("roster")
        from src.manager.cognition import assess_manager

        assert response.turn.brain_mode == assess_manager(alfred).mode.label

    def test_voice_does_not_bypass_capability_or_tool_checks(self):
        alfred = manager()
        response = VoiceChannel(alfred, FakeSpeech()).ask("research a subject")
        assert response.turn.tool_used == "web_search"
        assert response.turn.tool_available is False
        assert response.turn.succeeded is False

    def test_voice_does_not_bypass_trust_in_stages(self):
        from src.services.email.gmail_auth import SCOPES

        alfred = manager()
        response = VoiceChannel(alfred, FakeSpeech()).ask("send an email to my team")
        assert response.turn.succeeded is False
        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)

    def test_every_channel_goes_through_the_one_observe(self):
        """Structural. A channel that reached the Manager another way would be
        a second orchestration, so no channel may call handle_task itself."""
        source = (REPO_ROOT / "src" / "channels" / "channel.py").read_text(encoding="utf-8")
        assert "observe(" in source
        assert "handle_task" not in source

    def test_a_channel_holds_no_brain_memory_or_registry(self):
        alfred = manager()
        for channel in (TextChannel(alfred), VoiceChannel(alfred, FakeSpeech())):
            for forbidden in ("brain", "memory", "registry", "router", "tools"):
                assert not hasattr(channel, forbidden), forbidden


class TestSpeechIsHonestAboutItself:
    def test_the_shipped_adapter_reports_that_it_cannot_speak(self):
        """Asserted on the evidence, not only on the derived boolean.

        ``available`` is now computed from ``availability``, so checking it
        alone would pass for an adapter reporting UNKNOWN as readily as one
        reporting UNAVAILABLE - and those are different situations. The state
        and the reason are what tell them apart.
        """
        speech = NoSpeech()
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert speech.availability.is_usable is False
        assert speech.availability.reason == "no speech engine is installed"
        assert speech.available is False
        assert speech.speak("anything") is False

    def test_listening_without_an_engine_raises_rather_than_returning_nothing(self):
        """An empty string would be indistinguishable from somebody saying
        nothing, so a missing engine has to be an exception."""
        with pytest.raises(SpeechUnavailable):
            NoSpeech().listen()

    def test_the_written_answer_survives_when_speech_fails(self):
        channel = VoiceChannel(manager(), NoSpeech())
        delivered = channel.deliver(channel.ask("roster"))
        assert delivered.text.strip()
        assert delivered.spoken is False
        assert delivered.channel_note == "written only"

    def test_a_working_engine_actually_says_the_answer(self):
        """Asserted after delivery, because delivery is when speech happens.

        This test used to call ask() alone and assert spoken was True, which
        passed only because ask() spoke as well as deliver() did. Asserting
        the same thing one call later is what makes the double-speak visible
        instead of hiding it.
        """
        speech = FakeSpeech()
        channel = VoiceChannel(manager(), speech)
        delivered = channel.deliver(channel.ask("roster"))
        assert delivered.spoken is True
        assert speech.said and speech.said[0] == delivered.speech

    def test_asking_does_not_speak(self):
        """ask() obtains an answer. It does not present one, so it says nothing."""
        speech = FakeSpeech()
        response = VoiceChannel(manager(), speech).ask("roster")
        assert speech.said == []
        assert response.spoken is False

    def test_asking_then_delivering_speaks_exactly_once(self):
        """The regression this whole change exists for.

        ``deliver(ask(x))`` is how any real loop is written. When ask() spoke
        too, that said every answer twice - invisible in this project because
        the only shipped engine is silent, and immediately audible the moment
        one is not.
        """
        speech = FakeSpeech()
        channel = VoiceChannel(manager(), speech)
        channel.deliver(channel.ask("roster"))
        assert len(speech.said) == 1

    def test_an_unavailable_engine_leaves_the_answer_unspoken(self):
        speech = FakeSpeech(available=False)
        channel = VoiceChannel(manager(), speech)
        delivered = channel.deliver(channel.ask("roster"))
        assert delivered.spoken is False
        assert speech.said == []

    def test_typing_delivers_the_response_unchanged_and_never_speaks(self):
        channel = TextChannel(manager())
        response = channel.ask("roster")
        assert channel.deliver(response) is response
        assert response.spoken is False

    def test_voice_falls_back_to_typing_rather_than_refusing(self):
        """Voice is a convenience. It must never become a prerequisite."""
        channel = VoiceChannel(manager(), NoSpeech())
        assert channel.listening is False
        assert channel._fallback.name == "text"

    def test_what_is_spoken_comes_from_the_real_answer(self):
        """Not from a list of things Alfred is allowed to say."""
        alfred = manager()
        response = VoiceChannel(alfred, FakeSpeech()).ask("research a subject")
        assert response.speech
        assert response.speech in response.text or response.text.startswith(
            response.speech[:40]
        )

    def test_a_long_answer_is_shortened_for_speech_but_not_for_reading(self):
        from src.control_center.views import ConversationTurn

        long = ". ".join(f"sentence number {i}" for i in range(200))
        turn = ConversationTurn(
            request="x", handled_by="a", delegated=False, steps=(),
            outcome=long, succeeded=True,
        )
        spoken = speakable(turn)
        assert len(spoken) < len(long)
        assert spoken


class TestAnUnprovenEngineIsNotLockedOut:
    """The R1 deadlock: availability must never gate the call that creates it.

    `available` is derived from `availability.is_usable`, and is_usable is True
    only after a call has succeeded. So a check of the form "call listen() only
    if available" cannot ever be satisfied by an engine that has not yet been
    called - it is refused, so it is never used, so it stays unproven, so it is
    refused again. Absorbing, silent, and fatal to the first real microphone.
    """

    def test_a_fresh_engine_begins_unproven_rather_than_usable(self):
        speech = FakeSpeech(hears="scan my inbox")
        assert speech.availability.state is AvailabilityState.UNKNOWN
        assert speech.available is False

    def test_an_unproven_engine_is_still_given_a_chance_to_listen(self):
        """The proof. A working engine that has never been called is UNKNOWN,
        so `available` is False - and it must still reach listen()."""
        speech = FakeSpeech(hears="scan my inbox")
        assert speech.available is False

        heard = VoiceChannel(manager(), speech).receive()

        assert heard == "scan my inbox"

    def test_being_used_is_what_produces_the_evidence(self):
        speech = FakeSpeech(hears="scan my inbox")
        VoiceChannel(manager(), speech).receive()
        assert speech.availability.state is AvailabilityState.AVAILABLE
        assert speech.available is True

    def test_a_failing_engine_records_that_and_still_falls_back(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "typed instead")
        speech = FakeSpeech(available=False)

        heard = VoiceChannel(manager(), speech).receive()

        assert heard == "typed instead"
        assert speech.availability.state is AvailabilityState.UNAVAILABLE

    def test_a_missing_engine_still_falls_back_to_typing(self, monkeypatch):
        """NoSpeech behaviour is unchanged by removing the pre-gate: it raises,
        the except catches it, and typing takes over exactly as before."""
        monkeypatch.setattr("builtins.input", lambda prompt="": "typed instead")
        speech = NoSpeech()

        heard = VoiceChannel(manager(), speech).receive()

        assert heard == "typed instead"
        assert speech.availability.state is AvailabilityState.UNAVAILABLE

    def test_listening_reports_worth_trying_not_proven_usable(self):
        """An unproven engine is worth trying, and saying otherwise would be
        claiming knowledge nobody has."""
        assert VoiceChannel(manager(), NoSpeech()).listening is False

        fresh = FakeSpeech()
        assert fresh.availability.state is AvailabilityState.UNKNOWN
        assert fresh.available is False
        assert VoiceChannel(manager(), fresh).listening is True


# ---------------------------------------------------------------------------
# The three-state speech contract, proven end to end
# ---------------------------------------------------------------------------


class TestTheSpeechAvailabilityContract:
    """Speech reuses the model built for the Brain, so it must behave like it.

    The same three states, the same meaning, the same rule that only a call
    that came back produces AVAILABLE. These mirror
    `test_capability_floor_and_brain_modes.py`, which pins the identical claims
    for LLMManagerBrain - written here as well rather than assumed, because the
    guarantee belongs to whatever publishes an Availability, and a shared type
    is not a shared behaviour until something checks.
    """

    def test_a_fresh_engine_is_unknown_not_usable(self):
        speech = FakeSpeech()
        assert speech.availability.state is AvailabilityState.UNKNOWN
        assert speech.availability.is_usable is False
        assert speech.available is False

    def test_unknown_is_not_the_same_as_broken(self):
        """The distinction the third state exists for. Nothing is known about a
        fresh engine; something *is* known about NoSpeech."""
        assert FakeSpeech().availability.is_known_broken is False
        assert NoSpeech().availability.is_known_broken is True

    def test_a_successful_listen_is_what_produces_availability(self):
        speech = FakeSpeech(hears="something")
        speech.listen()
        assert speech.availability.state is AvailabilityState.AVAILABLE
        assert speech.availability.reason == "a call succeeded"
        assert speech.available is True

    def test_a_successful_speak_is_what_produces_availability(self):
        speech = FakeSpeech()
        assert speech.speak("hello") is True
        assert speech.availability.state is AvailabilityState.AVAILABLE
        assert speech.available is True

    def test_a_failed_listen_records_the_reason_and_the_error_type(self):
        speech = FakeSpeech(available=False)
        with pytest.raises(SpeechUnavailable):
            speech.listen()
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert speech.available is False
        assert speech.availability.reason == "nothing to listen with"
        assert speech.availability.error_type == "SpeechUnavailable"

    def test_a_failed_speak_records_the_reason_and_the_error_type(self):
        speech = FakeSpeech(available=False)
        assert speech.speak("hello") is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert speech.available is False
        assert speech.availability.reason == "nothing to speak with"
        assert speech.availability.error_type == "SpeechUnavailable"

    def test_a_failure_is_the_latest_observation_not_a_permanent_verdict(self):
        """Recovery. A device that was busy, a connection that dropped, a rate
        limit that cleared - none of those should condemn an engine forever,
        and `Availability` records the last call rather than the worst one."""
        speech = FakeSpeech()

        speech.works = False
        assert speech.speak("first") is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert speech.available is False

        speech.works = True
        assert speech.speak("second") is True
        assert speech.availability.state is AvailabilityState.AVAILABLE
        assert speech.available is True

    def test_recovery_works_through_the_channel_too(self):
        speech = FakeSpeech(hears="heard it")
        channel = VoiceChannel(manager(), speech)

        speech.works = False
        channel.deliver(channel.ask("roster"))
        assert speech.available is False

        speech.works = True
        assert channel.receive() == "heard it"
        assert speech.available is True


class TestNoSpeechIsStructurallyUnavailable:
    """NoSpeech does not *observe* that it cannot speak - it simply cannot.

    Every other adapter's availability is a record of what calls revealed.
    NoSpeech's is a fixed fact about a class that implements no engine, which
    is why it is UNAVAILABLE from construction rather than UNKNOWN, and why no
    amount of using it can move it.
    """

    def test_it_is_unavailable_before_anything_is_called(self):
        speech = NoSpeech()
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert speech.availability.is_usable is False
        assert speech.availability.reason == "no speech engine is installed"
        assert speech.available is False

    def test_using_it_does_not_change_what_it_reports(self):
        speech = NoSpeech()
        before = speech.availability

        assert speech.speak("anything") is False
        with pytest.raises(SpeechUnavailable):
            speech.listen()

        assert speech.availability is before
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert speech.available is False

    def test_it_still_refuses_in_the_ways_callers_depend_on(self):
        speech = NoSpeech()
        assert speech.speak("anything") is False
        with pytest.raises(SpeechUnavailable):
            speech.listen()


class TestAvailabilityIsTheOnlySourceOfTruth:
    """Structural. `available` must stay derived, everywhere and for everyone.

    Behavioural tests prove the two implementations that exist today happen to
    agree. This proves a third one *cannot* disagree: the natural thing to
    write against a boolean contract is `return self._client is not None`, and
    that single line is the Full Brain bug wearing a different name. A subclass
    that answers `available` for itself has to fail here first.

    Checked against parsed code in the style of
    `test_architecture_audit.py::test_no_outbox_implementation_exists`, and
    across tests and scripts as well as src - a test double that declared
    itself usable would quietly validate a world the system cannot reach.
    """

    SEARCHED = ("src", "tests", "scripts")

    def _speech_adapters(self):
        """Every module-level SpeechAdapter subclass in the project.

        Module level only, and that boundary is deliberate rather than
        convenient. A class defined at module scope is an adapter something can
        import and use; one defined inside a test body is a throwaway built for
        that test alone, and two of them below exist *precisely* to violate the
        contract in order to prove it is enforced. Scanning those would make
        this test fail on its own evidence.
        """
        found = []
        for area in self.SEARCHED:
            for path in sorted((REPO_ROOT / area).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in tree.body:
                    if not isinstance(node, ast.ClassDef):
                        continue
                    bases = {
                        getattr(b, "id", getattr(b, "attr", "")) for b in node.bases
                    }
                    if "SpeechAdapter" in bases:
                        found.append((path, node))
        return found

    def test_the_search_actually_finds_the_adapters(self):
        """A structural test that matched nothing would pass forever."""
        names = {node.name for _, node in self._speech_adapters()}
        assert "NoSpeech" in names
        assert "FakeSpeech" in names

    def test_no_subclass_defines_its_own_available(self):
        for path, node in self._speech_adapters():
            defined = {
                member.name
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert "available" not in defined, (path.name, node.name)

    def test_every_subclass_answers_availability_instead(self):
        for path, node in self._speech_adapters():
            defined = {
                member.name
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert "availability" in defined, (path.name, node.name)

    def test_the_contract_requires_availability_and_derives_available(self):
        """The runtime half: one is abstract, the other cannot be."""
        assert "availability" in SpeechAdapter.__abstractmethods__
        assert "available" not in SpeechAdapter.__abstractmethods__

    def test_an_adapter_answering_only_availability_is_complete(self):
        """Nothing else is required to satisfy the contract, so nothing else
        can be quietly demanded of a future engine."""

        class OnlyAvailability(SpeechAdapter):
            @property
            def name(self):
                return "probe"

            @property
            def availability(self):
                return Availability()

            def listen(self, timeout=None):
                return ""

            def speak(self, text):
                return False

        probe = OnlyAvailability()
        assert probe.available is False  # UNKNOWN is not usable

    def test_omitting_availability_makes_an_adapter_unconstructible(self):
        class ForgotAvailability(SpeechAdapter):
            @property
            def name(self):
                return "broken"

            def listen(self, timeout=None):
                return ""

            def speak(self, text):
                return False

        with pytest.raises(TypeError):
            ForgotAvailability()


# ---------------------------------------------------------------------------
# Events describe facts; the narrator finds the words
# ---------------------------------------------------------------------------


class TestEventsCarryFactsNotSentences:
    def test_an_event_holds_values_rather_than_wording(self):
        event = capability_became_available("research", "web_search", "Manager")
        assert event.facts["capability"] == "research"
        assert event.facts["tool"] == "web_search"
        for field in ("sentence", "message", "phrase", "text"):
            assert field not in event.facts

    def test_only_important_events_may_be_announced(self):
        assert capability_became_available("x", None, "a").announceable is True
        assert delegation("a", "b", "because").announceable is False
        assert routine("memory.written", "a").announceable is False

    def test_routine_events_are_not_even_recorded_as_history(self):
        assert routine("x", "a").significance.worth_recording is False
        assert delegation("a", "b", "r").significance.worth_recording is True

    def test_work_that_found_nothing_is_not_announced(self):
        """The quiet-run rule the heartbeat already lives by."""
        assert work_completed("a", "nothing new", findings=0).announceable is False
        assert work_completed("a", "3 found", findings=3).announceable is True

    def test_announceable_filtering_happens_in_one_place(self):
        events = [
            capability_became_available("x", None, "a"),
            routine("y", "a"),
            delegation("a", "b", "r"),
        ]
        assert len(announceable_only(events)) == 1

    def test_the_log_is_bounded(self):
        log = EventLog(limit=3)
        for index in range(10):
            log.record(routine(f"k{index}", "a"))
        assert len(log) == 3

    def test_the_log_can_be_queried_without_being_dumped(self):
        log = EventLog()
        log.record(capability_became_available("x", None, "Manager"))
        log.record(routine("noise", "Manager"))
        assert len(log.announceable()) == 1
        assert len(log.by("Manager")) == 2
        assert len(log.of_kind("noise")) == 1


class TestTheNarratorIsNotAPhraseBook:
    def test_wording_is_built_from_the_events_own_values(self):
        said = compose(capability_became_available("pattern recognition", "recogniser", "M"))
        assert "pattern recognition" in said

    def test_an_unfamiliar_event_is_described_rather_than_forced(self):
        """A kind nobody wrote a frame for still narrates truthfully."""
        said = compose(SystemEvent(kind="something.new", actor="Manager"))
        assert "something.new" in said
        assert said.strip()

    def test_the_same_event_always_reads_the_same_way(self):
        """Variation is stable, not random - otherwise nothing is testable."""
        event = capability_became_available("research", "web_search", "M")
        assert len({compose(event) for _ in range(20)}) == 1

    def test_different_events_do_not_all_sound_identical(self):
        wordings = {
            compose(capability_became_available(name, None, "M"))
            for name in ("alpha", "beta", "gamma", "delta", "epsilon")
        }
        assert len(wordings) == 5

    def test_no_capability_name_is_written_into_the_narrator(self):
        """The frames are grammar. The nouns come from events."""
        source = (REPO_ROOT / "src" / "events" / "narrator.py").read_text(encoding="utf-8")
        for subject in ("liverpool", "titanic", "elon", "football", "recognition"):
            assert subject not in source.lower(), subject

    def test_announcing_filters_before_it_phrases(self):
        said = announcements(
            [capability_became_available("x", None, "M"), routine("noise", "M")]
        )
        assert len(said) == 1

    def test_narration_works_with_no_model_at_all(self):
        """Half Brain must still be able to describe its own system."""
        assert compose_with_brain(run_failed("M", "disk full"), None).strip()

    def test_an_unverified_brain_is_not_asked_to_phrase_anything(self):
        """Asking an unproven provider would stall or fail, and the
        deterministic wording is already correct."""
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        calls = []

        class Counting(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                calls.append(1)
                return Completion(text="{}", provider="anthropic")

        event = capability_became_available("x", None, "M")
        assert compose_with_brain(event, LLMManagerBrain(Counting())) == compose(event)
        assert calls == []

    def test_a_failing_brain_falls_back_instead_of_breaking(self):
        class Broken:
            name = "broken"

            def deliberate(self, *args, **kwargs):
                raise RuntimeError("no")

        event = run_failed("M", "something")
        assert compose_with_brain(event, Broken()) == compose(event)


# ---------------------------------------------------------------------------
# A specialist interface is a shape, never a permission
# ---------------------------------------------------------------------------


class TestSpecialistInterfacesAreDiscovered:
    def test_an_agent_that_declares_nothing_gets_a_real_default(self):
        descriptor = describe_interface(object())
        assert InterfaceFeature.CHAT in descriptor.features
        assert descriptor.can_render(InterfaceFeature.CHAT)

    def test_a_declaring_agent_is_taken_at_its_word(self):
        alfred = manager()
        arthur = alfred.registry.get("Arthur")
        descriptor = describe_interface(arthur)
        assert descriptor.offers(InterfaceFeature.MAILBOX)
        assert descriptor.offers(InterfaceFeature.OPPORTUNITIES)

    def test_declared_but_unbuilt_surfaces_are_shown_as_planned(self):
        """The same honesty a capability without its tool gets."""
        arthur = manager().registry.get("Arthur")
        descriptor = describe_interface(arthur)
        assert InterfaceFeature.MAILBOX in descriptor.planned
        assert not descriptor.can_render(InterfaceFeature.MAILBOX)
        assert "planned" in descriptor.describe()

    def test_a_surface_cannot_be_implemented_without_being_offered(self):
        descriptor = InterfaceDescriptor(
            agent="x",
            display_name="x",
            features=frozenset({InterfaceFeature.CHAT}),
            implemented=frozenset({InterfaceFeature.CODE}),
        )
        assert descriptor.implemented == frozenset()

    def test_declaring_a_surface_grants_no_capability(self):
        """A descriptor is what a screen draws, never what an agent may do."""
        alfred = manager()
        arthur = alfred.registry.get("Arthur")
        before = set(arthur.spec.capabilities)
        describe_interface(arthur)
        assert set(arthur.spec.capabilities) == before
        assert arthur.can_send is False

    def test_declaring_code_does_not_create_a_coding_tool(self):
        descriptor = InterfaceDescriptor(
            agent="x",
            display_name="x",
            features=frozenset({InterfaceFeature.CODE, InterfaceFeature.FILES}),
            implemented=frozenset({InterfaceFeature.CODE}),
        )
        assert descriptor.has_workspace
        assert manager().own_skills.tools.is_empty

    def test_no_future_specialist_is_named_in_the_contract(self):
        source = (REPO_ROOT / "src" / "control_center" / "interfaces.py").read_text(
            encoding="utf-8"
        )
        for name in ("coding_agent", "research_agent", "sports_agent", "media_agent"):
            assert name not in source.lower(), name

    def test_features_are_kinds_of_surface_not_subjects(self):
        for feature in InterfaceFeature:
            for subject in ("liverpool", "football", "movie", "history"):
                assert subject not in feature.value


# ---------------------------------------------------------------------------
# Self-improvement observes and proposes. It cannot change anything.
# ---------------------------------------------------------------------------


class TestImprovementCannotModifyAnything:
    def test_the_boundary_is_stated_as_an_assertion(self):
        assert improvement_is_advisory_only() is True

    def test_a_proposal_always_requires_approval(self):
        proposal = Proposal(
            title="t", rationale="r", change=ChangeKind.INVESTIGATE,
            requires_approval=False,
        )
        assert proposal.requires_approval is True
        assert proposal.is_actionable_without_a_person is False

    def test_nothing_in_the_package_can_write_execute_or_install(self):
        """Structural. There is no apply path, and this is what would have to
        be edited to add one."""
        forbidden_calls = {"system", "popen", "run", "call", "check_output", "exec", "eval"}
        forbidden_imports = {"subprocess", "shutil", "os", "importlib", "pip"}

        for package in ("src/improvement",):
            for path in sorted((REPO_ROOT / package).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            assert alias.name.split(".")[0] not in forbidden_imports, (
                                path.name, alias.name
                            )
                    if isinstance(node, ast.ImportFrom) and node.module:
                        assert node.module.split(".")[0] not in forbidden_imports, (
                            path.name, node.module
                        )
                    if isinstance(node, ast.Call):
                        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                        assert name not in forbidden_calls, (path.name, name)
                        assert name != "open", path.name

    def test_no_proposal_type_has_an_apply_method(self):
        for kind in (Proposal, ImprovementRecord, ImprovementBoard):
            for forbidden in ("apply", "execute", "run", "commit", "write", "patch"):
                assert not hasattr(kind, forbidden), (kind.__name__, forbidden)

    def test_a_proposal_to_widen_a_permission_is_refused(self):
        for attempt in (
            "add gmail.send so replies can be automated",
            "auto-apply for matching roles",
            "expand permission for the mailbox",
        ):
            with pytest.raises(RefusedProposal):
                Proposal(title="improve", rationale=attempt, change=ChangeKind.ADJUST_WORKFLOW)

    def test_a_proposal_to_weaken_a_test_is_refused(self):
        for attempt in (
            "disable the test that blocks this",
            "remove the audit so the change can land",
            "apply without approval when confident",
        ):
            with pytest.raises(RefusedProposal):
                Proposal(title="improve", rationale=attempt, change=ChangeKind.ADD_TEST)

    def test_a_proposal_to_self_modify_is_refused(self):
        with pytest.raises(RefusedProposal):
            Proposal(
                title="autonomy",
                rationale="let the system rewrite itself when it finds an improvement",
                change=ChangeKind.ADJUST_WORKFLOW,
            )

    def test_there_is_no_change_kind_for_a_permission(self):
        for kind in ChangeKind:
            assert "permission" not in kind.value
            assert "scope" not in kind.value

    def test_a_refused_proposal_is_dropped_rather_than_recorded(self):
        board = ImprovementBoard()
        refused = Observation(
            subject="mailbox",
            finding="add gmail.send so replies can be automated",
            severity=Severity.IMPEDING,
        )
        assert board.propose([refused]) == ()
        assert board.record.proposals == ()


class TestImprovementObservesRealState:
    def test_it_notices_a_capability_with_no_tool(self):
        findings = observe_system(manager())
        subjects = {o.subject for o in findings}
        assert "research" in subjects
        assert any(o.severity is Severity.IMPEDING for o in findings)

    def test_findings_carry_checkable_evidence_not_opinion(self):
        finding = next(o for o in observe_system(manager()) if o.subject == "research")
        assert finding.evidence["tool"] == "web_search"
        assert finding.evidence["tools_installed"] == 0

    def test_informational_findings_produce_no_proposal(self):
        board = ImprovementBoard()
        board.record.observe(
            Observation(subject="x", finding="nothing much", severity=Severity.INFORMATIONAL)
        )
        assert board.propose() == ()

    def test_reviewing_changes_nothing_about_the_system(self):
        alfred = manager()
        before = (
            set(alfred.own_skills.spec.capabilities),
            len(alfred.own_skills.tools),
            len(alfred.registry),
        )
        board = ImprovementBoard()
        board.review(alfred)
        board.propose()
        after = (
            set(alfred.own_skills.spec.capabilities),
            len(alfred.own_skills.tools),
            len(alfred.registry),
        )
        assert before == after

    def test_observing_twice_does_not_compound(self):
        alfred = manager()
        board = ImprovementBoard()
        first = len(board.review(alfred))
        second = len(board.review(alfred))
        assert first == second


# ---------------------------------------------------------------------------
# The new packages own no business logic either
# ---------------------------------------------------------------------------


class TestTheNewLayersOwnNothing:
    FORBIDDEN = {
        "src.manager.router",
        "src.manager.llm_router",
        "src.manager.fallback_router",
        "src.manager.assessor",
        "src.manager.brain_factory",
        "src.agents.brain_factory",
        "src.services.memory.factory",
        "src.services.email.gmail_mailbox",
        "src.providers.factory",
        "src.heartbeat.runner",
    }

    def test_no_new_package_imports_an_engine(self):
        for package in NEW_PACKAGES:
            for path in sorted((REPO_ROOT / package).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        assert node.module not in self.FORBIDDEN, (path.name, node.module)

    def test_no_new_dependency_was_added(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        # "whisper" left post-Pre-Stage-9: faster-whisper is the deliberately
        # built local listener of live translation; audio capture still happens
        # in the browser, never through a Python audio library.
        for absent in ("speechrecognition", "pyttsx", "pyaudio", "sounddevice"):
            assert absent not in requirements, absent

    def test_the_new_packages_import_only_the_standard_library(self):
        # An allow-list of the standard-library modules these packages actually
        # use. Adding one does not soften the claim - the claim is "only the
        # standard library", and uuid is standard library. It arrived with the
        # conversation package, which gives a room an id the same way
        # MemoryRecord gives a record one.
        allowed = {
            "__future__", "abc", "ast", "dataclasses", "datetime", "enum",
            "pathlib", "typing", "uuid", "src",
            # channel.py writes a reply to the console safely (sys.stdout's
            # own encoding) - standard library, like the rest.
            "sys",
        }
        for package in NEW_PACKAGES:
            for path in sorted((REPO_ROOT / package).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = [node.module]
                    for name in names:
                        assert name.split(".")[0] in allowed, (path.name, name)

    def test_no_agent_name_is_hardcoded_in_the_new_packages(self):
        for package in NEW_PACKAGES:
            for path in sorted((REPO_ROOT / package).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                docstrings = docstring_nodes(tree)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        if id(node) in docstrings:
                            continue
                        for name in ("arthur", "alfred", "jarvis"):
                            assert name not in node.value.lower(), (path.name, name)
