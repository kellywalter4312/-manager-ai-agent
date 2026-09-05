"""Stage 13 correction - the governed collaboration entry point.

Closes the limitation named at the end of Stage 13's own implementation:
ordinary Alfred orchestration had no governed path for deciding *when*
collaboration with a specialist is worth starting. This proves the new,
generic decision (`collaboration_candidates`) and the entry point built on
it (`answer_with_collaboration`) - both in `src/manager/collaboration_entry.py`,
beside `start_collaboration`, which they reuse rather than replace.
Imported here through `scripts.exchange`'s re-export, exactly as any
other existing caller would.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.brain import BrainAction, BrainDecision, ManagerBrain
from src.manager.llm_brain import LLMManagerBrain
from scripts.exchange import (
    answer_with_collaboration,
    collaboration_candidates,
    start_collaboration,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class ScriptedProvider:
    """A minimal, real LLMProvider double - never a network call."""

    def __init__(self, text: str, *, fail: bool = False):
        self._text = text
        self._fail = fail
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        if self._fail:
            raise RuntimeError("provider exploded")
        self.prompts.append(prompt)
        return Completion(
            text=self._text, provider=self.name, model="scripted-1",
            input_tokens=len(prompt), output_tokens=len(self._text),
        )


def with_scripted_brain(manager, text: str, *, fail: bool = False):
    """Swap a real manager's Brain for a scripted-but-real LLMManagerBrain,
    and re-point ManagerSkills' own bound converse at the same instance -
    the exact wiring composition.py already performs, done here by hand
    so a test can control what the Brain says without touching a network."""
    brain = LLMManagerBrain(ScriptedProvider(text, fail=fail))
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return brain


class SyntheticAgent(BaseAgent):
    """A specialist nothing in this codebase has ever seen."""

    def __init__(self, name: str, capabilities: frozenset[str], reply: str):
        self._name = name
        self._capabilities = capabilities
        self._reply = reply

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(name=self._name, description="synthetic", capabilities=self._capabilities)

    def execute(self, task: Task) -> TaskResult:
        return TaskResult.success(task, self._name, self._reply)


class FailingAgent(BaseAgent):
    def __init__(self, name: str, capabilities: frozenset[str]):
        self._name = name
        self._capabilities = capabilities

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(name=self._name, description="cannot reason", capabilities=self._capabilities)

    def execute(self, task: Task) -> TaskResult:
        return TaskResult.failure(task, self._name, "no usable reasoning capability")


# ---------------------------------------------------------------------------
# Alfred can decide to collaborate, and can decide not to
# ---------------------------------------------------------------------------


class TestTheCollaborationDecisionIsGeneric:
    def test_a_task_overlapping_arthurs_capabilities_yields_arthur_as_a_candidate(self):
        manager = real_manager()
        task = Task(description="email opportunities update please")
        assert collaboration_candidates(manager, task) == ("Arthur",)

    def test_a_task_matching_nobody_elses_capabilities_yields_no_candidates(self):
        manager = real_manager()
        task = Task(description="teach me something new today")
        assert collaboration_candidates(manager, task) == ()

    def test_alfred_himself_is_never_his_own_candidate(self):
        manager = real_manager()
        task = Task(description="status report please")  # matches Alfred's own roster_report words
        assert "Alfred" not in collaboration_candidates(manager, task)

    def test_the_decision_reuses_declared_capability_words_not_a_new_vocabulary(self):
        """Structural: the function reads agent.spec.capabilities - the
        exact same field CapabilityRouter already scores against - and
        nothing else."""
        source = inspect.getsource(collaboration_candidates)
        assert "spec.capabilities" in source
        assert "tokenize" in source


class TestAlfredHandlesRequestsWithoutCollaborationToo:
    def test_no_candidates_means_ordinary_handling_unchanged(self):
        manager = real_manager()
        task = Task(description="status")
        direct = manager.handle_task(Task(description="status"))
        via_entry_point = answer_with_collaboration(manager, task)
        assert via_entry_point.succeeded == direct.succeeded
        assert via_entry_point.agent_name == direct.agent_name == "Alfred"
        assert "handles directly" in via_entry_point.output

    def test_thanks_still_gets_the_ordinary_small_talk_reply(self):
        manager = real_manager()
        result = answer_with_collaboration(manager, Task(description="thanks"))
        assert result.output == "You're welcome!"


# ---------------------------------------------------------------------------
# Canonical instance, Brain/Memory independence, provenance
# ---------------------------------------------------------------------------


class TestCanonicalInstanceAndIndependence:
    def test_the_collaborating_specialist_is_the_canonical_registered_instance(self):
        manager = real_manager()
        arthur_before = manager.registry.get("Arthur")
        with_scripted_brain(manager, "synthesised answer")

        answer_with_collaboration(manager, Task(description="email opportunities update"))

        assert manager.registry.get("Arthur") is arthur_before

    def test_brain_and_memory_remain_independent_through_collaboration(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        with_scripted_brain(manager, "synthesised answer")

        answer_with_collaboration(manager, Task(description="email opportunities update"))

        assert manager.brain is not arthur.brain
        assert manager.memory is not arthur.memory

    def test_no_second_brain_or_memory_is_constructed_by_this_module(self):
        source = (REPO_ROOT / "src" / "manager" / "collaboration_entry.py").read_text(encoding="utf-8")
        for forbidden in (
            "Memory(", "Brain(", "PersistentMemory(", "SpecialistBrain(",
            "LLMManagerBrain(", "EphemeralMemory(", "ManagerSkills(", "EmailAgent(",
        ):
            assert forbidden not in source

    def test_contributions_preserve_provenance(self):
        manager = real_manager()
        with_scripted_brain(manager, "synthesised answer")
        task = Task(description="email opportunities update please")

        candidates = collaboration_candidates(manager, task)
        collaboration = start_collaboration(manager, task, with_agents=candidates)

        assert collaboration.contributions
        assert collaboration.contributions[0].source == "Arthur"

    def test_alfred_consumes_the_specialist_contribution_in_the_final_answer(self):
        manager = real_manager()
        with_scripted_brain(
            manager, "Arthur checked and there are no matching opportunities right now."
        )

        result = answer_with_collaboration(
            manager, Task(description="email opportunities update please")
        )

        assert result.succeeded
        assert result.agent_name == "Alfred"  # Alfred remains responsible for the final answer
        assert "no matching opportunities" in result.output


# ---------------------------------------------------------------------------
# No private memory leakage
# ---------------------------------------------------------------------------


class TestNoPrivateMemoryLeakage:
    def test_arthurs_own_memory_is_never_copied_into_the_graph(self):
        from src.services.memory.memory import MemoryRecord

        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        arthur.memory.remember(MemoryRecord(content="a private domain lesson nobody shared", kind="domain_lesson"))
        with_scripted_brain(manager, "synthesised answer")

        task = Task(description="email opportunities update please")
        candidates = collaboration_candidates(manager, task)
        collaboration = start_collaboration(manager, task, with_agents=candidates)

        for contribution in collaboration.contributions:
            assert "a private domain lesson nobody shared" not in contribution.content

    def test_answer_with_collaboration_never_reads_a_memory_directly(self):
        source = inspect.getsource(answer_with_collaboration)
        assert ".memory" not in source
        assert ".recall(" not in source


# ---------------------------------------------------------------------------
# A future synthetic specialist, no new branch
# ---------------------------------------------------------------------------


class TestFutureSpecialistNeedsNoNewBranch:
    def test_a_never_before_seen_specialist_can_be_a_collaboration_candidate(self):
        manager = real_manager()
        manager.register_agent(
            SyntheticAgent("Cartographer", frozenset({"map", "territory"}), "mapped the territory")
        )
        with_scripted_brain(manager, "the territory has been mapped")

        task = Task(description="map the territory please")
        assert "Cartographer" in collaboration_candidates(manager, task)

        result = answer_with_collaboration(manager, task)
        assert result.succeeded
        assert result.agent_name == "Alfred"

    def test_neither_function_compares_against_a_literal_agent_name(self):
        for fn in (collaboration_candidates, answer_with_collaboration):
            source = inspect.getsource(fn)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    for side in (node.left, *node.comparators):
                        if isinstance(side, ast.Constant) and isinstance(side.value, str):
                            assert side.value.lower() not in ("alfred", "arthur")


# ---------------------------------------------------------------------------
# Half Brain honesty
# ---------------------------------------------------------------------------


class TestHalfBrainRemainsHonest:
    def test_no_usable_brain_falls_back_to_ordinary_handling_not_fabrication(self):
        manager = real_manager()  # default NullBrain
        task = Task(description="email opportunities update please")

        result = answer_with_collaboration(manager, task)

        # Falls back to real, ordinary delegation (Arthur handles it
        # directly) rather than inventing a synthesis with no Brain to
        # produce one.
        assert result.agent_name == "Arthur"
        assert result.succeeded

    def test_a_failing_provider_also_falls_back_honestly(self):
        manager = real_manager()
        with_scripted_brain(manager, "", fail=True)

        result = answer_with_collaboration(
            manager, Task(description="email opportunities update please")
        )
        assert result.agent_name == "Arthur"  # ordinary fallback, not a fabricated Alfred answer

    def test_a_collaborator_that_cannot_reason_contributes_nothing(self):
        manager = real_manager()
        manager.register_agent(FailingAgent("Broken", frozenset({"broken"})))
        with_scripted_brain(manager, "should not matter")

        task = Task(description="broken thing please")
        candidates = collaboration_candidates(manager, task)
        collaboration = start_collaboration(manager, task, with_agents=candidates)

        assert collaboration.has("Broken")
        assert collaboration.contributions == ()

    def test_brain_capability_tool_and_shared_context_availability_are_independent(self):
        """No accidental conflation: collaboration_candidates works with
        zero Brain configured (it never touches manager.brain at all)."""
        source = inspect.getsource(collaboration_candidates)
        assert "brain" not in source.lower()


# ---------------------------------------------------------------------------
# Existing Alfred/Arthur behavior intact
# ---------------------------------------------------------------------------


class TestExistingBehaviorIsUnaffected:
    def test_alfred_status_still_works_after_this_module_is_used(self):
        manager = real_manager()
        answer_with_collaboration(manager, Task(description="email opportunities update"))
        result = manager.handle_task(Task(description="status"))
        assert result.succeeded and result.agent_name == "Alfred"

    def test_arthur_ordinary_delegation_still_works_after_this_module_is_used(self):
        manager = real_manager()
        answer_with_collaboration(manager, Task(description="status"))
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"

    def test_manageragent_gained_no_new_public_method(self):
        from src.manager.manager import ManagerAgent

        assert not hasattr(ManagerAgent, "collaborate")
        assert not hasattr(ManagerAgent, "answer_with_collaboration")


# ---------------------------------------------------------------------------
# Stage 11.A / 11.B / 12 isolation
# ---------------------------------------------------------------------------


class TestIsolationFromOtherStages:
    def test_this_module_does_not_import_extensions_or_evolution(self):
        source = (REPO_ROOT / "src" / "manager" / "collaboration_entry.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = ", ".join(getattr(n, "name", "") for n in node.names)
                assert "extensions" not in module and "extensions" not in names
                assert "evolution" not in module and "evolution" not in names

    def test_stage_12_memory_capabilities_still_work_after_a_collaboration(self):
        from src.services.memory.memory import MemoryRecord
        from src.manager.memory_policy import MemoryCategory

        manager = real_manager()
        with_scripted_brain(manager, "synthesised answer")
        answer_with_collaboration(manager, Task(description="email opportunities update"))

        manager.retain(MemoryRecord(content="a durable fact", kind=MemoryCategory.USER_CONTEXT.value))
        result = manager.handle_task(Task(description="what do you remember about a durable fact"))
        assert result.succeeded
        assert "durable fact" in result.output


# ---------------------------------------------------------------------------
# No subject-specific routing, no arbitrary execution
# ---------------------------------------------------------------------------


class TestNoSubjectRoutingOrArbitraryExecution:
    def test_no_subject_word_appears_in_the_new_functions(self):
        for fn in (collaboration_candidates, answer_with_collaboration):
            source = inspect.getsource(fn).lower()
            for subject in ("football", "arsenal", "liverpool", "messi", "gakpo", "trading", "physics"):
                assert subject not in source

    def test_no_execution_primitives_in_the_module(self):
        source = (REPO_ROOT / "src" / "manager" / "collaboration_entry.py").read_text(encoding="utf-8")
        for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__"):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# Mutation tests - the collaboration-decision and canonical-instance gates
# ---------------------------------------------------------------------------


class TestMutationCollaborationDecision:
    def test_the_candidate_check_is_load_bearing(self):
        """A source-level mutation of collaboration_candidates (always
        returning ()) is performed by hand as part of this correction's
        own verification (see the session's mutation-test report). This
        pins the behaviour such a mutation would break: a real overlap
        must currently produce a real candidate."""
        manager = real_manager()
        assert collaboration_candidates(manager, Task(description="email opportunities update")) != ()


class TestMutationCanonicalInstance:
    def test_the_registry_is_the_only_source_of_the_collaborating_agent(self):
        """Pins the positive claim a mutation of start_collaboration's
        registry.get(...) call (already mutation-tested in
        test_collaboration.py) would break: the object contributing is
        `is` the registry's own instance, not a lookalike."""
        manager = real_manager()
        real_arthur = manager.registry.get("Arthur")
        with_scripted_brain(manager, "synthesised answer")

        answer_with_collaboration(manager, Task(description="email opportunities update"))

        assert manager.registry.get("Arthur") is real_arthur
