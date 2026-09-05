"""Bringing somebody into a conversation.

The path this file protects:

    person asks       "bring Arthur into this conversation"
    Manager decides   capability routing -> Alfred's own invitation handler
    handler resolves  a NAME, from the registry it already reads
    entry point opens the door with that name
    room gains        a Participant: a name and a kind, nothing else

**Only a string crosses the boundary.** The handler has no Conversation in
scope and the room has no registry, so neither can do the other's job even if
a later edit wanted it to.

The last class records a genuine limitation rather than hiding it: an agent
whose *name* is also one of Alfred's *capability words* is captured by that
capability instead of by the invitation. It is asserted here so the day it is
fixed, the test fails and somebody updates it deliberately.
"""

import ast
from pathlib import Path

import pytest

from src.composition import (
    build_channel,
    build_conversation,
    build_manager,
    build_runtime,
)
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.conversation.conversation import Participant, ParticipantKind
from src.core.contracts import Task
from src.manager.capabilities import (
    INVITED_KEY,
    PARTICIPANT_INVITATION,
    select_capability,
)
from src.services.email.in_memory_mailbox import InMemoryMailbox

REPO_ROOT = Path(__file__).resolve().parent.parent


def system():
    runtime = build_runtime(
        Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
    )
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    return (
        ControlCenter(runtime, manager),
        build_channel(runtime, manager),
        build_conversation(manager),
    )


def talk(centre, channel, room, words):
    import scripts.control_center as cc

    return cc.run_command(centre, f"talk {words}", 80, channel, room)


# ---------------------------------------------------------------------------
# The invitation path
# ---------------------------------------------------------------------------


class TestInvitingASpecialist:
    def test_an_invitation_reaches_the_managers_own_handler(self):
        """Deciding who takes part is orchestration, so Alfred handles it."""
        centre, _, _ = system()
        task = Task(description="bring Arthur into this conversation")

        assert centre.manager.decide(task).agent_name == "Alfred"
        chosen = select_capability(task, centre.manager.own_skills.capabilities)
        assert chosen is PARTICIPANT_INVITATION

    def test_the_specialist_joins_the_room(self):
        centre, channel, room = system()
        assert not room.has("Arthur")

        talk(centre, channel, room, "bring Arthur into this conversation")

        assert room.has("Arthur")

    def test_the_manager_answers_the_invitation_himself(self):
        """Alfred did the work, so Alfred says so - he is not relaying anybody."""
        centre, channel, room = system()

        talk(centre, channel, room, "invite Arthur")

        assert room.utterances[-1].speaker == "Alfred"

    def test_only_a_name_crosses_the_boundary(self):
        """The handler resolves an identity, never hands over an agent."""
        centre, _, _ = system()
        result = centre.manager.handle_task(Task(description="invite Arthur"))

        invited = result.payload[INVITED_KEY]
        assert isinstance(invited, str)
        assert invited == "Arthur"

    def test_the_invited_participant_is_identity_only(self):
        centre, channel, room = system()
        talk(centre, channel, room, "invite Arthur")

        joined = room.participant("Arthur")
        assert set(vars(joined)) == {"name", "kind"}
        for forbidden in ("brain", "memory", "tools", "registry", "agent"):
            assert not hasattr(joined, forbidden), forbidden

    def test_an_invited_specialist_can_then_be_addressed(self):
        """The point of inviting somebody: they become answerable."""
        centre, channel, room = system()
        assert room.addressed_in("Arthur, what did you find") is None

        talk(centre, channel, room, "invite Arthur")

        assert room.addressed_in("Arthur, what did you find") == "Arthur"

    def test_inviting_twice_does_not_duplicate(self):
        centre, channel, room = system()
        talk(centre, channel, room, "invite Arthur")
        before = len(room.participants)

        talk(centre, channel, room, "invite Arthur")

        assert len(room.participants) == before


# ---------------------------------------------------------------------------
# Nobody is invented
# ---------------------------------------------------------------------------


class TestNobodyIsInvented:
    def test_an_unregistered_name_resolves_to_nothing(self):
        centre, _, _ = system()
        result = centre.manager.handle_task(
            Task(description="invite a Ledger Specialist")
        )
        assert result.payload[INVITED_KEY] is None

    def test_the_answer_says_who_is_actually_registered(self):
        """More useful than silently agreeing somebody exists."""
        centre, _, _ = system()
        result = centre.manager.handle_task(
            Task(description="invite a Ledger Specialist")
        )
        assert "no specialist by that name" in result.output.lower()
        assert "Arthur" in result.output

    def test_the_room_is_unchanged(self):
        centre, channel, room = system()
        before = [p.name for p in room.participants]

        talk(centre, channel, room, "invite a Ledger Specialist")

        assert [p.name for p in room.participants] == before


