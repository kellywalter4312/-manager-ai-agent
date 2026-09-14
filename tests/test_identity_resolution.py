"""Telling a name apart from a kind of work.

The collision this fixes: agent *names* and capability *words* shared one flat
token space, so a specialist called Research, Coding or Security could not be
invited - "invite Research" scored for the research capability and lost the
tie alphabetically. That is the gating issue for adding any future specialist,
because those are exactly the names a person would choose.

The rule now:

    an invitation verb   invite / bring / join
    AND a registered name a name currently in the roster
    -> an invitation

Neither alone. "research this topic" names work and no agent. "write a
function to join two lists" uses a verb and names nobody. Only both together
mean somebody should join.

**Every specialist in this file is invented by the test.** Nothing in the
invitation code knows Research, Coding or Security exist - they are registered
here and discovered through the roster, which is the whole claim: a future
specialist inherits this by registering.
"""

import ast
from pathlib import Path

import pytest

from src.agents.echo_agent import EchoAgent
from src.composition import build_manager, build_runtime
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import (
    INVITATION_VERBS,
    INVITED_KEY,
    PARTICIPANT_INVITATION,
    invitation_intent,
    select_capability,
)
from src.services.email.in_memory_mailbox import InMemoryMailbox

REPO_ROOT = Path(__file__).resolve().parent.parent

# Names chosen precisely because each is also a capability word or an ordinary
# verb's object. If the fix were a list of names, this file would be where it
# showed.
COLLIDING = {
    "Research": {"sources", "evidence"},
    "Coding": {"refactor", "compile"},
    "Security": {"audit", "vulnerability"},
}


def manager_with_specialists():
    runtime = build_runtime(
        Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
    )
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    for name, capabilities in COLLIDING.items():
        manager.register_agent(EchoAgent(name=name, capabilities=capabilities))
    return manager


def invited_by(manager, words):
    """Who the Manager resolved an invitation to, if anyone."""
    result = manager.handle_task(Task(description=words))
    return (result.payload or {}).get(INVITED_KEY, "<not an invitation>")


# ---------------------------------------------------------------------------
# An invitation naming a registered specialist resolves to that specialist
# ---------------------------------------------------------------------------


class TestAnInvitationResolvesTheIdentity:
    @pytest.mark.parametrize(
        "words, expected",
        [
            ("invite Arthur", "Arthur"),
            ("bring Arthur into this conversation", "Arthur"),
            ("invite Research", "Research"),
            ("bring Research into this conversation", "Research"),
            ("invite Coding", "Coding"),
            ("bring the Coding specialist into this conversation", "Coding"),
            ("invite Security", "Security"),
            ("join Research to this room", "Research"),
        ],
    )
    def test_the_named_specialist_is_resolved(self, words, expected):
        assert invited_by(manager_with_specialists(), words) == expected

    def test_the_invitation_handler_is_the_one_selected(self):
        manager = manager_with_specialists()
        names = [a.name for a in manager.registry.all_agents()]

        chosen = select_capability(
            Task(description="invite Research"),
            manager.own_skills.capabilities,
            participants=names,
        )

        assert chosen is PARTICIPANT_INVITATION


# ---------------------------------------------------------------------------
# Ordinary work is still ordinary work
# ---------------------------------------------------------------------------


class TestNormalTasksAreNotInvitations:
    @pytest.mark.parametrize(
        "words",
        [
            "research this topic for me",
            "help me research this",
            "code this function",
            "debug this script",
            "look up information about a company",
            "write a function to join two lists",
            "list your capabilities",
        ],
    )
    def test_a_task_is_not_an_invitation(self, words):
        assert invited_by(manager_with_specialists(), words) == "<not an invitation>"

    def test_a_verb_alone_is_not_an_invitation(self):
        """"join" is an ordinary coding word. Without a registered name it
        means what it usually means."""
        manager = manager_with_specialists()
        names = [a.name for a in manager.registry.all_agents()]

        assert invitation_intent(
            Task(description="write a function to join two lists"), names
        ) is False

    def test_a_name_alone_is_not_an_invitation(self):
        """Naming a specialist while describing work is not asking for them to
        join - and this is what stops every mention becoming an invitation."""
        manager = manager_with_specialists()
        names = [a.name for a in manager.registry.all_agents()]

        assert invitation_intent(Task(description="research this topic"), names) is False

    def test_both_together_are(self):
        manager = manager_with_specialists()
        names = [a.name for a in manager.registry.all_agents()]

        assert invitation_intent(Task(description="invite Research"), names) is True


