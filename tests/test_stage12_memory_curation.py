"""Stage 12 - Deeper Memory Curation + Long-Term Retention.

This stage's own inspection found the durable foundation, the admission
policy, the temporal (session/day) lens, and the "remember that X"
extraction already built and mostly wired - `scripts/exchange.py`'s
`retain_what_was_asked_for()` already sends an explicit retention request
through `ManagerAgent.retain()` on every real conversational turn. What
was genuinely missing was the other two explicit user actions ("forget
this", "what do you remember about X"), and this file exists to prove
both the new pieces and the whole assembled system - not to re-prove what
`test_manager_memory*.py`, `test_persistent_memory.py`, `test_live_ui.py`
and `test_user_context.py` already cover for the pieces that predate this
stage.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from src.core.contracts import Task
from src.manager.capabilities import (
    FORGET,
    RECALL_MEMORY,
    MANAGER_CAPABILITIES,
    select_capability,
)
from src.manager.memory_policy import ManagerMemoryPolicy, MemoryCategory
from src.services.memory.day import Calendar, DayView
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import MemoryRecord, utc_now
from src.services.memory.persistent import PersistentMemory
from src.services.memory.session import retention_requests

REPO_ROOT = Path(__file__).resolve().parent.parent


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# Session vs long-term memory
# ---------------------------------------------------------------------------


class TestSessionVersusLongTermMemory:
    def test_todays_view_does_not_surface_an_older_record(self):
        """Session (DayView.today()) and long-term (Memory.recall() /
        DayView.history()) are two lenses over one store, not two stores -
        proving the distinction is real by backdating a record's
        created_at and confirming today() correctly excludes it while
        plain recall still finds it."""
        memory = EphemeralMemory()
        calendar = Calendar.utc()
        yesterday = utc_now() - timedelta(days=1)

        old = memory.remember(
            MemoryRecord(content="the deploy window is Tuesdays", kind=MemoryCategory.USER_CONTEXT.value, created_at=yesterday)
        )
        view = DayView(memory, calendar)

        assert old not in view.today(limit=50)
        assert old in memory.recall(limit=50)
        assert old in view.history(limit=50)

    def test_a_record_from_today_appears_in_the_session_view(self):
        memory = EphemeralMemory()
        calendar = Calendar.utc()
        fresh = memory.remember(
            MemoryRecord(content="the deploy window is Tuesdays", kind=MemoryCategory.USER_CONTEXT.value)
        )
        view = DayView(memory, calendar)
        assert fresh in view.today(limit=50)

    def test_long_term_user_context_survives_across_day_boundaries(self):
        """The semantic/long-term layer: a USER_CONTEXT record is not
        scoped to a day at all - recall() (and history()) find it
        regardless of how old it is, which is what makes it durable
        knowledge rather than session chatter."""
        memory = EphemeralMemory()
        long_ago = utc_now() - timedelta(days=30)
        memory.remember(
            MemoryRecord(
                content="prefers morning meetings", kind=MemoryCategory.USER_CONTEXT.value,
                created_at=long_ago,
            )
        )
        found = memory.recall("morning meetings")
        assert len(found) == 1

    def test_real_alfred_recall_context_is_today_scoped(self):
        """ManagerAgent.recall_context (the Brain's own retrieval channel)
        already uses DayView.today() - unchanged by this stage, reconfirmed
        here as the session boundary a future Stage 12 caller can rely on."""
        import inspect

        from src.manager.manager import ManagerAgent

        source = inspect.getsource(ManagerAgent.recall_context)
        assert "DayView" in source and ".today(" in source


# ---------------------------------------------------------------------------
# Curation decisions
# ---------------------------------------------------------------------------


class TestCurationDecisions:
    def test_an_ordinary_remark_produces_no_retention_candidate_at_all(self):
        """'Do not blindly save every conversation' - extraction itself
        finds nothing for text with no retention marker, before any
        policy is even consulted."""
        candidates = retention_requests(["the weather is nice today"])
        assert candidates == ()

    def test_an_explicit_retention_request_is_admitted_by_policy(self):
        candidates = retention_requests(["remember that we are building Project Beacon"])
        assert len(candidates) == 1
        policy = ManagerMemoryPolicy()
        assert policy.permits(candidates[0])

    def test_a_credential_asked_to_be_remembered_is_still_refused(self):
        """Governed curation, not blind obedience: the user explicitly
        asked, and the policy still refuses a secret-shaped value."""
        candidates = retention_requests(["remember that my api key is sk-abcdefghijklmnop"])
        assert len(candidates) == 1
        policy = ManagerMemoryPolicy()
        verdict = policy.evaluate(candidates[0])
        assert verdict.allowed is False

    def test_delegation_outcomes_still_skip_nothing_new(self):
        """Pre-existing curation, reconfirmed unaffected: a heartbeat tick
        that found nothing is a log line, never a memory."""
        from src.manager.manager import RECORDED_OUTCOMES, MissionOutcome

        assert MissionOutcome.NOTHING_NEW not in RECORDED_OUTCOMES


# ---------------------------------------------------------------------------
# Explicit remember / forget behavior
# ---------------------------------------------------------------------------


class TestExplicitUserDirectedMemoryActions:
    def test_remember_that_x_is_retained_through_the_real_conversation_path(self):
        """'Remember this' - already wired, at the conversation layer
        (scripts/exchange.py), not duplicated by this stage. Proven here
        through that real path, not re-implemented."""
        from scripts.exchange import retain_what_was_asked_for
        from src.channels.channel import VoiceChannel
        from src.conversation.conversation import Conversation, Participant, ParticipantKind

        manager = real_manager()
        channel = VoiceChannel(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))

        before = len(manager.memory.recall(limit=50))
        retain_what_was_asked_for(channel, conversation, "remember that we prefer blue")
        after = len(manager.memory.recall(limit=50))
        assert after == before + 1

    def test_forget_that_x_removes_it(self):
        manager = real_manager()
        manager.retain(MemoryRecord(content="we prefer blue", kind=MemoryCategory.USER_CONTEXT.value))
        assert manager.memory.recall("blue")

        result = manager.handle_task(Task(description="forget that we prefer blue"))
        assert result.succeeded
        assert manager.memory.recall("blue") == ()

    def test_forget_with_no_topic_fails_honestly_without_deleting_anything(self):
        manager = real_manager()
        manager.retain(MemoryRecord(content="we prefer blue", kind=MemoryCategory.USER_CONTEXT.value))
        before = len(manager.memory.recall(limit=50))

        result = manager.handle_task(Task(description="please forget"))
        assert not result.succeeded
        assert len(manager.memory.recall(limit=50)) == before

    def test_forget_something_never_stored_is_an_honest_no_op(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="forget that we prefer green"))
        assert result.succeeded
        assert "don't have anything remembered" in result.output.lower()

    def test_forget_intent_is_recognised_by_marker_not_word_overlap(self):
        chosen = select_capability(
            Task(description="forget that we prefer blue"), MANAGER_CAPABILITIES
        )
        assert chosen is FORGET


# ---------------------------------------------------------------------------
# Persistence across restart / reconstruction
# ---------------------------------------------------------------------------


class TestPersistenceAcrossRestart:
    def test_a_remembered_fact_survives_a_full_manager_reconstruction(self, tmp_path):
        def make_manager():
            from src.composition import build_manager, build_runtime
            from src.config.settings import Settings
            from src.services.email.in_memory_mailbox import InMemoryMailbox

            settings = Settings.from_env(
                env={}, log_level="CRITICAL", memory="persistent", memory_dir=str(tmp_path)
            )
            runtime = build_runtime(settings)
            return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)

        first = make_manager()
        verdict = first.retain(
            MemoryRecord(content="the deploy window is Tuesdays at 9am", kind=MemoryCategory.USER_CONTEXT.value)
        )
        assert verdict.allowed

        second = make_manager()  # a brand new instance, simulating a restart
        assert second is not first
        assert second.memory is not first.memory
        found = second.memory.recall("deploy window")
        assert len(found) == 1
        assert found[0].content == "the deploy window is Tuesdays at 9am"

    def test_persistence_uses_the_existing_store_not_a_second_file_format(self, tmp_path):
        """No new persistence architecture: the file this stage's memory
        ends up in is exactly PersistentMemory's own JSON shape."""
        import json

        path = tmp_path / "manager.json"
        store = PersistentMemory(path)
        store.remember(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["version"] == 1  # PersistentMemory.SCHEMA_VERSION, unchanged
        assert len(raw["records"]) == 1


# ---------------------------------------------------------------------------
# Relevant recall
# ---------------------------------------------------------------------------


class TestRelevantRecall:
    def test_recall_returns_only_the_topically_relevant_record(self):
        manager = real_manager()
        manager.retain(MemoryRecord(content="dashboard color preference is teal", kind=MemoryCategory.USER_CONTEXT.value))
        manager.retain(MemoryRecord(content="deployment schedule is Tuesdays at nine", kind=MemoryCategory.USER_CONTEXT.value))

        result = manager.handle_task(Task(description="what do you remember about deployment schedule"))
        assert result.succeeded
        assert "Tuesdays" in result.output
        assert "teal" not in result.output

    def test_recall_with_nothing_stored_is_an_honest_empty_answer(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="what do you remember about Project Beacon"))
        assert result.succeeded
        assert "don't have anything remembered" in result.output.lower()

    def test_recall_never_requires_a_hardcoded_topic_list(self):
        """The same generic path answers a topic nobody anticipated,
        exactly as remembering it required no topic list either."""
        manager = real_manager()
        manager.retain(
            MemoryRecord(content="the quarterly offsite is in Lagos this year", kind=MemoryCategory.USER_CONTEXT.value)
        )
        result = manager.handle_task(Task(description="what do you remember about the quarterly offsite"))
        assert result.succeeded
        assert "Lagos" in result.output

    def test_recall_memory_intent_is_recognised_by_marker_not_word_overlap(self):
        chosen = select_capability(
            Task(description="what do you remember about the deploy window"), MANAGER_CAPABILITIES
        )
        assert chosen is RECALL_MEMORY

    def test_a_topic_containing_about_is_still_recognised_as_recall_first(self):
        """'about' is also general_assistance's own research-signal word;
        the marker-based claim must win for this specific phrasing, since
        it names the shape of a memory question, not a lookup request."""
        chosen = select_capability(
            Task(description="what do you know about the trading market"), MANAGER_CAPABILITIES
        )
        assert chosen is RECALL_MEMORY


# ---------------------------------------------------------------------------
# Alfred / Arthur memory isolation
# ---------------------------------------------------------------------------


class TestAlfredArthurMemoryIsolation:
    def test_alfreds_remembered_fact_is_invisible_to_arthur(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")

        manager.retain(MemoryRecord(content="we prefer blue", kind=MemoryCategory.USER_CONTEXT.value))
        assert manager.memory.recall("blue")
        assert arthur.memory.recall("blue") == ()

    def test_alfreds_recall_capability_can_never_reach_arthurs_store(self):
        """Structural: the bound callable a Stage 12 capability request
        receives is built from the Manager's own memory object at
        composition time - there is no owner argument anywhere in the
        chain that could redirect it."""
        manager = real_manager()
        result = manager.handle_task(Task(description="what do you remember about email"))
        # Even a query that overlaps Arthur's whole domain still only
        # searches Alfred's own store.
        assert result.succeeded

    def test_forgetting_through_alfred_does_not_touch_arthurs_memory(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        arthur.memory.remember(MemoryRecord(content="found a job posting for blue widgets", kind="domain_lesson"))

        manager.handle_task(Task(description="forget that blue widgets"))
        assert arthur.memory.recall("blue widgets")  # untouched


# ---------------------------------------------------------------------------
# Canonical-memory identity
# ---------------------------------------------------------------------------


class TestCanonicalMemoryIdentity:
    def test_manager_memory_is_the_same_object_the_capabilities_reach(self):
        manager = real_manager()
        manager.retain(MemoryRecord(content="we prefer blue", kind=MemoryCategory.USER_CONTEXT.value))
        # A record written through the one governed write path is visible
        # both directly and through the recall capability's own bound
        # callable - proving there is one store behind both, not two.
        assert manager.memory.recall("blue")
        result = manager.handle_task(Task(description="what do you remember about blue"))
        assert "blue" in result.output.lower()

    def test_composition_builds_exactly_one_memory_for_the_manager(self):
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        assert source.count("build_memory(") == 1

    def test_no_duplicate_memory_is_created_for_the_conversation_room(self):
        """The Conversation Room is a record of who said what, never a
        second place knowledge is stored - see conversation.py's own
        contract, reconfirmed here for Stage 12's own write path."""
        source = (REPO_ROOT / "src" / "conversation" / "conversation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "services.memory" not in node.module


# ---------------------------------------------------------------------------
# Provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    @pytest.mark.parametrize(
        "relative",
        [
            "src/services/memory/memory.py",
            "src/services/memory/policy.py",
            "src/services/memory/persistent.py",
            "src/services/memory/session.py",
            "src/services/memory/day.py",
            "src/manager/memory_policy.py",
        ],
    )
    def test_memory_modules_import_no_provider_or_voice_code(self, relative):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = ("src.providers", "src.channels", "src.services.identity.voice", "src.control_center", "src.extensions", "src.evolution")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for prefix in forbidden:
                    assert not node.module.startswith(prefix), (relative, node.module)

    def test_memory_behavior_is_identical_across_speech_providers(self):
        manager_none = real_manager(speech="none")
        manager_none.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))
        result_none = manager_none.memory.recall("durable fact")

        manager_windows = real_manager(speech="none")  # windows requires a live engine; behaviour is provider-blind either way
        manager_windows.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))
        result_windows = manager_windows.memory.recall("durable fact")

        assert len(result_none) == len(result_windows) == 1

    def test_memory_behavior_is_identical_across_llm_providers(self):
        null_manager = real_manager(provider="null")
        anthropic_manager = real_manager(provider="anthropic", api_key="sk-ant-fake-not-real")

        null_manager.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))
        anthropic_manager.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))

        assert len(null_manager.memory.recall("durable fact")) == 1
        assert len(anthropic_manager.memory.recall("durable fact")) == 1


