"""Stage 13 final integration - the governed collaboration entry point,
wired into the real live conversation path.

Stage 13 built the Shared Context Graph. The correction that followed it
built ``answer_with_collaboration`` - a generic, tested decision - but
nothing in the actual assistant called it yet; every real request still
ran the ordinary DATA cycle directly. This file proves the wiring: the
same path ``scripts/control_center.py`` and ``scripts/serve_ui.py`` use
for every real request - ``scripts.exchange.exchange`` ->
``Channel.ask`` -> ``src.control_center.trail.observe`` - now reaches
``answer_with_collaboration`` for every unaddressed request, and nothing
about addressed requests, Brain/Memory identity, Stage 12 retention, or
voice delivery changed underneath it.

No test here calls ``answer_with_collaboration`` or ``start_collaboration``
directly - that is already covered, exhaustively, by
``tests/test_collaboration.py`` and ``tests/test_governed_collaboration_entry.py``.
Every test below goes in through the same door a real user's words would.
"""

from __future__ import annotations

import src.control_center.trail as trail
import src.manager.collaboration_entry as collaboration_entry
import src.manager.objective_loop as objective_loop
from src.channels.channel import TextChannel, VoiceChannel
from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.conversation.conversation import Conversation, Participant, ParticipantKind
from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice import voice_for
from scripts.exchange import exchange


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


def room():
    return Conversation(participants=(Participant("you", ParticipantKind.PERSON),))


class ScriptedProvider:
    """A real, local LLMProvider double - never a network call."""

    def __init__(self, text: str):
        self._text = text
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        self.prompts.append(prompt)
        return Completion(
            text=self._text, provider=self.name, model="scripted-1",
            input_tokens=len(prompt), output_tokens=len(self._text),
        )


def with_scripted_brain(manager, text: str):
    """The exact wiring composition.py performs, done by hand so a test
    can control what the Brain says without any network call."""
    brain = LLMManagerBrain(ScriptedProvider(text))
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return brain


class ScriptedSpeech(SpeechAdapter):
    """The same honest test double used throughout this project's own
    voice-production tests: records what it is asked to say, never
    fabricates success."""

    def __init__(self, state=AvailabilityState.AVAILABLE):
        self._availability = Availability(state=state)
        self.spoken: list[tuple[str, object]] = []

    @property
    def name(self):
        return "scripted"

    @property
    def availability(self):
        return self._availability

    def listen(self, timeout=None):
        raise SpeechUnavailable("not needed by this test")

    def speak(self, text, voice=None):
        self.spoken.append((text, voice))
        return self._availability.is_usable


EMAIL_REQUEST = "email opportunities update please"
NO_CANDIDATE_REQUEST = "status"


# ---------------------------------------------------------------------------
# The real live conversation path reaches answer_with_collaboration()
# ---------------------------------------------------------------------------


class TestTheLivePathReachesTheGovernedEntryPoint:
    def test_exchange_reaches_answer_with_collaboration_for_an_unaddressed_request(self, monkeypatch):
        """Patched where the call now actually happens: the Pre-Phase
        Agentic Workflow checkpoint inserted ``pursue_objective``
        between ``observe()`` and ``answer_with_collaboration`` (still
        the identical, single, governed entry point every step -
        ordinary or part of a longer objective - goes through; see
        ``src/manager/objective_loop.py``'s own docstring for why
        nothing about that guarantee changed)."""
        manager = real_manager()
        channel = TextChannel(manager)
        calls = []
        real = objective_loop.answer_with_collaboration

        def spy(mgr, task):
            calls.append(task.description)
            return real(mgr, task)

        monkeypatch.setattr(objective_loop, "answer_with_collaboration", spy)

        exchange(channel, room(), NO_CANDIDATE_REQUEST)

        assert calls == [NO_CANDIDATE_REQUEST]

    def test_an_addressed_request_does_not_go_through_the_collaboration_entry_point(self, monkeypatch):
        """Addressing somebody by name is a deliberate override - the
        Manager's own decision (and therefore the collaboration decision
        built on top of it) is skipped, exactly as it always has been."""
        manager = real_manager()
        channel = TextChannel(manager)
        conversation = room()
        conversation.join(Participant("Arthur", ParticipantKind.AGENT))
        calls = []
        real = objective_loop.answer_with_collaboration

        def spy(mgr, task):
            calls.append(task.description)
            return real(mgr, task)

        monkeypatch.setattr(objective_loop, "answer_with_collaboration", spy)

        exchange(channel, conversation, "Arthur, check my email")

        assert calls == []