# ---------------------------------------------------------------------------
# Boundaries the invitation must not cross
# ---------------------------------------------------------------------------


class TestTheInvitationCrossesNoBoundary:
    def test_the_handler_cannot_reach_a_conversation(self):
        """Structural. It resolves a name; joining is somebody else's job.

        The boundary is the ``src.conversation`` **package** - Conversation,
        Participant and the collaboration entry that joins them - matched by
        path segment rather than by the substring "conversation".

        The substring was the original spelling and it stopped meaning what
        it said. Stage 11.3 added ``src/services/conversation_floor.py``, a
        pure text module (word sets and one predicate; its own imports are
        ``typing`` and ``src.core.contracts``, and it exposes no
        Conversation, no participants and nothing joinable), and the
        assertion began failing on a *word* while the boundary it exists to
        guard was never crossed.

        This is stricter than the substring where it counts - it catches
        every module under the package, ``src.conversation.collaboration``
        included - and no longer catches modules that merely have the word
        in their name.
        """
        source = (REPO_ROOT / "src" / "manager" / "capabilities.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                segments = node.module.split(".")
                assert segments[:2] != ["src", "conversation"], node.module
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src.conversation"), alias.name

    def test_the_conversation_still_reaches_no_registry(self):
        source = (REPO_ROOT / "src" / "conversation" / "conversation.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src"), node.module

    def test_no_agent_is_named_in_the_handler(self):
        """A specialist added later is invitable the day it registers."""
        source = (REPO_ROOT / "src" / "manager" / "capabilities.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
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
                assert "arthur" not in node.value.lower(), node.value[:40]

    def test_there_is_still_no_participant_ceiling(self):
        """Invitation must not have introduced one."""
        _, _, room = system()
        for index in range(250):
            room.join(Participant(f"agent-{index}", ParticipantKind.AGENT))
        assert len(room.agents) == 251  # 250 plus the Manager


# ---------------------------------------------------------------------------
# A known limitation, recorded rather than hidden
# ---------------------------------------------------------------------------


class TestAnUnregisteredNameIsNotAnIdentity:
    """A word is only a name if somebody is registered under it.

    This class recorded a limitation in Phase 5: "invite Research" was captured
    by the research capability, because agent names and capability words shared
    one flat token space. Phase 5.1 fixed that - see
    tests/test_identity_resolution.py.

    Phase 1.3 changed what happens next. Word overlap used to be the only
    thing standing between "invite Research" and PARTICIPANT_INVITATION -
    general_assistance's own overlap on "research" happened to win the tie
    alphabetically, so this fell through to a research-shaped answer instead.
    general_assistance no longer scores on bare words at all (a reasoning
    question that merely mentions "research" or "function" must reach the
    Brain, not a tool-refusal - see tests/test_brain_architecture_phase_1_3.py),
    so nothing now competes with PARTICIPANT_INVITATION's own word overlap on
    "invite" - and that capability's ``handle`` was always built to give the
    honest "no specialist by that name" answer for exactly this shape (see
    TestNobodyIsInvented above, and the scoreable comment on
    PARTICIPANT_INVITATION itself). "Research" is not a name in *this*
    project, and nothing is fabricated either way - it is simply the same
    honest fallback "invite a Ledger Specialist" already gets, rather than a
    special case for names that also happen to be capability words.
    """

    def test_an_unregistered_word_is_honestly_refused_not_invented(self):
        centre, _, _ = system()
        task = Task(description="invite Research")
        names = [a.name for a in centre.manager.registry.all_agents()]
        assert "Research" not in names

        chosen = select_capability(
            task, centre.manager.own_skills.capabilities, participants=names
        )
        assert chosen is PARTICIPANT_INVITATION

        result = centre.manager.handle_task(task)
        assert result.payload[INVITED_KEY] is None
        assert "no specialist by that name" in result.output.lower()

    def test_a_registered_name_does(self):
        """The contrast. Same words, and the only difference is who exists."""
        centre, _, _ = system()

        chosen = select_capability(
            Task(description="invite Arthur"),
            centre.manager.own_skills.capabilities,
            participants=[a.name for a in centre.manager.registry.all_agents()],
        )

        assert chosen is PARTICIPANT_INVITATION

    def test_nobody_is_fabricated_either_way(self):
        centre, channel, room = system()
        before = [p.name for p in room.participants]

        talk(centre, channel, room, "bring Research into this conversation")

        assert [p.name for p in room.participants] == before
        assert not room.has("Research")
