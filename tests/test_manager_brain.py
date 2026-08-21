"""Tests for the Manager's reasoning layer.

Two things are being proved here, and the second matters more than the first:

  1. A brain can be injected, consulted, and its advice reaches the caller.
  2. Adding a brain changed *nothing* about how the Manager already worked.

No Anthropic, no network, no key. Every brain in this file is a fake.
"""

import inspect
import socket

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.brain import (
    BrainAction,
    BrainDecision,
    BrainError,
    ManagerBrain,
    NullBrain,
)
from src.manager.manager import ManagerAgent, MissionOutcome
from src.registry.agent_registry import AgentRegistry


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class StubAgent(BaseAgent):
    """A minimal specialist, so the roster is not empty."""

    def __init__(self, name="researcher", capabilities=("research", "summarise")):
        self._name = name
        self._capabilities = tuple(capabilities)

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
        return TaskResult.success(task, agent_name=self._name, output="done")


class RecordingBrain(ManagerBrain):
    """Returns a fixed decision and remembers what it was asked."""

    def __init__(self, decision=None):
        self.decision = decision or BrainDecision(
            action=BrainAction.HANDLE_SELF,
            reason="this is a routine request",
            confidence=0.8,
            source="recording",
        )
        self.calls = []

    @property
    def name(self):
        return "recording"

    def deliberate(self, task, agents=(), notes=()):
        self.calls.append((task, tuple(agents), tuple(notes)))
        return self.decision


class ExplodingBrain(ManagerBrain):
    @property
    def name(self):
        return "exploding"

    def deliberate(self, task, agents=(), notes=()):
        raise RuntimeError("kaboom")


class RogueBrain:
    """Not a ManagerBrain at all - a brain is injected, so anything may arrive."""

    name = "rogue"

    def deliberate(self, task, agents=(), notes=()):
        return "just do it"


def a_task(description="summarise the quarterly numbers"):
    return Task(id="t-1", description=description)


def manager_with(brain=None, agents=(StubAgent(),)):
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return ManagerAgent(registry=registry, brain=brain)


# ----------------------------------------------------------------------
# 1. The contract
# ----------------------------------------------------------------------


class TestTheDecisionContract:
    def test_a_decision_is_frozen(self):
        decision = BrainDecision(action=BrainAction.REPORT, reason="worth telling you")
        with pytest.raises(Exception):
            decision.action = BrainAction.ESCALATE

    def test_a_decision_must_explain_itself(self):
        """Advice nobody can evaluate is not advice."""
        with pytest.raises(BrainError):
            BrainDecision(action=BrainAction.REPORT, reason="   ")

    def test_delegation_must_name_someone(self):
        with pytest.raises(BrainError):
            BrainDecision(action=BrainAction.DELEGATE, reason="a specialist is better")

    def test_delegation_with_a_target_is_valid(self):
        decision = BrainDecision(
            action=BrainAction.DELEGATE, reason="research work", delegate_to="researcher"
        )
        assert decision.delegate_to == "researcher"

    def test_confidence_outside_zero_to_one_is_rejected(self):
        for bad in (-0.1, 1.5, 42):
            with pytest.raises(BrainError):
                BrainDecision(action=BrainAction.REPORT, reason="x", confidence=bad)

    def test_an_unknown_action_is_rejected(self):
        with pytest.raises(BrainError):
            BrainDecision(action="panic", reason="x")

    def test_a_string_action_is_normalised(self):
        assert BrainDecision(action="report", reason="x").action is BrainAction.REPORT

    def test_no_view_is_a_valid_decision(self):
        decision = BrainDecision.no_view(reason="not enough context")
        assert decision.action is BrainAction.UNKNOWN
        assert not decision.is_decided

    def test_steps_are_cleaned_and_deduplicated(self):
        decision = BrainDecision(
            action=BrainAction.REPORT,
            reason="x",
            steps=("  read  the mail ", "", "read the mail", "reply"),
        )
        assert decision.steps == ("read the mail", "reply")


# ----------------------------------------------------------------------
# 2. The interface stays narrow
# ----------------------------------------------------------------------