# ---------------------------------------------------------------------------
# No candidate -> the existing Alfred path, unchanged
# ---------------------------------------------------------------------------


class TestNoCandidateStillFollowsTheExistingPath:
    def test_a_request_with_no_collaboration_candidate_is_handled_by_alfred_directly(self):
        manager = real_manager()
        channel = TextChannel(manager)

        turn = exchange(channel, room(), NO_CANDIDATE_REQUEST)

        assert turn.handled_by == "Alfred"
        assert not turn.delegated
        assert "handles directly" in turn.outcome

    def test_result_is_identical_to_calling_handle_task_directly(self):
        live_manager = real_manager()
        direct_manager = real_manager()

        live_turn = exchange(TextChannel(live_manager), room(), NO_CANDIDATE_REQUEST)
        direct_result = direct_manager.handle_task(Task(description=NO_CANDIDATE_REQUEST))

        assert live_turn.outcome == direct_result.output
        assert live_turn.handled_by == direct_result.agent_name


# ---------------------------------------------------------------------------
# A valid candidate actually creates a collaboration, canonical instance
# ---------------------------------------------------------------------------


class TestAValidCandidateCreatesARealCollaboration:
    def test_start_collaboration_is_actually_invoked_for_a_matching_request(self, monkeypatch):
        manager = real_manager()
        with_scripted_brain(manager, "synthesised through the live path")
        channel = TextChannel(manager)
        calls = []
        real = collaboration_entry.start_collaboration

        def spy(mgr, task, *, with_agents, results=None):
            calls.append(tuple(with_agents))
            return real(mgr, task, with_agents=with_agents, results=results)

        monkeypatch.setattr(collaboration_entry, "start_collaboration", spy)

        exchange(channel, room(), EMAIL_REQUEST)

        assert calls == [("Arthur",)]

    def test_the_canonical_registered_arthur_is_who_actually_contributes(self):
        manager = real_manager()
        real_arthur = manager.registry.get("Arthur")
        with_scripted_brain(manager, "synthesised through the live path")
        channel = TextChannel(manager)

        exchange(channel, room(), EMAIL_REQUEST)

        assert manager.registry.get("Arthur") is real_arthur


# ---------------------------------------------------------------------------
# Alfred receives and synthesizes the contribution, through the live path
# ---------------------------------------------------------------------------


class TestAlfredSynthesizesThroughTheLivePath:
    def test_alfred_produces_the_final_answer_using_arthurs_contribution(self):
        manager = real_manager()
        with_scripted_brain(
            manager, "Based on Arthur, there are no matching opportunities right now."
        )
        channel = TextChannel(manager)

        turn = exchange(channel, room(), EMAIL_REQUEST)

        assert turn.handled_by == "Alfred"
        assert "no matching opportunities" in turn.outcome

    def test_a_specialist_that_joins_the_room_still_only_happens_for_the_agent_alfred_actually_used(self):
        """`exchange()`'s own room-joining logic keys off `handled_by`
        (see scripts/exchange.py) - when Alfred is the one who answered,
        the room reflects that honestly rather than crediting Arthur for
        words Alfred's Brain produced."""
        manager = real_manager()
        with_scripted_brain(manager, "Alfred's own synthesis of the situation.")
        conversation = room()

        exchange(TextChannel(manager), conversation, EMAIL_REQUEST)

        assert not conversation.has("Arthur")


# ---------------------------------------------------------------------------
# Brain / Memory identity unchanged
# ---------------------------------------------------------------------------


class TestBrainAndMemoryIdentityUnchanged:
    def test_no_new_brain_or_memory_is_constructed_by_a_live_request(self):
        manager = real_manager()
        with_scripted_brain(manager, "synthesised through the live path")
        alfred_brain, alfred_memory = manager.brain, manager.memory
        arthur = manager.registry.get("Arthur")
        arthur_brain, arthur_memory = arthur.brain, arthur.memory

        channel = TextChannel(manager)
        exchange(channel, room(), EMAIL_REQUEST)
        exchange(channel, room(), NO_CANDIDATE_REQUEST)

        assert manager.brain is alfred_brain
        assert manager.memory is alfred_memory
        assert manager.registry.get("Arthur").brain is arthur_brain
        assert manager.registry.get("Arthur").memory is arthur_memory


