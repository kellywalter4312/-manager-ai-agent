"""Tests for memory retrieval feeding the Manager's brain.

This closes the loop: outcomes recorded after Assess become context for the
next diagnosis. The claims that carry the weight:

    TestRoutingIsNotChangedByMemory  - the Router stays mechanical
    TestIrrelevantMemoriesStayOut    - context is filtered, not dumped
    TestRetrievalFailureIsContained  - a broken store cannot stop reasoning

No Anthropic, no Gmail, no files, no network. Every brain and memory here is a
test double.
"""

import socket

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.assessor import ContractAssessor
from src.manager.brain import BrainAction, BrainDecision, ManagerBrain, NullBrain
from src.manager.manager import (
    MEMORY_CONTEXT_LIMIT,
    MEMORY_NOTE_PREFIX,
    ManagerAgent,
    MissionOutcome,
)
from src.manager.memory_policy import MemoryCategory
from src.manager.router import CapabilityRouter
from src.registry.agent_registry import AgentRegistry
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------


class RecordingBrain(ManagerBrain):
    """Remembers exactly what context it was handed."""

    def __init__(self, decision=None):
        self.decision = decision or BrainDecision(
            action=BrainAction.REPORT, reason="noted", confidence=0.5, source="recording"
        )
        self.calls = []

    @property
    def name(self):
        return "recording"

    def deliberate(self, task, agents=(), notes=()):
        self.calls.append({"task": task, "agents": tuple(agents), "notes": tuple(notes)})
        return self.decision

    @property
    def last_notes(self):
        return self.calls[-1]["notes"]


class SpecialistAgent(BaseAgent):
    def __init__(self, name="research_agent", capabilities=("research",), result=None):
        self._name = name
        self._capabilities = tuple(capabilities)
        self._result = result

    @property
    def name(self):
        return self._name

    @property
    def spec(self):
        return AgentSpec(
            name=self._name,
            description=f"{self._name} agent",
            capabilities=self._capabilities,
        )

    def execute(self, task):
        if callable(self._result):
            return self._result(task)
        return TaskResult.success(task, agent_name=self._name, output="done")


def a_task(description="research the freelance market", **fields):
    return Task(description=description, **fields)


def remembered(content, kind=MemoryCategory.DELEGATION_OUTCOME):
    return MemoryRecord(content=content, kind=kind.value)


def manager_with(brain=None, memory=None, agents=(), **kwargs):
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return ManagerAgent(
        registry=registry,
        brain=brain,
        memory=memory if memory is not None else EphemeralMemory(),
        **kwargs,
    )


def memory_holding(*contents):
    store = EphemeralMemory()
    for content in contents:
        store.remember(remembered(content))
    return store


# ----------------------------------------------------------------------
# 1-2. Retrieval happens, and reaches the brain
# ----------------------------------------------------------------------