class TestTheInterfaceStaysNarrow:
    def test_a_brain_has_exactly_one_abstract_method(self):
        """The guard against this becoming a second ManagerAgent."""
        abstract = set(ManagerBrain.__abstractmethods__)
        assert abstract == {"name", "deliberate"}

    def test_a_brain_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            ManagerBrain()

    def test_a_brain_does_not_execute_delegate_or_report(self):
        forbidden = ("execute", "delegate", "handle_task", "register", "route", "assess")
        public = {n for n in dir(ManagerBrain) if not n.startswith("_")}
        assert public.isdisjoint(forbidden)

    def test_the_brain_module_holds_no_state(self):
        """Memory is a separate component with a separate lifecycle."""
        import src.manager.brain as brain_module

        source = open(brain_module.__file__, encoding="utf-8").read().lower()
        for word in ("history", "remember", "store", "persist", "sqlite", "json.dump"):
            assert word not in source, word


# ----------------------------------------------------------------------
# 3. NullBrain
# ----------------------------------------------------------------------


class TestNullBrain:
    def test_it_satisfies_the_contract(self):
        assert isinstance(NullBrain(), ManagerBrain)

    def test_it_reports_no_view_honestly(self):
        decision = NullBrain().deliberate(a_task())
        assert decision.action is BrainAction.UNKNOWN
        assert not decision.is_decided
        assert "no reasoning capability" in decision.reason

    def test_it_never_invents_a_plan(self):
        decision = NullBrain().deliberate(a_task())
        assert decision.steps == () and decision.confidence == 0.0

    def test_it_is_the_default(self):
        assert isinstance(ManagerAgent().brain, NullBrain)

    def test_a_manager_without_a_brain_says_so(self):
        assert not ManagerAgent().can_reason
        assert manager_with(RecordingBrain()).can_reason


# ----------------------------------------------------------------------
# 4. The Manager depends on the abstraction
# ----------------------------------------------------------------------


class TestTheManagerConsultsItsBrain:
    def test_an_injected_brain_is_used(self):
        brain = RecordingBrain()
        decision = manager_with(brain).deliberate(a_task())
        assert decision is brain.decision
        assert len(brain.calls) == 1

    def test_the_task_is_passed_through(self):
        brain = RecordingBrain()
        task = a_task("find me freelance work")
        manager_with(brain).deliberate(task)
        assert brain.calls[0][0] is task

    def test_the_roster_is_supplied_as_specs(self):
        """So a brain can weigh delegation without touching the registry."""
        brain = RecordingBrain()
        manager_with(brain, agents=(StubAgent("researcher"), StubAgent("writer"))).deliberate(
            a_task()
        )
        agents = brain.calls[0][1]
        assert {a.name for a in agents} == {"researcher", "writer"}
        assert all(isinstance(a, AgentSpec) for a in agents)

    def test_notes_reach_the_brain(self):
        brain = RecordingBrain()
        manager_with(brain).deliberate(a_task(), notes=["the last attempt timed out"])
        assert brain.calls[0][2] == ("the last attempt timed out",)

    def test_the_manager_holds_the_abstraction_not_a_concrete_type(self):
        assert isinstance(manager_with(RecordingBrain()).brain, ManagerBrain)


# ----------------------------------------------------------------------
# 5. Fault isolation
# ----------------------------------------------------------------------


class TestAFailingBrainIsContained:
    def test_an_exception_becomes_no_view(self):
        decision = manager_with(ExplodingBrain()).deliberate(a_task())
        assert decision.action is BrainAction.UNKNOWN
        assert "kaboom" in decision.reason

    def test_the_failure_names_the_brain(self):
        decision = manager_with(ExplodingBrain()).deliberate(a_task())
        assert decision.source == "exploding"

    def test_a_non_decision_is_rejected_not_coerced(self):
        decision = manager_with(RogueBrain()).deliberate(a_task())
        assert decision.action is BrainAction.UNKNOWN
        assert "str" in decision.reason

    def test_a_broken_brain_does_not_stop_the_data_loop(self):
        manager = manager_with(ExplodingBrain())
        result = manager.handle_task(Task(id="t-2", description="research something"))
        assert result.status is TaskStatus.COMPLETED


# ----------------------------------------------------------------------
# 6. Nothing that already worked has changed
# ----------------------------------------------------------------------


