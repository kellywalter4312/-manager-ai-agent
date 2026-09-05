"""The room agents share without sharing minds.

The claim this file exists to protect, stated once:

    shared context != shared intelligence

Collaboration between agents is easy to get wrong in one line. Hand a
specialist the Manager, the registry, or its peers and they can work together -
and every isolation guarantee in the architecture is gone, because an agent
holding the registry can reach any other agent's brain, memory and tools.

So the tests below fall into two groups. One proves a Conversation is *useful*:
participants join, utterances are recorded, addressing is resolved. The other
proves it is *inert*: there is no brain in it, no memory, no registry, no
agent, and no way to reach one through it.

The second group is the one that matters.
"""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.conversation.conversation import (
    Conversation,
    ConversationError,
    Participant,
    ParticipantKind,
    Utterance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONVERSATION = REPO_ROOT / "src" / "conversation"


def person(name="you"):
    return Participant(name, ParticipantKind.PERSON)


def agent(name):
    return Participant(name, ParticipantKind.AGENT)


def room(*names):
    """A conversation with a person and the named agents in it."""
    return Conversation(participants=(person(), *(agent(n) for n in names)))


# ---------------------------------------------------------------------------
# Shared context is not shared intelligence
# ---------------------------------------------------------------------------


class TestTheRoomHoldsIdentitiesNotAgents:
    def test_a_participant_has_no_brain_memory_or_tools(self):
        """Absent, not ignored. There is no field for them to live in."""
        taking_part = agent("Alfred")
        for forbidden in ("brain", "memory", "tools", "registry", "manager", "agent"):
            assert not hasattr(taking_part, forbidden), forbidden

    def test_a_conversation_holds_no_brain_memory_or_registry(self):
        conversation = room("Alfred", "Arthur")
        for forbidden in ("brain", "memory", "tools", "registry", "manager", "router"):
            assert not hasattr(conversation, forbidden), forbidden

    def test_nothing_can_reach_an_agent_through_the_room(self):
        """The decisive one.

        Two agents are in the same conversation. Neither can obtain the other -
        the room yields a name and a kind, and there is nothing else in it to
        reach through.
        """
        conversation = room("Alfred", "Arthur")

        found = conversation.participant("Arthur")
        assert found is not None
        assert found.name == "Arthur"
        assert set(vars(found)) == {"name", "kind"}

    def test_a_conversation_refuses_anything_that_is_not_a_participant(self):
        """The seam where an agent object would sneak in.

        The double is shaped like a *real* agent - name, spec, brain, memory -
        because a mutation proved the weaker version was not enough. A `join`
        that helpfully converted anything agent-shaped into a Participant
        passed the whole suite: the earlier double had no `spec`, so the
        conversion path never fired on it. The refusal must hold against the
        thing that would actually be passed by mistake.
        """

        class PretendAgent:
            name = "Impostor"
            spec = object()
            brain = object()
            memory = object()
            tools = object()

            def execute(self, task):  # what a real agent offers
                raise AssertionError("the room must never be able to call this")

        with pytest.raises(ConversationError, match="identities"):
            room().join(PretendAgent())

    def test_the_room_will_not_convert_an_agent_into_a_participant(self):
        """Refusing is not the same as quietly extracting a name.

        A `join` that read `.name` off an agent and built a Participant from it
        would store identical state, so no assertion about the *stored* room
        could tell the difference. The boundary being protected is that an
        agent object must not be accepted at all - callers resolve an identity
        first, and the Manager is what resolves it.
        """

        class RealShapedAgent:
            name = "Arthur"
            spec = object()

        conversation = room()
        with pytest.raises(ConversationError):
            conversation.join(RealShapedAgent())
        assert not conversation.has("Arthur")

    def test_the_package_imports_no_agent_brain_memory_or_registry(self):
        """Structural. A future edit that reached for one fails here first."""
        forbidden = (
            "src.manager", "src.agents", "src.registry", "src.services.memory",
            "src.composition", "src.providers",
        )
        for path in sorted(CONVERSATION.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for banned in forbidden:
                        assert not node.module.startswith(banned), (path.name, node.module)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for banned in forbidden:
                            assert not alias.name.startswith(banned), (path.name, alias.name)


# ---------------------------------------------------------------------------
# Participants are dynamic, and there is no ceiling
# ---------------------------------------------------------------------------


class TestParticipantsAreDynamic:
    def test_an_agent_can_join_an_existing_conversation(self):
        conversation = room("Alfred")
        assert not conversation.has("Research")

        conversation.join(agent("Research"))

        assert conversation.has("Research")

    def test_joining_twice_does_not_duplicate(self):
        """Somebody repeating themselves is not a request for two Researches."""
        conversation = room("Alfred")
        conversation.join(agent("Research"))
        conversation.join(agent("Research"))
        assert len(conversation.participants) == 3

    def test_leaving_keeps_what_was_said(self):
        """A transcript with a speaker deleted from it would be a record of a
        conversation that never happened."""
        conversation = room("Alfred")
        conversation.say(Utterance(speaker="Alfred", text="here"))

        assert conversation.leave("Alfred") is True

        assert conversation.has("Alfred") is False
        assert conversation.transcript() == ("Alfred: here",)

    def test_leaving_somebody_who_was_never_here_is_not_an_error(self):
        assert room("Alfred").leave("Nobody") is False

    def test_there_is_no_participant_ceiling(self):
        """No maximum. Not three, not five, not ten.

        A limit here would be an architectural claim about how many agents may
        think about a problem together, and there is no honest number to write.
        """
        conversation = room()
        for index in range(300):
            conversation.join(agent(f"specialist-{index}"))

        assert len(conversation.participants) == 301
        assert len(conversation.agents) == 300

    def test_no_agent_name_is_written_into_the_package(self):
        """A future specialist joins by being named, not by being known here."""
        for path in sorted(CONVERSATION.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    body = getattr(node, "body", None)
                    if body and isinstance(body[0], ast.Expr):
                        if isinstance(body[0].value, ast.Constant):
                            docstrings.add(id(body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    for name in ("alfred", "arthur", "jarvis"):
                        assert name not in node.value.lower(), (path.name, node.value[:40])


# ---------------------------------------------------------------------------
# What was said
# ---------------------------------------------------------------------------


class TestTheTranscript:
    def test_an_utterance_records_who_said_what_and_when(self):
        conversation = room("Alfred")
        said = conversation.say(Utterance(speaker="you", text="hello"))
        assert said.speaker == "you"
        assert said.text == "hello"
        assert isinstance(said.when, datetime)
        assert said.when.tzinfo is not None

    def test_a_stranger_cannot_speak(self):
        """An utterance from somebody who never joined is either a bug or a
        participant nobody announced. Both are worth failing on."""
        with pytest.raises(ConversationError, match="not in this conversation"):
            room("Alfred").say(Utterance(speaker="Ghost", text="hello"))

    def test_an_utterance_must_say_something(self):
        with pytest.raises(ConversationError):
            Utterance(speaker="you", text="   ")

    def test_the_log_line_never_carries_what_was_said(self):
        """A conversation holds whatever a person chose to say, and this
        project keeps that out of log files."""
        said = Utterance(speaker="you", text="my bank password is hunter2")
        assert "hunter2" not in said.describe()
        assert "you" in said.describe()

    def test_utterances_are_frozen(self):
        said = Utterance(speaker="you", text="hello")
        with pytest.raises(Exception):
            said.text = "something else"

    def test_the_room_keeps_order(self):
        conversation = room("Alfred")
        conversation.say(Utterance(speaker="you", text="first"))
        conversation.say(Utterance(speaker="Alfred", text="second"))
        assert conversation.transcript() == ("you: first", "Alfred: second")

    def test_a_conversation_is_not_bounded(self):
        """Unlike EventLog. A room that silently discarded what somebody said
        would be lying about itself; compaction is a memory concern."""
        conversation = room("Alfred")
        for index in range(500):
            conversation.say(Utterance(speaker="you", text=f"line {index}"))
        assert len(conversation) == 500


# ---------------------------------------------------------------------------
# Addressing - the seam voice will route on
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_a_leading_name_is_an_address(self):
        conversation = room("Alfred", "Research")
        assert conversation.addressed_in("Alfred, bring Research into this") == "Alfred"

    def test_addressing_is_case_insensitive_but_returns_the_real_spelling(self):
        """So a later router can match on it exactly."""
        assert room("Alfred").addressed_in("alfred, status please") == "Alfred"

    def test_a_name_mentioned_mid_sentence_addresses_nobody(self):
        """Talking *about* somebody is not talking *to* them. Guessing the
        difference would make the room route on a hunch."""
        conversation = room("Alfred", "Research")
        assert conversation.addressed_in("tell Alfred about this") is None

    def test_a_name_inside_a_longer_word_is_not_an_address(self):
        assert room("Ali").addressed_in("Alison should look at this") is None

    def test_the_longest_matching_name_wins(self):
        conversation = room("Research", "Research Agent")
        assert conversation.addressed_in("Research Agent, look into this") == "Research Agent"

    def test_addressing_somebody_not_in_the_room_finds_nobody(self):
        """The room never invents a participant."""
        assert room("Alfred").addressed_in("Coding, write this") is None

    def test_nothing_addressed_is_the_common_and_honest_answer(self):
        assert room("Alfred").addressed_in("what is on my schedule") is None
        assert room("Alfred").addressed_in("") is None

    def test_addressing_needs_no_model(self):
        """Deterministic, so it behaves identically in Half Brain."""
        conversation = room("Alfred")
        first = conversation.addressed_in("Alfred, hello")
        second = conversation.addressed_in("Alfred, hello")
        assert first == second == "Alfred"


# ---------------------------------------------------------------------------
# The room decides nothing
# ---------------------------------------------------------------------------


class TestTheRoomOrchestratesNothing:
    def test_it_has_no_way_to_route_reason_or_remember(self):
        conversation = room("Alfred", "Arthur")
        for forbidden in (
            "route", "diagnose", "handle_task", "deliberate", "remember",
            "recall", "delegate", "assess",
        ):
            assert not hasattr(conversation, forbidden), forbidden

    def test_detail_is_carried_and_never_interpreted(self):
        """The same contract MemoryRecord.context has. It is how a handled turn
        rides along without this package learning what a turn is."""
        marker = object()
        conversation = room("Alfred")
        said = conversation.say(
            Utterance(speaker="Alfred", text="done", detail=marker)
        )
        assert said.detail is marker

    def test_addressing_records_rather_than_routes(self):
        """Resolving who was addressed must not select, invoke or notify
        anybody. It answers a question about the words."""
        conversation = room("Alfred", "Arthur")
        before = len(conversation)
        conversation.addressed_in("Arthur, what did you find")
        assert len(conversation) == before