class TestMemoriesReachTheBrain:
    def test_relevant_memories_are_retrieved(self):
        manager = manager_with(memory=memory_holding("research tasks usually succeed"))
        context = manager.recall_context(a_task("research something"))
        assert len(context) == 1

    def test_retrieved_memories_are_passed_to_the_brain(self):
        brain = RecordingBrain()
        manager = manager_with(
            brain=brain, memory=memory_holding("research tasks usually succeed")
        )
        manager.deliberate(a_task("research something"))
        assert any("usually succeed" in note for note in brain.last_notes)

    def test_recalled_notes_are_labelled_as_recalled(self):
        """The brain must be able to tell memory from instruction."""
        brain = RecordingBrain()
        manager = manager_with(brain=brain, memory=memory_holding("research is reliable"))
        manager.deliberate(a_task("research something"))
        assert brain.last_notes[0].startswith(f"{MEMORY_NOTE_PREFIX}: ")

    def test_caller_notes_are_preserved_and_come_first(self):
        brain = RecordingBrain()
        manager = manager_with(brain=brain, memory=memory_holding("research is reliable"))
        manager.deliberate(a_task("research something"), notes=["the user is in a hurry"])

        assert brain.last_notes[0] == "the user is in a hurry"
        assert brain.last_notes[1].startswith(MEMORY_NOTE_PREFIX)

    def test_caller_notes_are_never_relabelled_as_memory(self):
        brain = RecordingBrain()
        manager = manager_with(brain=brain)
        manager.deliberate(a_task(), notes=["a caller instruction"])
        assert not brain.last_notes[0].startswith(MEMORY_NOTE_PREFIX)

    def test_the_roster_still_reaches_the_brain(self):
        brain = RecordingBrain()
        manager = manager_with(brain=brain, agents=(SpecialistAgent(),))
        manager.deliberate(a_task())
        assert {a.name for a in brain.calls[0]["agents"]} == {"research_agent"}

    def test_recorded_outcomes_become_context_on_the_next_diagnosis(self):
        """The loop, end to end: Assess writes, Diagnose reads."""
        brain = RecordingBrain()
        manager = manager_with(brain=brain, agents=(SpecialistAgent(),))

        manager.handle_task(a_task("research the market"))   # records an outcome
        manager.deliberate(a_task("research the market"))    # recalls it

        assert any("research_agent" in note for note in brain.last_notes)
        assert any(note.startswith(MEMORY_NOTE_PREFIX) for note in brain.last_notes)

    def test_the_context_is_bounded(self):
        store = EphemeralMemory()
        for n in range(50):
            store.remember(remembered(f"research outcome number {n}"))

        manager = manager_with(memory=store)
        assert len(manager.recall_context(a_task("research something"))) == MEMORY_CONTEXT_LIMIT

    def test_the_limit_is_adjustable(self):
        store = memory_holding(*[f"research note {n}" for n in range(10)])
        manager = manager_with(memory=store)
        assert len(manager.recall_context(a_task("research something"), limit=2)) == 2

    def test_a_non_positive_limit_yields_no_context(self):
        manager = manager_with(memory=memory_holding("research is reliable"))
        assert manager.recall_context(a_task(), limit=0) == ()


# ----------------------------------------------------------------------
# 3. Irrelevant memories stay out
# ----------------------------------------------------------------------


class TestIrrelevantMemoriesStayOut:
    def test_unrelated_records_are_not_passed(self):
        brain = RecordingBrain()
        manager = manager_with(
            brain=brain, memory=memory_holding("the calendar sync failed last Tuesday")
        )
        manager.deliberate(a_task("research the freelance market"))
        assert brain.last_notes == ()

    def test_only_matching_records_are_carried(self):
        store = memory_holding(
            "research tasks usually succeed",
            "the calendar sync failed last Tuesday",
            "invoicing was delegated to nobody",
        )
        manager = manager_with(memory=store)
        context = manager.recall_context(a_task("research something"))

        assert len(context) == 1
        assert "research tasks usually succeed" in context[0]

    def test_a_full_store_can_still_yield_no_context(self):
        store = memory_holding(*[f"calendar entry {n}" for n in range(20)])
        manager = manager_with(memory=store)
        assert manager.recall_context(a_task("research something")) == ()

    def test_common_words_alone_do_not_make_a_match(self):
        """Two English sentences almost always share "the" or "a".

        Without stripping those from the query, every stored record would come
        back as context and relevance would mean nothing.
        """
        manager = manager_with(memory=memory_holding("the calendar sync failed"))
        assert manager.recall_context(a_task("the research is done")) == ()

    def test_a_task_with_no_distinctive_words_recalls_nothing(self):
        """An empty query would return recent records regardless of subject."""
        manager = manager_with(memory=memory_holding("research is reliable"))
        assert manager.recall_context(a_task("do it for me")) == ()

    def test_the_query_is_deterministic(self):
        task = a_task("research the freelance market")
        assert ManagerAgent._query_for(task) == ManagerAgent._query_for(task)

    def test_the_query_keeps_only_distinctive_terms(self):
        query = ManagerAgent._query_for(a_task("research the freelance market"))
        assert set(query.split()) == {"research", "freelance", "market"}

    def test_relevance_is_literal_not_inferred(self):
        """The store matches tokens. It does not reason about meaning - that
        would make retrieval a second reasoning system."""
        manager = manager_with(memory=memory_holding("remote work suits the user"))
        assert manager.recall_context(a_task("telecommuting options")) == ()


# ----------------------------------------------------------------------
# 4. No memories is an ordinary case
# ----------------------------------------------------------------------


