"""Carrying something out of a session without carrying everything.

Two stores, two questions:

    session      what are we talking about right now?   -> Conversation
    memory       what should change a future decision?  -> Memory, under policy

Most of a conversation is only the first. A system that persisted every
utterance would build the history-instead-of-knowledge pile the memory policy
exists to prevent, at the fastest rate available. So the only thing that
crosses is what somebody **asked** to keep.

The tests below fall into three groups: that a request to remember survives a
new session; that nothing else does; and that crossing this seam gave nobody
access to anybody else's store.
"""

import ast
from pathlib import Path

import pytest

from src.agents.echo_agent import EchoAgent
from src.composition import build_manager, build_runtime
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.manager import ManagerAgent
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.memory.factory import build_memory
from src.services.memory.memory import MemoryRecord
from src.services.memory.session import (
    RETAINED_SOURCE,
    retention_requests,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def settings(tmp_path, **overrides):
    """Persistent, in a temporary directory - never the real data/ store."""
    return Settings.from_env(
        env={},
        log_level="CRITICAL",
        memory="persistent",
        memory_dir=str(tmp_path),
        **overrides,
    )


def a_session(tmp_path):
    """One process's worth of system, built the way production builds it."""
    runtime = build_runtime(settings(tmp_path))
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# What crosses
# ---------------------------------------------------------------------------


class TestAnExplicitRequestSurvivesTheSession:
    def test_a_request_to_remember_becomes_a_candidate(self):
        candidates = retention_requests(
            ["remember that we are building Project Beacon"]
        )
        assert len(candidates) == 1
        assert candidates[0].content == "we are building Project Beacon"

    def test_the_instruction_itself_is_not_stored(self):
        """Storing "remember that ..." alongside the fact would make every
        recalled note read like a transcript."""
        candidate = retention_requests(["remember that the deadline is Friday"])[0]
        assert "remember that" not in candidate.content.lower()

    def test_it_survives_into_a_later_session(self, tmp_path):
        """The success condition. Two separately built systems over the same
        store - which is what a new process is."""
        first = a_session(tmp_path)
        for candidate in retention_requests(
            ["remember that we are building Project Beacon"]
        ):
            assert first.retain(candidate).allowed

        second = a_session(tmp_path)

        recalled = second.memory.recall("Beacon", limit=5)
        assert recalled, "a later session should recall what was retained"
        assert "Beacon" in recalled[0].content

    def test_the_note_records_where_it_was_learned(self, tmp_path):
        """Provenance, in the opaque context field - the same rule an
        observation follows through ToolResult."""
        manager = a_session(tmp_path)
        candidate = retention_requests(
            ["remember that the deadline is Friday"], conversation_id="room-1"
        )[0]
        manager.retain(candidate)

        recalled = manager.memory.recall("deadline", limit=1)[0]
        assert recalled.context["source"] == RETAINED_SOURCE
        assert recalled.context["conversation"] == "room-1"

    def test_extraction_needs_no_model(self):
        """Deterministic, so it behaves identically in Half Brain."""
        once = retention_requests(["remember that x"])
        twice = retention_requests(["remember that x"])
        assert once[0].content == twice[0].content == "x"


# ---------------------------------------------------------------------------
# What does not cross
# ---------------------------------------------------------------------------


class TestOrdinaryConversationDoesNotBecomeMemory:
    @pytest.mark.parametrize(
        "said",
        [
            "what did you find?",
            "scan my inbox for opportunities",
            "thanks, that is useful",
            "try the other one",
            "list your capabilities",
        ],
    )
    def test_an_ordinary_utterance_is_not_a_candidate(self, said):
        assert retention_requests([said]) == ()

    def test_a_whole_conversation_yields_only_what_was_asked_for(self):
        conversation = [
            "scan my inbox for opportunities",
            "what did you find?",
            "remember that we are building Project Beacon",
            "thanks",
        ]
        candidates = retention_requests(conversation)
        assert len(candidates) == 1
        assert candidates[0].content == "we are building Project Beacon"

    def test_talking_does_not_fill_the_store(self, tmp_path):
        """Through the live path: an ordinary exchange writes nothing."""
        import scripts.control_center as cc
        from src.composition import build_channel, build_conversation
        from src.control_center.observer import ControlCenter

        runtime = build_runtime(settings(tmp_path))
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        centre = ControlCenter(runtime, manager)
        channel = build_channel(runtime, manager)
        room = build_conversation(manager)

        before = len(manager.memory.recall(limit=200))
        cc.run_command(centre, "talk list your capabilities", 80, channel, room)

        assert len(manager.memory.recall(limit=200)) == before

    def test_asking_to_remember_does_fill_it(self, tmp_path):
        import scripts.control_center as cc
        from src.composition import build_channel, build_conversation
        from src.control_center.observer import ControlCenter

        runtime = build_runtime(settings(tmp_path))
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        centre = ControlCenter(runtime, manager)
        channel = build_channel(runtime, manager)
        room = build_conversation(manager)

        cc.run_command(
            centre,
            "talk remember that we are building Project Beacon",
            80,
            channel,
            room,
        )

        assert manager.memory.recall("Beacon", limit=5)

    def test_the_room_still_holds_the_whole_session(self, tmp_path):
        """Session context is not thinned by what memory declined. The room
        keeps everything; memory keeps what was asked for."""
        import scripts.control_center as cc
        from src.composition import build_channel, build_conversation
        from src.control_center.observer import ControlCenter

        runtime = build_runtime(settings(tmp_path))
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        centre = ControlCenter(runtime, manager)
        channel = build_channel(runtime, manager)
        room = build_conversation(manager)

        cc.run_command(centre, "talk list your capabilities", 80, channel, room)
        cc.run_command(centre, "talk roster", 80, channel, room)

        assert len(room) == 4  # two questions, two answers
        assert len(manager.memory.recall(limit=200)) == 0


# ---------------------------------------------------------------------------
# The seam grants nobody anything
# ---------------------------------------------------------------------------


class TestContinuityGrantsNoAccess:
    def test_a_candidate_still_faces_the_policy(self, tmp_path):
        """Extraction proposes; the policy is the authority. A record it
        refuses is not stored, however it arrived."""
        manager = a_session(tmp_path)
        refused = MemoryRecord(content="a raw log line", kind="debug_dump")

        verdict = manager.retain(refused)

        assert not verdict.allowed
        assert verdict.reason
        assert manager.memory.recall("raw log", limit=5) == ()

    def test_retaining_writes_only_into_this_agents_store(self, tmp_path):
        """No owner argument, no way to name another memory."""
        import inspect

        parameters = set(inspect.signature(ManagerAgent.retain).parameters)
        assert parameters == {"self", "record"}

    def test_a_specialist_keeps_its_own_store(self, tmp_path):
        manager = a_session(tmp_path)
        arthur = manager.registry.get("Arthur")

        manager.retain(
            MemoryRecord(content="a fact Alfred was told", kind="user_context")
        )

        assert manager.memory is not arthur.memory
        assert arthur.memory.recall("Alfred was told", limit=5) == ()

    def test_one_agents_note_is_not_recalled_by_another(self, tmp_path):
        """Separate stores, proven by writing to one and reading the other."""
        manager = a_session(tmp_path)
        other = EchoAgent(name="Somebody", capabilities={"ledger"})
        other_memory = build_memory(settings(tmp_path), "Somebody")

        manager.retain(
            MemoryRecord(content="Project Beacon is ours", kind="user_context")
        )

        assert other_memory.recall("Beacon", limit=5) == ()

    def test_the_session_module_reaches_no_store_or_agent(self):
        """Structural. It builds records and knows nothing else."""
        tree = ast.parse(
            (REPO_ROOT / "src" / "services" / "memory" / "session.py").read_text(
                encoding="utf-8"
            )
        )
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported += [a.name for a in node.names]

        assert imported == ["__future__", "typing", "src.services.memory.memory"]

    def test_the_conversation_still_depends_on_no_store(self):
        """Session context did not become a database."""
        tree = ast.parse(
            (REPO_ROOT / "src" / "conversation" / "conversation.py").read_text(
                encoding="utf-8"
            )
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src"), node.module

    def test_continuity_added_no_write_to_the_world(self, tmp_path):
        """Remembering something must not become doing something."""
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES
        from src.services.email.mailbox import Outbox

        manager = a_session(tmp_path)
        manager.retain(MemoryRecord(content="a note", kind="user_context"))

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        with pytest.raises(TypeError):
            Outbox()
        assert manager.own_skills.tools.is_empty
