"""Tests for the action layer: BrainDecision -> validated execution.

The claims that carry the weight:

    TestRouterAuthorityIsPreserved - a model's preference cannot become policy
    TestNothingElseExecutes        - REPORT/ESCALATE/UNKNOWN run no work
    TestItIsNotASecondBrain        - no model, no memory, no judgement

No Anthropic, no Gmail, no network, no files.
"""

import socket
import sys

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.actions import ActionExecutor, ActionResult, ActionStatus, _HANDLERS
from src.manager.assessor import ContractAssessor
from src.manager.brain import BrainAction, BrainDecision
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter, Router
from src.registry.agent_registry import AgentRegistry
from src.services.memory.ephemeral import EphemeralMemory


def code_of(module) -> str:
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


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------


class SpyAgent(BaseAgent):
    def __init__(self, name="research_agent", capabilities=("research",), result=None):
        self._name = name
        self._capabilities = tuple(capabilities)
        self._result = result
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
        if callable(self._result):
            return self._result(task)
        return TaskResult.success(task, agent_name=self._name, output="done")


class ManagerSkills(BaseAgent):
    def __init__(self):
        self.executions = []

    @property
    def name(self):
        return "manager"

    @property
    def spec(self):
        return AgentSpec(
            name="manager",
            description="the manager's own coordination skills",
            capabilities=("coordinate", "summarise"),
        )

    def execute(self, task):
        self.executions.append(task)
        return TaskResult.success(task, agent_name="manager", output="coordinated")


def a_task(description="research something"):
    return Task(description=description)


def decision(action=BrainAction.DELEGATE, reason="a specialist fits", **fields):
    fields.setdefault("delegate_to", "research_agent" if action is BrainAction.DELEGATE else None)
    return BrainDecision(action=action, reason=reason, **fields)


def manager_with(agents=(), **kwargs):
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return ManagerAgent(registry=registry, **kwargs)


def executor_for(agents=(), **kwargs):
    manager = manager_with(agents, **kwargs)
    return ActionExecutor(manager), manager


# ----------------------------------------------------------------------
# 1. Every action is handled
# ----------------------------------------------------------------------


class TestEveryActionIsHandled:
    def test_every_brain_action_has_a_handler(self):
        assert set(_HANDLERS) == set(BrainAction)

    @pytest.mark.parametrize("action", list(BrainAction))
    def test_every_action_produces_a_result(self, action):
        agent = SpyAgent()
        executor, _ = executor_for((agent,), own_skills=ManagerSkills())
        outcome = executor.execute(decision(action=action), a_task())
        assert isinstance(outcome, ActionResult)
        assert outcome.action is action

    @pytest.mark.parametrize("action", list(BrainAction))
    def test_every_result_explains_itself(self, action):
        executor, _ = executor_for((SpyAgent(),), own_skills=ManagerSkills())
        assert executor.execute(decision(action=action), a_task()).reason.strip()

    def test_an_action_result_is_frozen(self):
        outcome = ActionResult(
            action=BrainAction.REPORT, status=ActionStatus.NO_ACTION, reason="x"
        )
        with pytest.raises(Exception):
            outcome.status = ActionStatus.EXECUTED

    def test_a_result_cannot_accompany_a_non_execution(self):
        """A refusal carrying an outcome would let a caller read work into a
        non-event."""
        task = a_task()
        with pytest.raises(ValueError):
            ActionResult(
                action=BrainAction.DELEGATE,
                status=ActionStatus.REFUSED,
                reason="refused",
                result=TaskResult.success(task, "research_agent", "done"),
            )


# ----------------------------------------------------------------------
# 2-4. Non-executing actions
# ----------------------------------------------------------------------


