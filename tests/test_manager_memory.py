"""Tests for the Manager's memory dependency.

This step wires an existing abstraction in; it adds no behaviour. So the tests
that matter most are the ones proving nothing *else* changed, and that the
Manager holds a memory rather than doing anything with it:

    TestNothingIsRecordedAutomatically  - the boundary, not a feature
    TestExistingBehaviourIsUnchanged    - the DATA loop is untouched
    TestMemoriesAreNotShared            - what makes per-specialist memory work

No database, no files, no network. Nothing is stubbed to make that true.
"""

import socket

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.brain import BrainAction, NullBrain
from src.manager.manager import ManagerAgent, MissionOutcome
from src.manager.router import CapabilityRouter
from src.registry.agent_registry import AgentRegistry
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord


def code_of(module) -> str:
    """A module's executable lines, with docstrings and comments removed.

    Structural tests must inspect what a module *does*, not what it says about
    itself. manager.py's docstring discusses memory and storage at length.
    """
    source = open(module.__file__, encoding="utf-8").read()
    lines, in_docstring = [], False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.count('"""') == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith(("#", '"""')):
            continue
        lines.append(line.split("#")[0])
    return "\n".join(lines).lower()


class StubAgent(BaseAgent):
    def __init__(self, name="researcher", capabilities=("research",)):
        self._name = name
        self._capabilities = tuple(capabilities)
        self.executions = []

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
        self.executions.append(task)
        return TaskResult.success(task, agent_name=self._name, output="done")


def manager_with(memory=None, agents=(), **kwargs):
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return ManagerAgent(registry=registry, memory=memory, **kwargs)


def a_task(description="research something"):
    return Task(id="t-1", description=description)


# ----------------------------------------------------------------------
# 1-3. Injection
# ----------------------------------------------------------------------


class TestMemoryIsInjected:
    def test_the_manager_accepts_an_injected_memory(self):
        assert manager_with(EphemeralMemory()).memory is not None

    def test_the_exact_instance_supplied_is_retained(self):
        """Not a copy, not a wrapper - the same object."""
        memory = EphemeralMemory()
        assert manager_with(memory).memory is memory

    def test_records_stored_before_injection_are_still_there(self):
        memory = EphemeralMemory()
        memory.remember(MemoryRecord(content="the user prefers remote work"))
        manager = manager_with(memory)
        assert len(manager.memory) == 1
        assert manager.memory.recall("remote")[0].content.startswith("the user")

    def test_the_manager_exposes_the_memory_it_was_given(self):
        memory = EphemeralMemory()
        manager = manager_with(memory)
        manager.memory.remember(MemoryRecord(content="something worth keeping"))
        assert len(memory) == 1        # the caller's own object was written to

    def test_the_default_is_a_usable_memory(self):
        assert isinstance(manager_with().memory, Memory)

    def test_the_default_memory_starts_empty(self):
        assert len(manager_with().memory) == 0
        assert manager_with().memory.recall() == ()

    def test_any_memory_implementation_is_accepted(self):
        """The Manager depends on the abstraction, not on EphemeralMemory."""

        class CountingMemory(Memory):
            def __init__(self):
                self.calls = 0

            def remember(self, record):
                self.calls += 1
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                return ()

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        memory = CountingMemory()
        manager = manager_with(memory)
        manager.memory.remember(MemoryRecord(content="x"))
        assert memory.calls == 1

    def test_the_declared_type_is_the_abstraction(self):
        import inspect

        import src.manager.manager as module

        signature = inspect.signature(module.ManagerAgent.__init__)
        assert signature.parameters["memory"].default is None

    def test_memory_sits_alongside_brain_not_inside_it(self):
        """Two separate dependencies, as the architecture requires."""
        manager = manager_with(EphemeralMemory(), brain=NullBrain())
        assert manager.brain is not manager.memory
        assert not hasattr(manager.brain, "memory")
        assert not hasattr(manager.memory, "brain")


# ----------------------------------------------------------------------
# 4, 6. Separate instances, no singleton
# ----------------------------------------------------------------------


