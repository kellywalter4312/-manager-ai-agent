"""Tests for the Manager's DATA loop.

These are the tests that matter most for Milestone 1: they prove a task can
travel Diagnose -> Assemble -> Take Action -> Assess and come back with a
trustworthy result.
"""

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.echo_agent import EchoAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.manager import ManagerAgent
from src.registry.agent_registry import AgentRegistry


@pytest.fixture
def manager():
    """A Manager with two stand-in agents that have distinct capabilities."""
    m = ManagerAgent()
    m.register_agent(EchoAgent(name="alpha_agent", capabilities={"email", "inbox"}))
    m.register_agent(EchoAgent(name="beta_agent", capabilities={"calendar"}))
    return m


# --- D: Diagnose -----------------------------------------------------------

def test_diagnose_picks_the_agent_whose_capability_matches(manager):
    assert manager.diagnose(Task(description="send an email")).agent_name == "alpha_agent"
    assert manager.diagnose(Task(description="check my calendar")).agent_name == "beta_agent"


def test_diagnose_returns_none_when_nothing_matches(manager):
    assert manager.diagnose(Task(description="book a flight")) is None


def test_diagnose_prefers_the_strongest_match(manager):
    # "email" and "inbox" both belong to alpha_agent -> score 2.
    decision = manager.diagnose(Task(description="email me my inbox"))
    assert decision.agent_name == "alpha_agent"
    assert decision.score == 2


def test_diagnose_is_case_insensitive(manager):
    assert manager.diagnose(Task(description="SEND AN EMAIL")).agent_name == "alpha_agent"


def test_diagnose_needs_no_manager_changes_for_a_new_agent(manager):
    """The key extensibility property: registering is enough."""
    manager.register_agent(EchoAgent(name="gamma_agent", capabilities={"weather"}))
    assert manager.diagnose(Task(description="what is the weather")).agent_name == "gamma_agent"


# --- A: Assemble -----------------------------------------------------------

def test_assemble_returns_the_registered_instance(manager):
    assert manager.assemble("alpha_agent") is manager.registry.get("alpha_agent")


def test_manager_uses_the_injected_registry():
    registry = AgentRegistry()
    registry.register(EchoAgent(name="alpha_agent", capabilities={"email"}))
    assert ManagerAgent(registry=registry).assemble("alpha_agent") is not None


# --- T: Take Action --------------------------------------------------------

def test_full_loop_delegates_and_returns_the_agents_result(manager):
    task = Task(description="send an email to my team")
    result = manager.handle_task(task)

    assert result.status is TaskStatus.COMPLETED
    assert result.agent_name == "alpha_agent"
    assert result.task_id == task.id
    assert task.description in result.output


def test_unroutable_task_reports_no_agent_found(manager):
    result = manager.handle_task(Task(description="book a flight"))

    assert result.status is TaskStatus.NO_AGENT_FOUND
    assert result.agent_name is None
    assert result.error


def test_a_crashing_agent_does_not_crash_the_manager():
    class ExplodingAgent(BaseAgent):
        @property
        def spec(self):
            return AgentSpec(name="boom_agent", description="fails", capabilities={"explode"})

        def execute(self, task):
            raise RuntimeError("kaboom")

    manager = ManagerAgent()
    manager.register_agent(ExplodingAgent())
    result = manager.handle_task(Task(description="please explode"))

    assert result.status is TaskStatus.FAILED
    assert result.agent_name == "boom_agent"
    assert "kaboom" in result.error


# --- A: Assess -------------------------------------------------------------

def test_assess_rejects_a_result_of_the_wrong_type():
    class BadTypeAgent(BaseAgent):
        @property
        def spec(self):
            return AgentSpec(name="bad_agent", description="d", capabilities={"misbehave"})

        def execute(self, task):
            return {"status": "completed"}  # a dict, not a TaskResult

    manager = ManagerAgent()
    manager.register_agent(BadTypeAgent())
    result = manager.handle_task(Task(description="please misbehave"))

    assert result.status is TaskStatus.REJECTED
    assert "expected TaskResult" in result.error


def test_assess_rejects_a_result_for_a_different_task():
    class WrongTaskAgent(BaseAgent):
        @property
        def spec(self):
            return AgentSpec(name="wrong_agent", description="d", capabilities={"misbehave"})

        def execute(self, task):
            return TaskResult.success(Task(description="a different task"), self.name, "done")

    manager = ManagerAgent()
    manager.register_agent(WrongTaskAgent())
    result = manager.handle_task(Task(description="please misbehave"))

    assert result.status is TaskStatus.REJECTED
    assert "expected" in result.error


def test_assess_rejects_success_with_no_output():
    class EmptyAgent(BaseAgent):
        @property
        def spec(self):
            return AgentSpec(name="empty_agent", description="d", capabilities={"misbehave"})

        def execute(self, task):
            return TaskResult.success(task, self.name, output="")

    manager = ManagerAgent()
    manager.register_agent(EmptyAgent())
    result = manager.handle_task(Task(description="please misbehave"))

    assert result.status is TaskStatus.REJECTED