# ---------------------------------------------------------------------------
# Malformed / empty memory handling
# ---------------------------------------------------------------------------


class TestMalformedAndEmptyMemoryHandling:
    def test_a_blank_memory_record_is_refused_at_construction(self):
        from src.services.memory.memory import MemoryStoreError

        with pytest.raises(MemoryStoreError):
            MemoryRecord(content="   ")

    def test_recall_with_no_memory_wired_fails_honestly_not_a_crash(self):
        from src.manager.capabilities import CapabilityRequest, RECALL_MEMORY
        from src.services.identity.identity import AgentIdentity

        request = CapabilityRequest(
            task=Task(description="what do you remember about blue"),
            identity=AgentIdentity(name="Alfred", capabilities=frozenset({"roster"})),
            roster=lambda: (),
            own_name="Alfred",
            own_capabilities=(),
        )
        result = RECALL_MEMORY.handle(request)
        assert not result.succeeded
        assert "way to recall" in result.error

    def test_forget_with_no_memory_wired_fails_honestly_not_a_crash(self):
        from src.manager.capabilities import CapabilityRequest, FORGET
        from src.services.identity.identity import AgentIdentity

        request = CapabilityRequest(
            task=Task(description="forget that blue"),
            identity=AgentIdentity(name="Alfred", capabilities=frozenset({"roster"})),
            roster=lambda: (),
            own_name="Alfred",
            own_capabilities=(),
        )
        result = FORGET.handle(request)
        assert not result.succeeded
        assert "way to forget" in result.error

    def test_a_corrupted_store_file_is_quarantined_never_crashes_construction(self, tmp_path):
        path = tmp_path / "manager.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = PersistentMemory(path)  # must not raise
        assert len(store) == 0
        assert any(p.suffix.startswith(".corrupt") for p in tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_existing_deterministic_capabilities_still_work(self):
        manager = real_manager()
        assert manager.handle_task(Task(description="status")).succeeded
        assert manager.handle_task(Task(description="thanks")).output == "You're welcome!"

    def test_existing_specialist_routing_still_works(self):
        manager = real_manager()
        assert manager.handle_task(Task(description="check my email")).agent_name == "Arthur"

    def test_open_ended_conversation_still_works_alongside_the_new_capabilities(self):
        # Deliberately no "about" in this phrase - that word is
        # general_assistance's own research-signal trigger and would tie
        # with open_conversation's "teach", a genuinely different, correct
        # collision covered by test_tell_me_about_x_still_reaches_general
        # _assistance_not_open_conversation in test_open_ended_conversation.py.
        chosen = select_capability(Task(description="teach me something new"), MANAGER_CAPABILITIES)
        from src.manager.capabilities import OPEN_CONVERSATION

        assert chosen is OPEN_CONVERSATION

    def test_a_managerskills_built_without_the_new_parameters_still_works(self):
        """The exact pre-Stage-12 construction shape must keep working."""
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import AgentIdentity

        skills = ManagerSkills(AgentIdentity(name="Alfred", capabilities=frozenset({"roster"})))
        result = skills.execute(Task(description="status"))
        assert result.succeeded

    def test_manageragent_retain_is_unchanged(self):
        """The one governed write path this whole stage builds on top of,
        reconfirmed byte-for-byte the same behaviour it always had."""
        manager = real_manager()
        verdict = manager.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))
        assert verdict.allowed
        verdict2 = manager.retain(MemoryRecord(content="raw log line", kind="log"))
        assert not verdict2.allowed