class TestMemoriesAreNotShared:
    """What will let each specialist own its own memory later."""

    def test_two_managers_can_have_separate_memories(self):
        first, second = EphemeralMemory(), EphemeralMemory()
        one, two = manager_with(first), manager_with(second)

        one.memory.remember(MemoryRecord(content="only the first knows this"))

        assert len(one.memory) == 1
        assert len(two.memory) == 0
        assert two.memory.recall("first") == ()

    def test_default_memories_are_not_shared_either(self):
        """The dangerous case: two Managers built with no arguments at all."""
        one, two = ManagerAgent(), ManagerAgent()

        assert one.memory is not two.memory

        one.memory.remember(MemoryRecord(content="private to the first manager"))
        assert len(one.memory) == 1 and len(two.memory) == 0

    def test_a_default_memory_is_fresh_every_time(self):
        for _ in range(5):
            assert len(ManagerAgent().memory) == 0

    def test_there_is_no_module_level_memory(self):
        """No global singleton hiding in the module."""
        import src.manager.manager as module

        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            assert not isinstance(value, (Memory, MemoryRecord)), name

    def test_clearing_one_manager_memory_leaves_the_other_alone(self):
        one, two = ManagerAgent(), ManagerAgent()
        one.memory.remember(MemoryRecord(content="a"))
        two.memory.remember(MemoryRecord(content="b"))

        one.memory.clear()
        assert len(one.memory) == 0 and len(two.memory) == 1

    def test_a_shared_memory_can_still_be_chosen_deliberately(self):
        """Sharing is possible - it just has to be asked for explicitly."""
        shared = EphemeralMemory()
        one, two = manager_with(shared), manager_with(shared)
        one.memory.remember(MemoryRecord(content="deliberately shared"))
        assert len(two.memory) == 1


# ----------------------------------------------------------------------
# THE BOUNDARY: holding memory is not using it
# ----------------------------------------------------------------------


class TestNothingIsRecordedAutomatically:
    """Only delegation outcomes are recorded. Everything else stays out.

    These assertions were written when *nothing* was recorded automatically.
    Delegation outcomes are now in scope, so each has been retargeted to the
    boundary that still holds - see test_manager_delegation_memory.py for the
    tests covering what delegation now writes.
    """

    def test_only_delegation_outcomes_are_recorded(self):
        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        manager.handle_task(a_task())
        kinds = {r.kind for r in manager.memory.recall()}
        assert kinds == {"delegation_outcome"}

    def test_no_other_category_is_populated(self):
        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        for n in range(10):
            manager.handle_task(Task(id=f"t-{n}", description="research something"))
        kinds = {r.kind for r in manager.memory.recall(limit=100)}
        assert "user_context" not in kinds
        assert "decision" not in kinds
        assert "lesson" not in kinds

    def test_a_failed_task_records_nothing(self):
        manager = manager_with(EphemeralMemory())
        manager.handle_task(Task(id="t-2", description="something unroutable"))
        assert len(manager.memory) == 0

    def test_routing_records_nothing(self):
        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        manager.diagnose(a_task())
        assert len(manager.memory) == 0

    def test_assessment_records_nothing(self):
        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        task = a_task()
        manager.assess(task, "researcher", TaskResult.success(task, "researcher", "ok"))
        assert len(manager.memory) == 0

    def test_deliberating_records_nothing(self):
        manager = manager_with(EphemeralMemory())
        manager.deliberate(a_task())
        assert len(manager.memory) == 0

    def test_reviewing_a_mission_records_nothing(self):
        manager = manager_with(EphemeralMemory())
        task = a_task()
        manager.review_mission(task, TaskResult.success(task, "researcher", "ok"))
        assert len(manager.memory) == 0

    def test_the_manager_never_deletes_memory_on_its_own(self):
        """It writes delegation outcomes and reads context; it never removes.

        Automatic deletion is not planned at all - a store that quietly
        dropped things would make its own contents unauditable.
        """
        import src.manager.manager as module

        code = code_of(module)
        assert "self.memory.forget(" not in code
        assert "self.memory.clear(" not in code

    def test_a_memory_that_raises_cannot_break_a_task(self):
        """Every access raises; the task still completes.

        Reading, forgetting and clearing are never attempted at all. Writing
        is, and its failure is contained - see the failure-isolation tests in
        test_manager_delegation_memory.py.
        """

        class UntouchableMemory(Memory):
            def remember(self, record):
                raise AssertionError("the Manager recorded something on its own")

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                raise AssertionError("the Manager read memory on its own")

            def forget(self, record_id):
                raise AssertionError("the Manager deleted something on its own")

            def clear(self):
                raise AssertionError("the Manager cleared memory on its own")

        manager = manager_with(UntouchableMemory(), agents=(StubAgent(),))
        result = manager.handle_task(a_task())
        manager.deliberate(a_task())
        assert result.status is TaskStatus.COMPLETED


# ----------------------------------------------------------------------
# 5, 9. Nothing else changed
# ----------------------------------------------------------------------


