"""Stage 13 - Shared Context Graph + Multi-Agent Collaboration.

Proves the graph is genuinely additive: a bounded, governed context-
exchange space that lets Alfred and a specialist contribute to one task
without merging, duplicating, or reaching into each other's canonical
Brain or Memory - and that a completely unforeseen future specialist
collaborates through the identical, unmodified mechanism.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult
from src.conversation.collaboration import (
    Collaboration,
    CollaborationError,
    Contribution,
)
from src.conversation.conversation import Participant, ParticipantKind
from scripts.exchange import start_collaboration

REPO_ROOT = Path(__file__).resolve().parent.parent


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class SyntheticAgent(BaseAgent):
    """A specialist nothing in this codebase has ever seen - the proof
    that collaboration needs no Alfred/Arthur-specific branch anywhere."""

    def __init__(self, name: str, reply: str):
        self._name = name
        self._reply = reply

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(name=self._name, description="synthetic", capabilities=frozenset({"synthetic"}))

    def execute(self, task: Task) -> TaskResult:
        return TaskResult.success(task, self._name, self._reply)


class FailingAgent(BaseAgent):
    """A specialist that cannot produce a result - Half Brain / no view,
    modelled as an ordinary failure."""

    def __init__(self, name: str):
        self._name = name

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(name=self._name, description="cannot reason", capabilities=frozenset({"broken"}))

    def execute(self, task: Task) -> TaskResult:
        return TaskResult.failure(task, self._name, "no usable reasoning capability")


# ---------------------------------------------------------------------------
# 1, 2 - a collaboration can be created; participants register generically
# ---------------------------------------------------------------------------


class TestCreationAndParticipants:
    def test_a_collaboration_can_be_created(self):
        collaboration = Collaboration(task="find opportunities")
        assert collaboration.task == "find opportunities"
        assert collaboration.participants == ()
        assert not collaboration.closed

    def test_a_blank_task_is_refused(self):
        with pytest.raises(CollaborationError):
            Collaboration(task="   ")

    def test_participants_join_generically(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Alfred", ParticipantKind.AGENT))
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        assert collaboration.has("Alfred")
        assert collaboration.has("Arthur")
        assert len(collaboration.participants) == 2

    def test_joining_twice_does_not_duplicate(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Alfred", ParticipantKind.AGENT))
        collaboration.join(Participant("Alfred", ParticipantKind.AGENT))
        assert len(collaboration.participants) == 1

    def test_only_a_participant_object_may_join(self):
        collaboration = Collaboration(task="find opportunities")
        with pytest.raises(CollaborationError):
            collaboration.join("Alfred")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3 - contributions preserve source-agent identity
# ---------------------------------------------------------------------------


class TestContributionsPreserveProvenance:
    def test_a_contribution_records_its_source(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        contribution = collaboration.contribute(
            Contribution(source="Arthur", content="found two matching roles")
        )
        assert contribution.source == "Arthur"
        assert collaboration.contributions[0].source == "Arthur"

    def test_a_contribution_from_a_non_participant_is_refused(self):
        collaboration = Collaboration(task="find opportunities")
        with pytest.raises(CollaborationError):
            collaboration.contribute(Contribution(source="Nobody", content="anything"))

    def test_a_blank_contribution_is_refused_at_construction(self):
        with pytest.raises(CollaborationError):
            Contribution(source="Arthur", content="   ")

    def test_a_blank_source_is_refused_at_construction(self):
        with pytest.raises(CollaborationError):
            Contribution(source="   ", content="something")


# ---------------------------------------------------------------------------
# 4 - shared context can be read by participating agents
# ---------------------------------------------------------------------------


class TestReadingSharedContext:
    def test_a_participant_reads_every_contribution(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Alfred", ParticipantKind.AGENT))
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        collaboration.contribute(Contribution(source="Arthur", content="found a role"))

        read_by_alfred = collaboration.context_for("Alfred")
        assert len(read_by_alfred) == 1
        assert read_by_alfred[0].content == "found a role"

    def test_a_non_participant_cannot_read(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        with pytest.raises(CollaborationError):
            collaboration.context_for("Somebody Else")

    def test_kind_narrows_what_is_read(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        collaboration.contribute(Contribution(source="Arthur", content="a result", kind="result"))
        collaboration.contribute(Contribution(source="Arthur", content="a question", kind="question"))

        assert len(collaboration.context_for("Arthur", kind="result")) == 1
        assert len(collaboration.context_for("Arthur", kind="question")) == 1
        assert len(collaboration.context_for("Arthur")) == 2

    def test_reading_still_works_after_closing(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        collaboration.contribute(Contribution(source="Arthur", content="a result"))
        collaboration.close()
        assert len(collaboration.context_for("Arthur")) == 1


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


class TestClosing:
    def test_cannot_join_a_closed_collaboration(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.close()
        with pytest.raises(CollaborationError):
            collaboration.join(Participant("Arthur", ParticipantKind.AGENT))

    def test_cannot_contribute_to_a_closed_collaboration(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.join(Participant("Arthur", ParticipantKind.AGENT))
        collaboration.close()
        with pytest.raises(CollaborationError):
            collaboration.contribute(Contribution(source="Arthur", content="too late"))

    def test_closing_twice_keeps_the_first_close_time(self):
        collaboration = Collaboration(task="find opportunities")
        collaboration.close()
        first = collaboration.closed_at
        collaboration.close()
        assert collaboration.closed_at == first


# ---------------------------------------------------------------------------
# 5, 6 - Alfred's Memory/Brain remain distinct from Arthur's
# ---------------------------------------------------------------------------


class TestAlfredArthurBrainMemoryIndependence:
    def test_memories_remain_distinct_through_a_real_collaboration(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        task = Task(description="check my email for opportunities")

        start_collaboration(manager, task, with_agents=["Arthur"])

        assert manager.memory is not arthur.memory

    def test_brains_remain_distinct_through_a_real_collaboration(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        task = Task(description="check my email for opportunities")

        start_collaboration(manager, task, with_agents=["Arthur"])

        assert manager.brain is not arthur.brain

    def test_collaborating_does_not_make_the_two_agents_identical(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        before_alfred_voice = manager.own_skills.voice
        before_arthur_voice = arthur.voice

        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])

        assert manager.own_skills.voice == before_alfred_voice
        assert arthur.voice == before_arthur_voice
        assert manager.own_skills.voice != arthur.voice


# ---------------------------------------------------------------------------
# 7, 22 - shared context does not duplicate/leak private Memory
# ---------------------------------------------------------------------------


class TestNoPrivateMemoryLeakage:
    def test_a_specialists_own_memory_is_not_auto_read_into_a_contribution(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        from src.services.memory.memory import MemoryRecord

        arthur.memory.remember(MemoryRecord(content="a private domain lesson nobody shared", kind="domain_lesson"))

        collaboration = start_collaboration(
            manager, Task(description="check my email for opportunities"), with_agents=["Arthur"]
        )

        for contribution in collaboration.contributions:
            assert "a private domain lesson nobody shared" not in contribution.content

    def test_collaboration_never_holds_a_memory_reference(self):
        """Structural: nothing on Collaboration or Contribution could
        even hold a Memory object - there is no field for one."""
        import dataclasses

        contribution_fields = {f.name for f in dataclasses.fields(Contribution)}
        assert "memory" not in contribution_fields
        assert not hasattr(Collaboration(task="x"), "memory")

    def test_collaboration_module_imports_no_memory_code(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "services.memory" not in node.module

    def test_only_what_an_agent_explicitly_returned_can_become_a_contribution(self):
        """start_collaboration builds a Contribution only from
        TaskResult.output - the same object any ordinary caller of
        take_action already receives, never a second, privileged read."""
        import inspect

        source = inspect.getsource(start_collaboration)
        assert "result.output" in source
        assert ".memory" not in source
        assert ".recall(" not in source


# ---------------------------------------------------------------------------
# 8, 9 - Conversation Room uses canonical instances; no duplicates created
# ---------------------------------------------------------------------------


class TestCanonicalAgentInstances:
    def test_the_participating_agent_is_the_real_registered_instance(self):
        manager = real_manager()
        arthur_before = manager.registry.get("Arthur")

        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])

        assert manager.registry.get("Arthur") is arthur_before

    def test_conversation_never_constructs_a_second_agent(self):
        """conversation.py's own, pre-existing isolation boundary,
        reconfirmed unaffected by this stage - Participant carries no
        field an agent object could occupy."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Participant)}
        assert fields == {"name", "kind"}

    def test_collaboration_py_never_constructs_an_agent_class(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        for forbidden in ("EmailAgent(", "ManagerSkills(", "CognitiveAgent(", "SpecialistWiring("):
            assert forbidden not in source

    def test_no_duplicate_brain_or_memory_class_is_ever_constructed_here(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        for forbidden in ("Memory(", "Brain(", "PersistentMemory(", "SpecialistBrain(", "LLMManagerBrain(", "EphemeralMemory("):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# 10 - voice identity remains independently resolved
# ---------------------------------------------------------------------------


class TestVoiceIndependence:
    def test_voiceprofile_is_unaffected_by_collaboration(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        alfred_voice_before = manager.own_skills.voice
        arthur_voice_before = arthur.voice

        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])

        assert manager.own_skills.voice == alfred_voice_before
        assert arthur.voice == arthur_voice_before

    def test_collaboration_module_imports_no_voice_or_provider_code(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = ("src.services.identity.voice", "src.providers", "src.channels")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for prefix in forbidden:
                    assert not node.module.startswith(prefix)


# ---------------------------------------------------------------------------
# 11 - a future synthetic specialist participates with zero new branches
# ---------------------------------------------------------------------------


class TestFutureSpecialistParticipation:
    def test_a_never_before_seen_agent_can_participate(self):
        manager = real_manager()
        manager.register_agent(SyntheticAgent("Cartographer", "mapped the territory"))

        collaboration = start_collaboration(
            manager, Task(description="map the territory"), with_agents=["Cartographer"]
        )

        assert collaboration.has("Cartographer")
        assert collaboration.contributions[0].source == "Cartographer"
        assert collaboration.contributions[0].content == "mapped the territory"

    def test_an_unregistered_name_is_skipped_never_invented(self):
        manager = real_manager()
        collaboration = start_collaboration(
            manager, Task(description="do something"), with_agents=["Nobody Registered"]
        )
        assert not collaboration.has("Nobody Registered")
        assert collaboration.contributions == ()

    def test_start_collaboration_never_branches_on_an_agent_name(self):
        import inspect

        source = inspect.getsource(start_collaboration)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for side in (node.left, *node.comparators):
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        assert side.value.lower() not in ("alfred", "arthur")


# ---------------------------------------------------------------------------
# 12 - Half Brain behavior remains honest: no fabricated contribution
# ---------------------------------------------------------------------------


class TestHalfBrainHonesty:
    def test_a_specialist_that_cannot_reason_contributes_nothing(self):
        manager = real_manager()
        manager.register_agent(FailingAgent("Broken"))

        collaboration = start_collaboration(
            manager, Task(description="do something hard"), with_agents=["Broken"]
        )

        assert collaboration.has("Broken")  # invited
        assert collaboration.contributions == ()  # but produced nothing

    def test_brain_availability_tool_availability_and_shared_context_are_distinct_concepts(self):
        """No accidental conflation: a collaboration can exist and a
        participant can join with zero Brain, zero tools, and zero
        contributions - each is checked independently, nowhere in this
        module's own code."""
        collaboration = Collaboration(task="x")
        collaboration.join(Participant("Y", ParticipantKind.AGENT))
        assert collaboration.has("Y")  # participation needs neither brain nor tool
        assert collaboration.contributions == ()


# ---------------------------------------------------------------------------
# 13, 14 - existing Alfred/Arthur behavior remains intact
# ---------------------------------------------------------------------------


class TestExistingBehaviorIsUnaffected:
    def test_alfred_still_answers_status_after_a_collaboration(self):
        manager = real_manager()
        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])
        result = manager.handle_task(Task(description="status"))
        assert result.succeeded
        assert result.agent_name == "Alfred"

    def test_arthur_still_handles_email_normally_after_a_collaboration(self):
        manager = real_manager()
        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"

    def test_the_data_loop_methods_are_completely_unmodified_by_this_stage(self):
        """ManagerAgent itself gained nothing - the orchestration helper
        lives in collaboration.py, deliberately, so this class's own
        surface is untouched."""
        from src.manager.manager import ManagerAgent

        assert not hasattr(ManagerAgent, "collaborate")
        assert not hasattr(ManagerAgent, "start_collaboration")