class TestNothingElseExecutes:
    def _executor(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        return executor, agent

    def test_unknown_executes_nothing(self):
        executor, agent = self._executor()
        outcome = executor.execute(decision(action=BrainAction.UNKNOWN), a_task())
        assert outcome.status is ActionStatus.NO_ACTION
        assert not outcome.executed
        assert agent.executions == []

    def test_unknown_explains_why_nothing_happened(self):
        executor, _ = self._executor()
        outcome = executor.execute(
            BrainDecision(action=BrainAction.UNKNOWN, reason="not enough context"),
            a_task(),
        )
        assert "not enough context" in outcome.reason

    def test_report_executes_nothing(self):
        executor, agent = self._executor()
        outcome = executor.execute(decision(action=BrainAction.REPORT), a_task())
        assert outcome.status is ActionStatus.NO_ACTION
        assert agent.executions == []

    def test_report_does_not_pretend_work_happened(self):
        executor, _ = self._executor()
        outcome = executor.execute(decision(action=BrainAction.REPORT), a_task())
        assert outcome.result is None
        assert not outcome.executed

    def test_report_carries_the_recommendation(self):
        executor, _ = self._executor()
        outcome = executor.execute(
            BrainDecision(action=BrainAction.REPORT, reason="three roles look promising"),
            a_task(),
        )
        assert outcome.reason == "three roles look promising"

    def test_escalate_executes_nothing(self):
        executor, agent = self._executor()
        outcome = executor.execute(decision(action=BrainAction.ESCALATE), a_task())
        assert outcome.status is ActionStatus.ESCALATED
        assert agent.executions == []
        assert outcome.result is None

    def test_escalate_invents_no_external_service(self):
        """There is no notifier, queue or mailer to call, and none is faked."""
        import src.manager.actions as module

        code = code_of(module)
        for invented in ("notify", "smtp", "webhook", "requests", "send("):
            assert invented not in code, invented


# ----------------------------------------------------------------------
# 5. HANDLE_SELF
# ----------------------------------------------------------------------


class TestHandleSelf:
    def test_it_is_unsupported_without_own_skills(self):
        executor, _ = executor_for((SpyAgent(),))
        outcome = executor.execute(decision(action=BrainAction.HANDLE_SELF), a_task())
        assert outcome.status is ActionStatus.UNSUPPORTED
        assert not outcome.executed

    def test_it_says_plainly_that_it_cannot(self):
        executor, _ = executor_for(())
        outcome = executor.execute(decision(action=BrainAction.HANDLE_SELF), a_task())
        assert "no skills of its own" in outcome.reason

    def test_it_invents_no_capability(self):
        executor, _ = executor_for(())
        outcome = executor.execute(decision(action=BrainAction.HANDLE_SELF), a_task())
        assert outcome.result is None

    def test_it_runs_the_managers_own_skills_when_they_fit(self):
        skills = ManagerSkills()
        executor, _ = executor_for((), own_skills=skills)
        outcome = executor.execute(
            decision(action=BrainAction.HANDLE_SELF), a_task("summarise the roster")
        )
        assert outcome.status is ActionStatus.EXECUTED
        assert len(skills.executions) == 1
        assert outcome.agent_name == "manager"

    def test_it_still_requires_the_router_to_agree(self):
        """The Manager wins its own work only when best suited, as always."""
        skills = ManagerSkills()
        executor, _ = executor_for((SpyAgent(),), own_skills=skills)
        outcome = executor.execute(
            decision(action=BrainAction.HANDLE_SELF), a_task("research something")
        )
        assert outcome.status is ActionStatus.REFUSED
        assert skills.executions == []

    def test_the_reported_action_stays_handle_self(self):
        skills = ManagerSkills()
        executor, _ = executor_for((), own_skills=skills)
        outcome = executor.execute(
            decision(action=BrainAction.HANDLE_SELF), a_task("summarise the roster")
        )
        assert outcome.action is BrainAction.HANDLE_SELF


# ----------------------------------------------------------------------
# 6-7, 9. DELEGATE validation
# ----------------------------------------------------------------------


class TestDelegateValidation:
    def test_a_registered_agent_the_router_agrees_on_runs(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(), a_task())

        assert outcome.status is ActionStatus.EXECUTED
        assert outcome.agent_name == "research_agent"
        assert len(agent.executions) == 1

    def test_an_unregistered_target_is_refused(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(delegate_to="ghost_agent"), a_task())

        assert outcome.status is ActionStatus.REFUSED
        assert "ghost_agent" in outcome.reason
        assert agent.executions == []

    def test_the_refusal_lists_who_is_registered(self):
        executor, _ = executor_for((SpyAgent(),))
        outcome = executor.execute(decision(delegate_to="ghost_agent"), a_task())
        assert "research_agent" in outcome.reason

    def test_an_agent_unregistered_since_the_decision_is_refused(self):
        """Live state, not the state when the decision was formed."""
        agent = SpyAgent()
        executor, manager = executor_for((agent,))
        proposed = decision()

        manager.registry._agents.clear()      # the world moved on

        outcome = executor.execute(proposed, a_task())
        assert outcome.status is ActionStatus.REFUSED
        assert agent.executions == []

    def test_nothing_runs_when_no_agent_is_registered(self):
        executor, _ = executor_for(())
        outcome = executor.execute(decision(), a_task())
        assert outcome.status is ActionStatus.REFUSED
        assert "none" in outcome.reason

    def test_an_unroutable_task_is_refused(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(), a_task("something nobody can do"))

        assert outcome.status is ActionStatus.REFUSED
        assert "no agent capable" in outcome.reason
        assert agent.executions == []


# ----------------------------------------------------------------------
# THE KEY CLAIM: Router authority is preserved
# ----------------------------------------------------------------------


class TestRouterAuthorityIsPreserved:
    """A model's preference must not quietly become the routing policy."""

    def _two_agents(self):
        return (
            SpyAgent(name="research_agent", capabilities=("research",)),
            SpyAgent(name="writing_agent", capabilities=("write",)),
        )

    def test_a_disagreement_executes_nothing(self):
        research, writing = self._two_agents()
        executor, _ = executor_for((research, writing))

        outcome = executor.execute(
            decision(delegate_to="writing_agent"), a_task("research something")
        )

        assert outcome.status is ActionStatus.REFUSED
        assert research.executions == []
        assert writing.executions == []

    def test_the_disagreement_is_reported_not_resolved(self):
        research, writing = self._two_agents()
        executor, _ = executor_for((research, writing))

        outcome = executor.execute(
            decision(delegate_to="writing_agent"), a_task("research something")
        )
        assert "proposed 'writing_agent'" in outcome.reason
        assert "selected 'research_agent'" in outcome.reason

    def test_a_confident_decision_cannot_override_the_router(self):
        research, writing = self._two_agents()
        executor, _ = executor_for((research, writing))

        outcome = executor.execute(
            BrainDecision(
                action=BrainAction.DELEGATE,
                reason="I am certain writing_agent should do this",
                confidence=1.0,
                delegate_to="writing_agent",
            ),
            a_task("research something"),
        )
        assert outcome.status is ActionStatus.REFUSED
        assert writing.executions == []

    def test_the_router_is_consulted_exactly_once(self):
        """With an LLM router each call is a request; two would double the cost."""
        calls = []

        class CountingRouter(CapabilityRouter):
            def route(self, task, agents):
                calls.append(task)
                return super().route(task, agents)

        executor, _ = executor_for((SpyAgent(),), router=CountingRouter())
        executor.execute(decision(), a_task())
        assert len(calls) == 1

    def test_the_router_receives_agents_not_a_decision(self):
        seen = {}

        class RecordingRouter(CapabilityRouter):
            def route(self, task, agents):
                seen["args"] = (task, tuple(agents))
                return super().route(task, agents)

        executor, _ = executor_for((SpyAgent(),), router=RecordingRouter())
        executor.execute(decision(), a_task())

        task, agents = seen["args"]
        assert isinstance(task, Task)
        assert all(isinstance(a, BaseAgent) for a in agents)

    def test_a_custom_router_still_decides(self):
        """Whatever the Router says is what happens - or nothing does."""
        research, writing = self._two_agents()

        class AlwaysWriting(Router):
            def route(self, task, agents):
                from src.core.contracts import RoutingDecision

                return RoutingDecision(agent_name="writing_agent", score=1, reason="always")

        executor, _ = executor_for((research, writing), router=AlwaysWriting())
        outcome = executor.execute(decision(delegate_to="writing_agent"), a_task())

        assert outcome.status is ActionStatus.EXECUTED
        assert len(writing.executions) == 1
        assert research.executions == []


# ----------------------------------------------------------------------
# 8. Malformed input
# ----------------------------------------------------------------------


class TestMalformedInputCannotBypassValidation:
    def test_a_non_decision_is_refused(self):
        executor, _ = executor_for((SpyAgent(),))
        for bad in ("delegate to research_agent", 42, None, {"action": "delegate"}):
            outcome = executor.execute(bad, a_task())
            assert outcome.status is ActionStatus.REFUSED
            assert not outcome.executed

    def test_a_non_decision_executes_nothing(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        executor.execute({"action": "delegate", "delegate_to": "research_agent"}, a_task())
        assert agent.executions == []

    def test_a_non_task_is_refused(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(), "research something")
        assert outcome.status is ActionStatus.REFUSED
        assert agent.executions == []

    def test_a_decision_object_lookalike_is_refused(self):
        """Duck typing must not be enough to get work executed."""

        class FakeDecision:
            action = BrainAction.DELEGATE
            reason = "looks real"
            delegate_to = "research_agent"
            confidence = 1.0
            source = "fake"

        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        outcome = executor.execute(FakeDecision(), a_task())

        assert outcome.status is ActionStatus.REFUSED
        assert agent.executions == []

    def test_the_brain_decision_contract_still_refuses_bad_input(self):
        from src.manager.brain import BrainError

        with pytest.raises(BrainError):
            BrainDecision(action=BrainAction.DELEGATE, reason="names nobody")


# ----------------------------------------------------------------------
# 15-16. Structured results and failure isolation
# ----------------------------------------------------------------------


class TestResultsAndFailureIsolation:
    def test_a_successful_delegation_carries_the_task_result(self):
        executor, _ = executor_for((SpyAgent(),))
        outcome = executor.execute(decision(), a_task())

        assert outcome.executed
        assert isinstance(outcome.result, TaskResult)
        assert outcome.result.status is TaskStatus.COMPLETED
        assert outcome.result.output == "done"

    def test_a_specialist_failure_is_represented_not_raised(self):
        agent = SpyAgent(
            result=lambda task: TaskResult.failure(task, "research_agent", "upstream down")
        )
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(), a_task())

        # The action ran; the work failed. Those are different facts.
        assert outcome.status is ActionStatus.EXECUTED
        assert outcome.result.status is TaskStatus.FAILED
        assert "upstream down" in outcome.result.error

    def test_a_crashing_specialist_is_contained(self):
        agent = SpyAgent(
            result=lambda task: (_ for _ in ()).throw(RuntimeError("agent crashed"))
        )
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(), a_task())

        assert outcome.status is ActionStatus.EXECUTED
        assert outcome.result.status is TaskStatus.FAILED
        assert "agent crashed" in outcome.result.error

    def test_a_malformed_specialist_result_is_still_assessed(self):
        agent = SpyAgent(result=lambda task: {"not": "a result"})
        executor, _ = executor_for((agent,))
        outcome = executor.execute(decision(), a_task())
        assert outcome.result.status is TaskStatus.REJECTED

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        agent = SpyAgent(result=lambda task: (_ for _ in ()).throw(KeyboardInterrupt()))
        executor, _ = executor_for((agent,))
        with pytest.raises(KeyboardInterrupt):
            executor.execute(decision(), a_task())

    def test_a_system_exit_is_not_swallowed(self):
        agent = SpyAgent(result=lambda task: (_ for _ in ()).throw(SystemExit(1)))
        executor, _ = executor_for((agent,))
        with pytest.raises(SystemExit):
            executor.execute(decision(), a_task())