# ---------------------------------------------------------------------------
# Discovered, never hardcoded
# ---------------------------------------------------------------------------


class TestSpecialistsAreDiscoveredNotHardcoded:
    def test_a_specialist_nobody_anticipated_is_invitable(self):
        """The scaling claim. This name appears nowhere in src/."""
        runtime = build_runtime(
            Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        )
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        manager.register_agent(
            EchoAgent(name="Quartermaster", capabilities={"inventory"})
        )

        assert invited_by(manager, "invite Quartermaster") == "Quartermaster"

    def test_an_unregistered_name_resolves_to_nothing(self):
        assert invited_by(manager_with_specialists(), "invite Cartographer") is None

    def test_no_agent_name_appears_in_the_capability_module(self):
        """If the fix were a list of names, it would live here.

        Checked against agent names only. "coding", "research" and "security"
        are deliberately *not* in this list: they are legitimate capability
        *words* and appear in handler descriptions - which is the very
        ambiguity this phase exists to resolve, and a string search cannot tell
        the two uses apart. The general claim is proved by
        test_a_specialist_nobody_anticipated_is_invitable instead, which works
        with a name nothing in src/ has ever seen.
        """
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
                for name in ("arthur", "alfred", "quartermaster"):
                    assert name not in node.value.lower(), node.value[:50]

    def test_the_verbs_are_a_small_closed_set_not_a_name_list(self):
        """Verbs describe the request. Names come from the roster, never from
        here - which is what makes this generalise."""
        assert INVITATION_VERBS == frozenset({"invite", "bring", "join"})


# ---------------------------------------------------------------------------
# Recognising a request is not routing
# ---------------------------------------------------------------------------