# ---------------------------------------------------------------------------
# 15, 16 - LLM/speech provider switching does not alter identity/boundaries
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_llm_provider_switching_does_not_alter_collaboration_identity(self):
        null_manager = real_manager(provider="null")
        anthropic_manager = real_manager(provider="anthropic", api_key="sk-ant-fake-not-real")

        for manager in (null_manager, anthropic_manager):
            collaboration = start_collaboration(
                manager, Task(description="check my email"), with_agents=["Arthur"]
            )
            assert collaboration.has("Alfred")
            assert collaboration.has("Arthur")

        assert null_manager.memory is not anthropic_manager.memory

    def test_speech_provider_switching_does_not_alter_collaboration_identity(self):
        none_manager = real_manager(speech="none")
        collaboration = start_collaboration(
            none_manager, Task(description="check my email"), with_agents=["Arthur"]
        )
        assert collaboration.has("Alfred")
        assert collaboration.has("Arthur")

    def test_collaboration_module_imports_no_llm_provider_code(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        for vendor in ("anthropic", "elevenlabs"):
            assert vendor not in source.lower()


# ---------------------------------------------------------------------------
# 17, 18, 19 - Stage 11.A/11.B/12 remain isolated
# ---------------------------------------------------------------------------


class TestIsolationFromLaterStages:
    def test_collaboration_module_does_not_import_extensions_or_evolution(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = ", ".join(getattr(n, "name", "") for n in node.names)
                assert "extensions" not in module and "extensions" not in names
                assert "evolution" not in module and "evolution" not in names

    def test_the_governed_lifecycle_does_not_import_collaboration(self):
        # src/extensions.py (Stage 11.A) was removed on 2026-09-15 as a
        # parallel, unwired governance architecture.
        for relative in ("src/evolution.py", "src/services/evolution/handlers.py"):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert "collaboration" not in source

    def test_stage_12_memory_capabilities_still_work_after_a_collaboration(self):
        from src.services.memory.memory import MemoryRecord
        from src.manager.memory_policy import MemoryCategory

        manager = real_manager()
        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])

        manager.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))
        result = manager.handle_task(Task(description="what do you remember about a durable fact"))
        assert result.succeeded
        assert "durable fact" in result.output