# ----------------------------------------------------------------------
# 10-11, 19. It is not a second brain
# ----------------------------------------------------------------------


class TestItIsNotASecondBrain:
    def test_it_calls_no_model(self):
        import src.manager.actions as module

        code = code_of(module)
        for word in ("provider", "complete(", "anthropic", "llm", "deliberate("):
            assert word not in code, word

    def test_it_imports_no_provider(self):
        import src.manager.actions as module

        code = code_of(module)
        assert "from src.providers" not in code

    def test_it_never_consults_the_brain(self):
        class ExplodingBrain:
            name = "exploding"

            def deliberate(self, task, agents=(), notes=()):
                raise AssertionError("the action layer consulted a brain")

        executor, _ = executor_for((SpyAgent(),), brain=None)
        executor._manager.brain = ExplodingBrain()
        assert executor.execute(decision(), a_task()).executed

    def test_it_touches_no_memory(self):
        import ast

        import src.manager.actions as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("remember", "recall", "forget", "clear")

    def test_it_holds_no_reference_to_memory(self):
        import src.manager.actions as module

        code = code_of(module)
        assert ".memory" not in code
        assert "from src.services.memory" not in code

    def test_manager_memory_is_untouched_by_an_action(self):
        agent = SpyAgent()
        executor, manager = executor_for((agent,))
        before = len(manager.memory)
        executor.execute(decision(), a_task())
        assert len(manager.memory) == before

    def test_specialist_memory_is_untouched_by_an_action(self):
        from src.agents.cognitive_agent import CognitiveAgent

        class CognitiveSpy(CognitiveAgent):
            def __init__(self):
                super().__init__(memory=EphemeralMemory())
                self.executions = []

            @property
            def spec(self):
                return AgentSpec(
                    name="research_agent",
                    description="researches",
                    capabilities=("research",),
                )

            def execute(self, task):
                self.executions.append(task)
                return TaskResult.success(task, "research_agent", "done")

        agent = CognitiveSpy()
        executor, _ = executor_for((agent,))
        executor.execute(decision(), a_task())
        assert len(agent.memory) == 0

    def test_it_holds_no_state_between_calls(self):
        executor, _ = executor_for((SpyAgent(),))
        before = dict(vars(executor))
        for _ in range(5):
            executor.execute(decision(), a_task())
        assert vars(executor) == before

    def test_it_makes_no_judgement_of_its_own(self):
        """It never rewrites one action as another."""
        executor, _ = executor_for((SpyAgent(),), own_skills=ManagerSkills())
        for action in BrainAction:
            outcome = executor.execute(decision(action=action), a_task())
            assert outcome.action is action