class TestExistingBehaviourIsUnchanged:
    def test_a_manager_built_with_no_arguments_still_works(self):
        manager = ManagerAgent()
        manager.register_agent(StubAgent())
        result = manager.handle_task(a_task())
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "researcher"

    def test_results_are_identical_with_and_without_memory(self):
        task = a_task()
        without = manager_with(agents=(StubAgent(),)).handle_task(task)
        with_memory = manager_with(EphemeralMemory(), agents=(StubAgent(),)).handle_task(task)

        assert without.status == with_memory.status
        assert without.agent_name == with_memory.agent_name
        assert without.output == with_memory.output
        assert without.error == with_memory.error

    def test_routing_is_unchanged(self):
        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        decision = manager.diagnose(a_task())
        assert decision is not None and decision.agent_name == "researcher"

    def test_the_default_router_is_still_the_capability_router(self):
        assert isinstance(manager_with(EphemeralMemory()).router, CapabilityRouter)

    def test_no_agent_found_still_behaves_the_same(self):
        result = manager_with(EphemeralMemory()).handle_task(a_task())
        assert result.status is TaskStatus.NO_AGENT_FOUND

    def test_assessment_is_unchanged(self):
        manager = manager_with(EphemeralMemory())
        task = a_task()
        failed = TaskResult.failure(task, agent_name="researcher", error="upstream down")
        assert manager.review_mission(task, failed) is MissionOutcome.RETRY

    def test_the_brain_is_unchanged(self):
        manager = manager_with(EphemeralMemory())
        assert isinstance(manager.brain, NullBrain)
        assert manager.deliberate(a_task()).action is BrainAction.UNKNOWN

    def test_the_brain_default_did_not_become_memory_dependent(self):
        assert not ManagerAgent().can_reason
        assert ManagerAgent(memory=EphemeralMemory()).can_reason is False

    def test_delegation_still_reaches_the_agent(self):
        agent = StubAgent()
        manager = manager_with(EphemeralMemory(), agents=(agent,))
        manager.handle_task(a_task())
        assert len(agent.executions) == 1

    def test_every_collaborator_is_still_injectable(self):
        import inspect

        parameters = set(inspect.signature(ManagerAgent.__init__).parameters)
        assert parameters == {
            "self",
            "registry",
            "router",
            "assessor",
            "own_skills",
            "brain",
            "memory",
            "memory_policy",
            "calendar",
        }


# ----------------------------------------------------------------------
# 7, 8. No I/O, no vendor coupling
# ----------------------------------------------------------------------


class TestNoStorageOrVendorCoupling:
    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        manager.handle_task(a_task())
        manager.memory.remember(MemoryRecord(content="something worth keeping"))
        assert list(tmp_path.iterdir()) == []

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        manager = manager_with(EphemeralMemory(), agents=(StubAgent(),))
        assert manager.handle_task(a_task()).status is TaskStatus.COMPLETED

    def test_no_database_driver_reaches_the_manager(self):
        import src.manager.manager as module

        code = code_of(module)
        for driver in ("sqlite", "redis", "psycopg", "pymongo", "sqlalchemy", "open("):
            assert driver not in code, driver

    def test_the_manager_still_names_no_vendor_or_specialist(self):
        """The existing structural guards must survive this wiring."""
        import src.manager.manager as module

        source = open(module.__file__, encoding="utf-8").read().lower()
        for word in ("anthropic", "gmail", "provider", "email", "mailbox"):
            assert word not in source, word

    def test_the_manager_imports_no_specialist_module(self):
        import src.manager.manager as module

        code = code_of(module)
        assert "from src.agents.email" not in code
        assert "from src.services.email" not in code
        assert "from src.providers" not in code

    def test_the_memory_import_is_the_abstraction(self):
        import src.manager.manager as module

        code = code_of(module)
        assert "from src.services.memory.memory import memory" in code

    def test_no_anthropic_sdk_is_imported(self):
        import sys

        manager_with(EphemeralMemory(), agents=(StubAgent(),)).handle_task(a_task())
        assert "anthropic" not in sys.modules


# ----------------------------------------------------------------------
# The memory contract itself was not modified
# ----------------------------------------------------------------------


class TestTheMemoryContractWasNotChanged:
    def test_the_contract_still_declares_four_operations(self):
        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }

    def test_the_contract_still_knows_nothing_of_the_manager(self):
        import src.services.memory.ephemeral as ephemeral
        import src.services.memory.memory as contract

        for module in (contract, ephemeral):
            assert "from src.manager" not in code_of(module)

    def test_a_record_is_still_frozen(self):
        item = MemoryRecord(content="x")
        with pytest.raises(Exception):
            item.content = "y"