class TestAnEmptyMemoryIsNormal:
    def test_an_empty_memory_yields_empty_context(self):
        assert manager_with().recall_context(a_task()) == ()

    def test_deliberating_with_no_memories_still_works(self):
        brain = RecordingBrain()
        decision = manager_with(brain=brain).deliberate(a_task())
        assert decision is brain.decision
        assert brain.last_notes == ()

    def test_the_default_manager_deliberates_normally(self):
        decision = ManagerAgent().deliberate(a_task())
        assert decision.action is BrainAction.UNKNOWN     # the null brain

    def test_handling_a_task_with_no_memories_is_unaffected(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        assert manager.handle_task(a_task()).status is TaskStatus.COMPLETED

    def test_no_placeholder_note_is_invented_when_nothing_is_found(self):
        """Absence is reported as absence, never as "no relevant history"."""
        brain = RecordingBrain()
        manager_with(brain=brain).deliberate(a_task())
        assert brain.last_notes == ()


# ----------------------------------------------------------------------
# 5. Retrieval failure is contained
# ----------------------------------------------------------------------


class TestRetrievalFailureIsContained:
    class ExplodingMemory(Memory):
        def remember(self, record):
            return record

        def recall(self, query="", *, kind=None, tags=(), limit=10):
            raise RuntimeError("the store is on fire")

        def forget(self, record_id):
            return False

        def clear(self):
            return 0

    def test_a_recall_failure_yields_empty_context(self):
        manager = manager_with(memory=self.ExplodingMemory())
        assert manager.recall_context(a_task()) == ()

    def test_a_recall_failure_does_not_stop_deliberation(self):
        brain = RecordingBrain()
        manager = manager_with(brain=brain, memory=self.ExplodingMemory())
        decision = manager.deliberate(a_task())
        assert decision is brain.decision
        assert brain.last_notes == ()

    def test_a_recall_failure_does_not_stop_a_task(self):
        manager = manager_with(
            agents=(SpecialistAgent(),), memory=self.ExplodingMemory()
        )
        assert manager.handle_task(a_task()).status is TaskStatus.COMPLETED

    def test_a_recall_failure_is_logged(self, caplog):
        import logging

        manager = manager_with(memory=self.ExplodingMemory())
        with caplog.at_level(logging.ERROR):
            manager.recall_context(a_task())
        assert "could not recall context" in caplog.text

    def test_unreadable_records_are_contained(self):
        """A store may return anything; rendering must not crash the Manager."""

        class Nonsense:
            @property
            def content(self):
                raise RuntimeError("no content here")

        class NonsenseMemory(Memory):
            def remember(self, record):
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                return (Nonsense(),)

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        assert manager_with(memory=NonsenseMemory()).recall_context(a_task()) == ()

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        class InterruptingMemory(Memory):
            def remember(self, record):
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                raise KeyboardInterrupt

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        manager = manager_with(memory=InterruptingMemory())
        with pytest.raises(KeyboardInterrupt):
            manager.recall_context(a_task())


# ----------------------------------------------------------------------
# 6-7. Router and Assessor are untouched
# ----------------------------------------------------------------------


class TestRoutingIsNotChangedByMemory:
    """Memory informs reasoning. It does not move the Router."""

    def _managers(self):
        agents = (
            SpecialistAgent(name="research_agent", capabilities=("research",)),
            SpecialistAgent(name="writing_agent", capabilities=("write",)),
        )
        loaded = memory_holding(
            "delegated 'research something' to 'writing_agent': accepted",
            "research work should always go to writing_agent",
        )
        return (
            manager_with(agents=agents),
            manager_with(agents=agents, memory=loaded),
        )

    def test_routing_is_identical_with_and_without_memory(self):
        empty, loaded = self._managers()
        task = a_task("research something")
        assert empty.diagnose(task).agent_name == loaded.diagnose(task).agent_name

    def test_memory_cannot_redirect_a_delegation(self):
        """The store says writing_agent; the router still says research_agent."""
        _, loaded = self._managers()
        result = loaded.handle_task(a_task("research something"))
        assert result.agent_name == "research_agent"

    def test_the_router_never_sees_memory(self):
        seen = {}

        class RecordingRouter(CapabilityRouter):
            def route(self, task, agents):
                seen["args"] = (task, tuple(agents))
                return super().route(task, agents)

        manager = manager_with(
            agents=(SpecialistAgent(),),
            memory=memory_holding("research is reliable"),
            router=RecordingRouter(),
        )
        manager.handle_task(a_task("research something"))

        task, agents = seen["args"]
        assert isinstance(task, Task)
        assert all(isinstance(a, BaseAgent) for a in agents)
        assert not hasattr(task, "memory")

    def test_the_default_router_is_unchanged(self):
        assert isinstance(manager_with().router, CapabilityRouter)

    def test_handle_task_still_does_not_consult_the_brain(self):
        """Retrieval feeds reasoning; the DATA loop stays mechanical."""
        brain = RecordingBrain()
        manager = manager_with(
            brain=brain,
            agents=(SpecialistAgent(),),
            memory=memory_holding("research is reliable"),
        )
        manager.handle_task(a_task("research something"))
        assert brain.calls == []


class TestTheAssessorIsUnchanged:
    def test_the_default_assessor_is_unchanged(self):
        assert isinstance(manager_with().assessor, ContractAssessor)

    def test_assessment_is_identical_with_and_without_memory(self):
        task = a_task()
        failed = TaskResult.failure(task, agent_name="research_agent", error="down")

        empty = manager_with()
        loaded = manager_with(memory=memory_holding("research often fails"))
        assert empty.review_mission(task, failed) is loaded.review_mission(task, failed)
        assert loaded.review_mission(task, failed) is MissionOutcome.RETRY

    def test_the_assessor_never_sees_memory(self):
        seen = {}

        class RecordingAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                seen["args"] = (task, agent_name, result)
                return super().assess(task, agent_name, result)

        manager = manager_with(
            agents=(SpecialistAgent(),),
            memory=memory_holding("research is reliable"),
            assessor=RecordingAssessor(),
        )
        manager.handle_task(a_task("research something"))
        assert set(seen["args"][0].__dict__) == set(Task(description="x").__dict__)


# ----------------------------------------------------------------------
# 8. Memory stays the Manager's
# ----------------------------------------------------------------------


class TestMemoryStaysIsolated:
    def test_two_managers_recall_independently(self):
        one = manager_with(memory=memory_holding("research is reliable"))
        two = manager_with()

        assert one.recall_context(a_task("research something"))
        assert two.recall_context(a_task("research something")) == ()

    def test_the_specialist_never_receives_memory(self):
        seen = {}

        def capture(task):
            seen["task"] = task
            return TaskResult.success(task, agent_name="research_agent", output="ok")

        manager = manager_with(
            agents=(SpecialistAgent(result=capture),),
            memory=memory_holding("research is reliable"),
        )
        manager.handle_task(a_task("research something"))

        assert not hasattr(seen["task"], "memory")
        assert not hasattr(seen["task"], "recall")

    def test_the_brain_receives_strings_not_a_store(self):
        """Context is inert text - the brain cannot query or write memory."""
        brain = RecordingBrain()
        manager = manager_with(brain=brain, memory=memory_holding("research is reliable"))
        manager.deliberate(a_task("research something"))

        assert all(isinstance(note, str) for note in brain.last_notes)
        assert not any(isinstance(note, Memory) for note in brain.last_notes)

    def test_the_brain_cannot_reach_the_managers_memory(self):
        brain = RecordingBrain()
        manager_with(brain=brain).deliberate(a_task())
        assert not hasattr(brain, "memory")

    def test_recalling_does_not_modify_memory(self):
        store = memory_holding("research is reliable")
        manager = manager_with(memory=store)
        for _ in range(5):
            manager.recall_context(a_task("research something"))
        assert len(store) == 1


# ----------------------------------------------------------------------
# 9-10. No files, deterministic
# ----------------------------------------------------------------------


class TestRetrievalIsDeterministicAndInert:
    def test_repeated_retrieval_returns_identical_context(self):
        manager = manager_with(
            memory=memory_holding("research succeeds", "research is reliable")
        )
        task = a_task("research something")
        assert manager.recall_context(task) == manager.recall_context(task)

    def test_repeated_deliberation_passes_identical_context(self):
        brain = RecordingBrain()
        manager = manager_with(
            brain=brain, memory=memory_holding("research succeeds", "research is reliable")
        )
        task = a_task("research something")
        manager.deliberate(task)
        first = brain.last_notes
        manager.deliberate(task)
        assert brain.last_notes == first

    def test_two_identical_stores_produce_identical_context(self):
        contents = ("research succeeds", "research is reliable")
        one = manager_with(memory=memory_holding(*contents))
        two = manager_with(memory=memory_holding(*contents))
        task = a_task("research something")
        assert one.recall_context(task) == two.recall_context(task)

    def test_ordering_is_stable_across_many_runs(self):
        manager = manager_with(
            memory=memory_holding(*[f"research note {n}" for n in range(10)])
        )
        task = a_task("research something")
        results = {manager.recall_context(task) for _ in range(20)}
        assert len(results) == 1

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        manager = manager_with(
            agents=(SpecialistAgent(),), memory=memory_holding("research is reliable")
        )
        manager.recall_context(a_task())
        manager.deliberate(a_task())
        manager.handle_task(a_task())
        assert list(tmp_path.iterdir()) == []

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        manager = manager_with(memory=memory_holding("research is reliable"))
        assert manager.recall_context(a_task("research something"))

    def test_nothing_persists_between_managers(self):
        manager_with(memory=memory_holding("research is reliable"))
        assert ManagerAgent().recall_context(a_task("research something")) == ()


# ----------------------------------------------------------------------
# Structural: the Memory abstraction is not bypassed
# ----------------------------------------------------------------------


class TestTheMemoryAbstractionIsNotBypassed:
    @staticmethod
    def _methods_calling(attribute: str, method: str) -> set[str]:
        import ast

        import src.manager.manager as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == method
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == attribute
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "self"
                ):
                    found.add(node.name)
        return found

    def test_reading_goes_through_one_method(self):
        """Retargeted: retrieval still has exactly one entry point, but it now
        reads through the temporal lens rather than the store directly."""
        assert self._methods_building_a_day_view() == {"recall_context"}

    def test_nothing_reads_the_store_behind_the_lens(self):
        """Stronger than before. A direct self.memory.recall would return
        every day's records, quietly putting yesterday back into today's
        context - the exact failure New Dawn exists to prevent."""
        assert self._methods_calling("memory", "recall") == set()


    @staticmethod
    def _methods_building_a_day_view() -> set[str]:
        """Methods that wrap self.memory in a DayView.

        Retargeted for New Dawn. Reading no longer calls self.memory.recall
        directly - it goes through the temporal lens - so the invariant is now
        expressed as "exactly one method builds a view over the store", which
        is the same guarantee one level up.
        """
        import ast

        import src.manager.manager as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "DayView"
                ):
                    found.add(node.name)
        return found

    def test_writing_still_goes_through_one_method(self):
        # Still exactly one. The writer moved from `_remember_delegation` to
        # the public `retain` when session continuity needed a second caller -
        # extracting it is what kept this at one method instead of two.
        assert self._methods_calling("memory", "remember") == {"retain"}

    def test_the_manager_never_deletes(self):
        assert self._methods_calling("memory", "forget") == set()
        assert self._methods_calling("memory", "clear") == set()

    def test_no_private_state_of_the_store_is_touched(self):
        """Only the four contract methods - never the list behind them."""
        import src.manager.manager as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "_records" not in source
        assert "memory._" not in source

    def test_the_declared_type_remains_the_abstraction(self):
        """Any Memory implementation works, not just the ephemeral one."""

        class MinimalMemory(Memory):
            def remember(self, record):
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                return (MemoryRecord(content="a minimal recollection"),)

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        manager = manager_with(memory=MinimalMemory())
        assert manager.recall_context(a_task()) == (
            f"{MEMORY_NOTE_PREFIX}: a minimal recollection",
        )

    def test_the_memory_contract_is_unchanged(self):
        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }

    def test_the_brain_contract_is_unchanged(self):
        import inspect

        params = set(inspect.signature(ManagerBrain.deliberate).parameters)
        assert params == {"self", "task", "agents", "notes"}
        assert set(ManagerBrain.__abstractmethods__) == {"name", "deliberate"}

    def test_no_second_memory_interface_was_invented(self):
        """Retrieval reuses recall() and the brain's existing notes slot."""
        import src.manager.manager as module

        source = open(module.__file__, encoding="utf-8").read()
        for invented in ("class MemoryQuery", "class MemoryContext", "def search("):
            assert invented not in source, invented

    def test_the_null_brain_still_works_with_context(self):
        manager = manager_with(
            brain=NullBrain(), memory=memory_holding("research is reliable")
        )
        assert manager.deliberate(a_task("research something")).action is BrainAction.UNKNOWN