class TestThisIsNotASecondRouter:
    def test_recognition_chooses_a_handler_never_an_agent(self):
        """A claim selects one of Alfred's own handlers. It cannot name an
        agent, because it returns a capability."""
        manager = manager_with_specialists()
        chosen = select_capability(
            Task(description="invite Research"),
            manager.own_skills.capabilities,
            participants=[a.name for a in manager.registry.all_agents()],
        )
        assert not hasattr(chosen, "agent_name")

    def test_the_manager_still_decides_who_handles_the_request(self):
        manager = manager_with_specialists()
        decision = manager.decide(Task(description="invite Research"))
        assert decision.agent_name == "Alfred"

    def test_intent_recognition_reads_the_roster_and_nothing_else(self):
        """It is given names, not agents - so it cannot reach a brain, a memory
        or a tool even if a later edit wanted to."""
        assert invitation_intent(Task(description="invite Research"), ["Research"])
        assert not invitation_intent(Task(description="invite Research"), [])

    def test_scoring_still_decides_everything_else(self):
        """The exception a handler must earn. Twenty capabilities claim -
        each for the same reason word overlap is the wrong instrument for
        its own job (naming who to invite; telling a short conversational
        turn from a request that merely contains one of its words;
        recognising a memory command by its marker phrase rather than its
        ordinary words like "forget" or "about"; recognising "what
        happened with/to X" as wanting an update on X rather than an
        explanation, Stage 14; recognising an explicit "record this [for
        X]" capture as distinct from "remember that X", a date-derived
        question as distinct from the date itself, and a recognisable
        achievement statement as distinct from ordinary conversation -
        all three additional Stage 12 items; recognising a
        call/text/read/reply command by its imperative shape rather than
        its ordinary words like "call" or "message", Stage 15;
        recognising "my X's birthday is Y" by its relational-possessive
        shape rather than the ordinary word "birthday", Stage 12
        closure; and recognising "check my connected account" by its
        own marker phrase rather than the ordinary word "connection",
        Stage 16; and recognising "who are you" as a question about an
        identity rather than about a kind of work, Stage 17.22 - which is
        the case this whole mechanism was written for; and recognising a
        settled question about any subject at all by its interrogative
        shape rather than by a list of subjects that could never exist,
        plus a first-person statement of study by its own shape rather
        than by the ordinary words "study" or "learning", Stage 18; and
        recognising "what day/date/month/year is it" by that same
        interrogative shape rather than the ordinary words "day", "date",
        "month" or "year" - each far too common to score safely, and "it"
        already scored small_talk before this existed - Foundation Repair
        #1); and recognising a genuine imperative ask for tool-gated
        coding or research work ("write a function", "research X") by an
        anchored-verb shape rather than the ordinary words "function" or
        "code", so that a description of a problem asking to be reasoned
        about - which shares those same words - is never swept into that
        capability's tool-refusal and instead reaches the Brain, Phase
        1.3; and recognising an explicit correction ("that's wrong, ...",
        "correction: ...", "update what you remembered about X to Y") by
        its own marker shape rather than the ordinary word "correct"
        ("that's the correct file"), Phase 2; and recognising an
        explicit ask to save real, composed findings as a real file
        ("create a report from these findings") by its own marker shape
        rather than the ordinary word "report" ("she reported the
        issue"), and a genuine ask to look at a real image by its own
        marker shape, Phase 3; and recognising an explicit task
        create/list/complete/reopen/update request ("remind me to X",
        "what are my tasks", "mark the task X as done", "make the task X
        incomplete", "change the task X to Y") by its own marker shape
        rather than the ordinary word "task" ("that was quite a task"),
        Phase 5.1 (reopen added by Alfred's Real 3D Workspace, so a
        completed task is never a dead end); and recognising an explicit
        ask to cut real clips from an existing video ("clip this video
        X.mp4 from 1:20-1:45", "find the best moments and clip them") by
        its own marker-plus-evidence shape rather than the ordinary word
        "clip" ("that was quite a clip show"), the video-clipping
        checkpoint; and recognising an explicit ask to create or revise a
        real, persistent handout ("make me a handout", "revise the
        handout") by its own marker shape, the multimodal
        workspace/artifact checkpoint; and recognising an explicit
        start/stop/cancel-a-real-microphone-recording request ("start
        recording for research", "stop recording", "cancel the
        recording") by its own marker shape rather than the ordinary
        word "record" ("that's a new personal record"), the Real Audio
        Recording/Capture checkpoint - and every other request is still
        settled by word overlap."""
        from src.manager.capabilities import MANAGER_CAPABILITIES

        claiming = sorted(c.name for c in MANAGER_CAPABILITIES.values() if c.claims)
        assert claiming == [
            "about_self",
            "calendar",
            "cancel_recording",
            "clip_video",
            "communication", "complete_task", "connected_action", "correct_memory",
            "create_artifact",
            "create_diagram",
            "create_task", "current_datetime", "forget",
            "general_assistance", "generate_document", "image_analysis",
            "knowledge", "list_recordings", "list_tasks", "live_information",
            "media_production",
            "milestone_recognition", "participant_invitation", "personal_context",
            "personal_recurring_events",
            # Pre-Stage-9 Campaign 7: read-back is recognised by its reading
            # shape plus a kept collection, never by the word "read" alone.
            "read_back",
            "read_content",
            "recall_memory", "record_this", "reopen_task", "revise_artifact", "revise_diagram",
            "select_voice",
            "small_talk", "start_recording", "stop_recording", "temporal_derivation", "transcribe_media", "update_task",
        ]


# ---------------------------------------------------------------------------
# Nothing else moved
# ---------------------------------------------------------------------------


class TestSurroundingArchitectureIsUntouched:
    def test_the_manager_still_handles_small_work_himself(self):
        manager = manager_with_specialists()
        result = manager.handle_task(Task(description="list your capabilities"))
        assert result.succeeded
        assert result.agent_name == "Alfred"

    def test_specialist_work_still_reaches_the_specialist(self):
        manager = manager_with_specialists()
        result = manager.handle_task(Task(description="scan my inbox for opportunities"))
        assert result.agent_name == "Arthur"

    def test_brains_and_memories_remain_separate(self):
        manager = manager_with_specialists()
        arthur = manager.registry.get("Arthur")
        assert manager.brain is not arthur.brain
        assert manager.memory is not arthur.memory

    def test_the_conversation_still_reaches_no_registry(self):
        source = (REPO_ROOT / "src" / "conversation" / "conversation.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src"), node.module