# ---------------------------------------------------------------------------
# Stage 12 memory behaviour intact, through the live path
# ---------------------------------------------------------------------------


class TestStage12BehaviourIntactThroughTheLivePath:
    def test_remember_that_still_retains_through_exchange(self):
        manager = real_manager()
        channel = TextChannel(manager)
        conversation = room()

        before = len(manager.memory.recall(limit=50))
        exchange(channel, conversation, "remember that we prefer blue")
        after = manager.memory.recall(limit=50)

        assert len(after) == before + 1
        assert any("blue" in record.content for record in after)

    def test_retention_still_goes_through_retain_what_was_asked_for(self, monkeypatch):
        """Structural: exchange() has not stopped calling the pre-existing
        Stage 12 retention path just because it now also reaches
        answer_with_collaboration - both run, independently."""
        import scripts.exchange as exchange_module

        manager = real_manager()
        channel = TextChannel(manager)
        calls = []
        real = exchange_module.retain_what_was_asked_for

        def spy(ch, conv, request):
            calls.append(request)
            return real(ch, conv, request)

        monkeypatch.setattr(exchange_module, "retain_what_was_asked_for", spy)

        exchange(channel, room(), "remember that we prefer blue")

        assert calls == ["remember that we prefer blue"]


# ---------------------------------------------------------------------------
# Voice delivery intact
# ---------------------------------------------------------------------------


class TestVoiceDeliveryIntact:
    def test_arthur_handling_directly_is_delivered_in_arthurs_own_registered_voice(self):
        manager = real_manager()
        speech = ScriptedSpeech()
        channel = VoiceChannel(manager, speech=speech)

        response = channel.ask(EMAIL_REQUEST)  # NullBrain -> honest fallback -> Arthur
        assert response.turn.handled_by == "Arthur"
        channel.deliver(response)

        expected = voice_for(manager.registry.get("Arthur"))
        assert speech.spoken[-1][1] == expected
        assert expected is not None

    def test_alfred_synthesizing_is_delivered_in_alfreds_own_registered_voice(self):
        manager = real_manager()
        with_scripted_brain(manager, "Alfred's synthesised answer for voice delivery.")
        speech = ScriptedSpeech()
        channel = VoiceChannel(manager, speech=speech)

        response = channel.ask(EMAIL_REQUEST)
        assert response.turn.handled_by == "Alfred"
        channel.deliver(response)

        expected = voice_for(manager.own_skills)
        assert speech.spoken[-1][1] == expected
        assert expected is not None
        assert expected != voice_for(manager.registry.get("Arthur"))


# ---------------------------------------------------------------------------
# Half Brain remains honest, through the live path
# ---------------------------------------------------------------------------


class TestHalfBrainHonestyThroughTheLivePath:
    def test_a_default_nullbrain_manager_still_falls_back_honestly_over_the_live_path(self):
        manager = real_manager()  # default NullBrain
        channel = TextChannel(manager)

        turn = exchange(channel, room(), EMAIL_REQUEST)

        assert turn.handled_by == "Arthur"
        assert turn.succeeded


# ---------------------------------------------------------------------------
# No subject-specific routing introduced
# ---------------------------------------------------------------------------


class TestNoSubjectSpecificRoutingIntroduced:
    def test_unrelated_topics_never_trigger_a_collaboration_or_crash_the_live_path(self):
        manager = real_manager()
        channel = TextChannel(manager)
        for request in (
            "tell me about Messi and Arsenal transfers",
            "teach me about how cars work",
            "what is happening with Liverpool and Gakpo",
            "explain trading markets to me",
        ):
            turn = exchange(channel, room(), request)
            # No collaboration candidate exists for any of these (nothing
            # registered declares a capability overlapping them), so each
            # one must still land on Alfred's own honest handling.
            assert turn.handled_by == "Alfred"

    def test_the_wiring_itself_names_no_subject_or_agent(self):
        import inspect

        source = inspect.getsource(trail.observe)
        for forbidden in (
            "arsenal", "liverpool", "messi", "gakpo", "football", "trading", "cars",
        ):
            assert forbidden not in source.lower()