class TestTheDataLoopIsUntouched:
    def test_the_brain_is_not_consulted_during_handle_task(self):
        """Routing and assessment must not depend on anything probabilistic."""
        brain = RecordingBrain()
        manager = manager_with(brain)
        manager.handle_task(Task(id="t-3", description="research something"))
        assert brain.calls == []

    def test_results_are_identical_with_and_without_a_brain(self):
        task = Task(id="t-4", description="research something")
        without = manager_with(None).handle_task(task)
        with_brain = manager_with(RecordingBrain()).handle_task(task)
        assert without.status == with_brain.status
        assert without.agent_name == with_brain.agent_name
        assert without.output == with_brain.output

    def test_routing_still_fails_the_same_way(self):
        manager = manager_with(RecordingBrain(), agents=())
        result = manager.handle_task(Task(id="t-5", description="anything"))
        assert result.status is TaskStatus.NO_AGENT_FOUND

    def test_the_brain_cannot_override_a_routing_decision(self):
        """It advises DELEGATE elsewhere; the router is unmoved."""
        brain = RecordingBrain(
            BrainDecision(
                action=BrainAction.DELEGATE,
                reason="the writer should do this",
                delegate_to="writer",
                confidence=1.0,
            )
        )
        manager = manager_with(brain, agents=(StubAgent("researcher"),))
        result = manager.handle_task(Task(id="t-6", description="research something"))
        assert result.agent_name == "researcher"

    def test_the_brain_cannot_override_a_mission_verdict(self):
        brain = RecordingBrain(
            BrainDecision(action=BrainAction.REPORT, reason="looks great", confidence=1.0)
        )
        manager = manager_with(brain)
        task = Task(id="t-7", description="research something")
        failed = TaskResult.failure(task, agent_name="researcher", error="upstream down")
        assert manager.review_mission(task, failed) is MissionOutcome.RETRY


# ----------------------------------------------------------------------
# 7. No vendor coupling
# ----------------------------------------------------------------------


class TestNoVendorCoupling:
    def test_the_brain_module_names_no_vendor(self):
        import src.manager.brain as brain_module

        source = open(brain_module.__file__, encoding="utf-8").read().lower()
        for word in ("anthropic", "openai", "api_key", "http"):
            assert word not in source, word

    def test_the_brain_module_imports_only_contracts(self):
        import src.manager.brain as brain_module

        source = open(brain_module.__file__, encoding="utf-8").read()
        imports = [
            line.strip()
            for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        project = [line for line in imports if "src." in line]
        assert project == ["from src.core.contracts import AgentSpec, Task"]

    def test_no_sdk_is_imported_when_reasoning(self):
        import sys

        manager_with(RecordingBrain()).deliberate(a_task())
        assert "anthropic" not in sys.modules

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert manager_with(RecordingBrain()).deliberate(a_task())

    def test_the_manager_still_names_no_vendor(self):
        """The existing structural guard, restated for this layer."""
        import src.manager.manager as manager_module

        source = open(manager_module.__file__, encoding="utf-8").read().lower()
        assert "anthropic" not in source

    def test_the_brain_does_not_reach_into_specialist_domains(self):
        import src.manager.brain as brain_module

        source = open(brain_module.__file__, encoding="utf-8").read().lower()
        for word in ("gmail", "inbox", "opportunity", "smtp"):
            assert word not in source, word


# ----------------------------------------------------------------------
# 8. It does not duplicate Router or Assessor
# ----------------------------------------------------------------------


class TestItDoesNotDuplicateExistingDecisionMakers:
    def test_the_brain_does_not_import_the_router_or_assessor(self):
        import src.manager.brain as brain_module

        source = open(brain_module.__file__, encoding="utf-8").read()
        assert "from src.manager.router" not in source
        assert "from src.manager.assessor" not in source

    def test_it_returns_a_decision_not_a_routing_decision(self):
        from src.core.contracts import RoutingDecision

        decision = RecordingBrain().deliberate(a_task())
        assert not isinstance(decision, RoutingDecision)

    def test_it_returns_a_decision_not_a_task_result(self):
        decision = RecordingBrain().deliberate(a_task())
        assert not isinstance(decision, TaskResult)

    def test_deliberate_takes_no_result_to_assess(self):
        """Judging a finished result is the Assessor's job, not the brain's."""
        params = set(inspect.signature(ManagerBrain.deliberate).parameters)
        assert "result" not in params
        assert params == {"self", "task", "agents", "notes"}