# ----------------------------------------------------------------------
# 12-14. Nothing existing changed
# ----------------------------------------------------------------------


class TestExistingBehaviourIsUnchanged:
    def test_routing_is_unchanged(self):
        executor, manager = executor_for((SpyAgent(),))
        assert isinstance(manager.router, CapabilityRouter)
        assert manager.diagnose(a_task()).agent_name == "research_agent"

    def test_assessment_is_unchanged(self):
        _, manager = executor_for((SpyAgent(),))
        assert isinstance(manager.assessor, ContractAssessor)

    def test_the_assessor_sees_every_executed_action(self):
        seen = []

        class RecordingAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                seen.append((task, agent_name))
                return super().assess(task, agent_name, result)

        executor, _ = executor_for((SpyAgent(),), assessor=RecordingAssessor())
        executor.execute(decision(), a_task())
        assert len(seen) == 1 and seen[0][1] == "research_agent"

    def test_the_assessor_is_not_called_when_nothing_ran(self):
        seen = []

        class RecordingAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                seen.append(task)
                return super().assess(task, agent_name, result)

        executor, _ = executor_for((SpyAgent(),), assessor=RecordingAssessor())
        executor.execute(decision(action=BrainAction.REPORT), a_task())
        executor.execute(decision(delegate_to="ghost_agent"), a_task())
        assert seen == []

    def test_handle_task_still_works_untouched(self):
        agent = SpyAgent()
        _, manager = executor_for((agent,))
        result = manager.handle_task(a_task())
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "research_agent"

    def test_the_manager_module_does_not_import_the_action_layer(self):
        """The action layer is invoked explicitly; handle_task stays mechanical."""
        import src.manager.manager as module

        code = code_of(module)
        assert "actions" not in code
        assert "actionexecutor" not in code

    def test_specialist_execution_is_unchanged(self):
        agent = SpyAgent()
        executor, _ = executor_for((agent,))
        task = a_task()
        executor.execute(decision(), task)
        assert agent.executions == [task]      # the same Task object, unmodified

    def test_the_brain_contract_is_unchanged(self):
        import inspect

        from src.manager.brain import ManagerBrain

        assert set(ManagerBrain.__abstractmethods__) == {"name", "deliberate"}
        params = set(inspect.signature(ManagerBrain.deliberate).parameters)
        assert params == {"self", "task", "agents", "notes"}

    def test_the_brain_decision_contract_is_unchanged(self):
        assert set(BrainDecision.__dataclass_fields__) == {
            "action",
            "reason",
            "confidence",
            "delegate_to",
            "steps",
            "source",
        }

    def test_the_memory_contract_is_unchanged(self):
        from src.services.memory.memory import Memory

        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }


# ----------------------------------------------------------------------
# 17-18, 20. Inert and deterministic
# ----------------------------------------------------------------------


class TestInertAndDeterministic:
    def test_the_same_decision_and_context_give_the_same_result(self):
        task = a_task()
        proposed = decision()

        one, _ = executor_for((SpyAgent(),))
        two, _ = executor_for((SpyAgent(),))

        first = one.execute(proposed, task)
        second = two.execute(proposed, task)

        assert (first.action, first.status, first.reason, first.agent_name) == (
            second.action,
            second.status,
            second.reason,
            second.agent_name,
        )

    def test_repeated_refusals_are_identical(self):
        executor, _ = executor_for((SpyAgent(),))
        task, proposed = a_task(), decision(delegate_to="ghost_agent")
        assert executor.execute(proposed, task).reason == executor.execute(
            proposed, task
        ).reason

    def test_no_anthropic_sdk_is_imported(self):
        executor, _ = executor_for((SpyAgent(),))
        executor.execute(decision(), a_task())
        assert "anthropic" not in sys.modules

    def test_no_client_is_created(self):
        import src.manager.actions as module

        code = code_of(module)
        assert "client" not in code

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        executor, _ = executor_for((SpyAgent(),))
        assert executor.execute(decision(), a_task()).executed

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        executor, _ = executor_for((SpyAgent(),), own_skills=ManagerSkills())
        for action in BrainAction:
            executor.execute(decision(action=action), a_task())
        assert list(tmp_path.iterdir()) == []

    def test_it_reaches_no_mail_or_gmail(self):
        import src.manager.actions as module

        code = code_of(module)
        for word in ("gmail", "mailbox", "email", "smtp"):
            assert word not in code, word