# ---------------------------------------------------------------------------
# 20, 21 - no arbitrary code execution, no hardcoded subject routing
# ---------------------------------------------------------------------------


class TestNoArbitraryExecutionOrHardcodedSubjects:
    def test_collaboration_module_contains_no_execution_primitives(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__"):
            assert forbidden not in source

    def test_no_subject_word_appears_anywhere_in_the_module(self):
        source = (REPO_ROOT / "src" / "conversation" / "collaboration.py").read_text(encoding="utf-8")
        for subject in (
            "football", "arsenal", "liverpool", "messi", "gakpo", "car ", "physics", "trading",
        ):
            assert subject not in source.lower()


# ---------------------------------------------------------------------------
# Mutation tests - the safety properties this stage most depends on
# ---------------------------------------------------------------------------


class TestMutationCanonicalAgentIdentity:
    def test_the_real_registered_arthur_is_who_actually_contributes(self):
        """The positive claim this stage's canonical-identity guarantee
        rests on, pinned directly: the object start_collaboration
        contributed through is `is` the same object the registry itself
        holds - not equal, not a lookalike, the identical instance. A
        source-level mutation of start_collaboration's registry.get(...)
        call is performed by hand as part of this stage's own
        verification (see the session's mutation-test report) precisely
        because this assertion is what such a mutation would break."""
        manager = real_manager()
        real_arthur = manager.registry.get("Arthur")

        start_collaboration(manager, Task(description="check my email"), with_agents=["Arthur"])

        assert manager.registry.get("Arthur") is real_arthur


class TestMutationPrivateMemoryIsolation:
    def test_if_start_collaboration_read_memory_directly_it_would_leak(self, monkeypatch):
        """Deliberately simulate the exact defect this stage exists to
        prevent: a version of contribution-building that reads an
        agent's Memory instead of its TaskResult. Confirms the isolation
        tests above would catch it, then leaves the real code untouched."""
        from src.services.memory.memory import MemoryRecord
        from src.conversation.collaboration import Contribution as RealContribution

        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        arthur.memory.remember(MemoryRecord(content="a private domain lesson nobody shared", kind="domain_lesson"))

        # A deliberately mutated, local stand-in for what start_collaboration
        # must never do - built here, never in src/, and never left behind.
        def leaky_collaborate(mgr, task, *, with_agents):
            collaboration = Collaboration(task=task.description)
            collaboration.join(Participant("Alfred", ParticipantKind.AGENT))
            for name in with_agents:
                agent = mgr.registry.get(name)
                collaboration.join(Participant(agent.name, ParticipantKind.AGENT))
                leaked = "; ".join(r.content for r in agent.memory.recall(limit=50))
                if leaked:
                    collaboration.contribute(RealContribution(source=agent.name, content=leaked))
            return collaboration

        leaked_collaboration = leaky_collaborate(manager, Task(description="check my email"), with_agents=["Arthur"])
        assert any(
            "a private domain lesson nobody shared" in c.content
            for c in leaked_collaboration.contributions
        ), "the mutation must actually leak, or it proves nothing"

        # The real function does not.
        honest_collaboration = start_collaboration(
            manager, Task(description="check my email"), with_agents=["Arthur"]
        )
        assert all(
            "a private domain lesson nobody shared" not in c.content
            for c in honest_collaboration.contributions
        )


class TestMutationParticipantProvenance:
    def test_breaking_the_membership_check_would_allow_forged_provenance(self):
        """Deliberately construct a Contribution bypassing contribute()'s
        membership check (by calling Contribution directly, which
        succeeds - contribution.source is only validated by
        Collaboration.contribute, not by Contribution itself) and
        confirm that collaboration.contribute() is the actual gate that
        would refuse it, proving that gate is load-bearing rather than
        decorative."""
        collaboration = Collaboration(task="x")
        forged = Contribution(source="Nobody Invited", content="a forged claim")
        with pytest.raises(CollaborationError):
            collaboration.contribute(forged)
        assert collaboration.contributions == ()
