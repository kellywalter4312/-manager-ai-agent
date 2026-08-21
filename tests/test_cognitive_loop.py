"""Tests for the explicit cognitive execution path.

    Task -> Diagnose -> Recall -> Deliberate -> Action Layer -> Specialist
         -> Assessor -> Memory -> CognitiveOutcome

The claims that carry the weight:

    TestTheOrderIsCorrect        - recall really does precede deliberation
    TestTheRouterStaysAuthoritative - a brain's wrong pick executes nothing
    TestHandleTaskIsUnchanged    - the mechanical path is byte-for-byte itself

No Anthropic, no Gmail, no network, no files. Every brain here is scripted.
"""

import socket
import sys

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.cognitive_agent import CognitiveAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.actions import ActionExecutor, ActionResult, ActionStatus
from src.manager.assessor import ContractAssessor
from src.manager.brain import BrainAction, BrainDecision, ManagerBrain, NullBrain
from src.manager.cognitive_loop import CognitiveLoop, CognitiveOutcome
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter
from src.registry.agent_registry import AgentRegistry
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord


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


class ScriptedBrain(ManagerBrain):
    """Returns a fixed decision and records what it was given."""

    def __init__(self, decision=None, fail=None):
        self.decision = decision if decision is not None else BrainDecision(
            action=BrainAction.DELEGATE,
            reason="a specialist fits",
            confidence=0.8,
            delegate_to="research_agent",
            source="scripted",
        )
        self.fail = fail
        self.calls = []

    @property
    def name(self):
        return "scripted"

    def deliberate(self, task, agents=(), notes=()):
        self.calls.append({"task": task, "agents": tuple(agents), "notes": tuple(notes)})
        if self.fail is not None:
            raise self.fail
        return self.decision

    @property
    def last_notes(self):
        return self.calls[-1]["notes"]


def a_task(description="research something"):
    return Task(description=description)


def remembered(content, kind="observation"):
    return MemoryRecord(content=content, kind=kind)


def loop_with(brain=None, agents=(SpyAgent(),), memory=None, **kwargs):
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    manager = ManagerAgent(
        registry=registry,
        brain=brain if brain is not None else ScriptedBrain(),
        memory=memory if memory is not None else EphemeralMemory(),
        **kwargs,
    )
    return CognitiveLoop(manager), manager


# ----------------------------------------------------------------------
# 1. The brain is consulted
# ----------------------------------------------------------------------


class TestTheBrainIsConsulted:
    def test_the_loop_consults_the_brain(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        loop.run(a_task())
        assert len(brain.calls) == 1

    def test_the_task_reaches_the_brain(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        task = a_task()
        loop.run(task)
        assert brain.calls[0]["task"] is task

    def test_the_roster_reaches_the_brain(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        loop.run(a_task())
        assert {a.name for a in brain.calls[0]["agents"]} == {"research_agent"}

    def test_the_routers_view_reaches_the_brain_as_evidence(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        loop.run(a_task())
        joined = " ".join(brain.last_notes)
        assert "capability router would select 'research_agent'" in joined

    def test_the_routing_note_is_phrased_as_observation_not_instruction(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        loop.run(a_task())
        note = brain.last_notes[0]
        assert "would select" in note
        assert "must" not in note.lower()

    def test_an_unroutable_task_is_reported_honestly_to_the_brain(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain, agents=())
        loop.run(a_task())
        assert "finds no registered agent" in " ".join(brain.last_notes)

    def test_the_brain_receives_only_inert_text(self):
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        loop.run(a_task())
        assert all(isinstance(note, str) for note in brain.last_notes)


# ----------------------------------------------------------------------
# 2. THE ORDER: recall before deliberation
# ----------------------------------------------------------------------


class TestTheOrderIsCorrect:
    """Memory must be recalled before the brain forms a view, not after."""

    def test_recall_happens_before_deliberation(self):
        order = []

        class WatchingMemory(EphemeralMemory):
            def recall(self, query="", *, kind=None, tags=(), limit=10):
                order.append("recall")
                return super().recall(query, kind=kind, tags=tags, limit=limit)

        class OrderedBrain(ScriptedBrain):
            def deliberate(self, task, agents=(), notes=()):
                order.append("deliberate")
                return super().deliberate(task, agents, notes)

        memory = WatchingMemory()
        memory.remember(remembered("research tasks usually succeed"))
        loop, _ = loop_with(OrderedBrain(), memory=memory)
        loop.run(a_task())

        assert order == ["recall", "deliberate"]

    def test_recalled_memory_reaches_the_brain(self):
        memory = EphemeralMemory()
        memory.remember(remembered("research tasks usually succeed"))
        brain = ScriptedBrain()
        loop, _ = loop_with(brain, memory=memory)
        loop.run(a_task())

        assert any("usually succeed" in note for note in brain.last_notes)

    def test_recalled_memory_is_marked_as_recalled(self):
        memory = EphemeralMemory()
        memory.remember(remembered("research tasks usually succeed"))
        brain = ScriptedBrain()
        loop, _ = loop_with(brain, memory=memory)
        loop.run(a_task())

        assert any(note.startswith("recalled:") for note in brain.last_notes)

    def test_the_routing_note_is_not_marked_as_recalled(self):
        """Evidence about now must not be confused with history."""
        brain = ScriptedBrain()
        loop, _ = loop_with(brain)
        loop.run(a_task())
        assert not brain.last_notes[0].startswith("recalled:")

    def test_diagnose_happens_before_deliberation(self):
        order = []

        class WatchingRouter(CapabilityRouter):
            def route(self, task, agents):
                order.append("route")
                return super().route(task, agents)

        class OrderedBrain(ScriptedBrain):
            def deliberate(self, task, agents=(), notes=()):
                order.append("deliberate")
                return super().deliberate(task, agents, notes)

        loop, _ = loop_with(OrderedBrain(), router=WatchingRouter())
        loop.run(a_task())
        assert order[0] == "route" and "deliberate" in order


# ----------------------------------------------------------------------
# 3. The decision reaches the action layer
# ----------------------------------------------------------------------


class TestTheDecisionReachesTheActionLayer:
    def test_the_action_layer_receives_the_decision(self):
        seen = {}

        class WatchingExecutor(ActionExecutor):
            def execute(self, decision, task):
                seen["decision"] = decision
                seen["task"] = task
                return super().execute(decision, task)

        brain = ScriptedBrain()
        registry = AgentRegistry()
        registry.register(SpyAgent())
        manager = ManagerAgent(registry=registry, brain=brain)
        loop = CognitiveLoop(manager, WatchingExecutor(manager))

        task = a_task()
        loop.run(task)

        assert seen["decision"] is brain.decision
        assert seen["task"] is task

    def test_the_outcome_carries_the_decision_and_the_action(self):
        loop, _ = loop_with()
        outcome = loop.run(a_task())
        assert isinstance(outcome, CognitiveOutcome)
        assert isinstance(outcome.decision, BrainDecision)
        assert isinstance(outcome.action, ActionResult)

    def test_the_outcome_records_what_the_router_chose(self):
        loop, _ = loop_with()
        assert loop.run(a_task()).routed_to == "research_agent"

    def test_agreement_is_visible_on_the_outcome(self):
        loop, _ = loop_with()
        assert loop.run(a_task()).agreed

    def test_the_executor_is_injectable(self):
        registry = AgentRegistry()
        registry.register(SpyAgent())
        manager = ManagerAgent(registry=registry, brain=ScriptedBrain())
        executor = ActionExecutor(manager)
        assert CognitiveLoop(manager, executor)._executor is executor


# ----------------------------------------------------------------------
# 4-5. THE KEY CLAIM: the Router stays authoritative
# ----------------------------------------------------------------------


class TestTheRouterStaysAuthoritative:
    def _two_agents(self):
        return (
            SpyAgent(name="research_agent", capabilities=("research",)),
            SpyAgent(name="writing_agent", capabilities=("write",)),
        )

    def test_a_brain_proposing_the_wrong_specialist_executes_nothing(self):
        research, writing = self._two_agents()
        brain = ScriptedBrain(
            BrainDecision(
                action=BrainAction.DELEGATE,
                reason="I want the writer",
                confidence=1.0,
                delegate_to="writing_agent",
            )
        )
        loop, _ = loop_with(brain, agents=(research, writing))
        outcome = loop.run(a_task("research something"))

        assert outcome.action.status is ActionStatus.REFUSED
        assert research.executions == []
        assert writing.executions == []

    def test_the_disagreement_is_visible(self):
        research, writing = self._two_agents()
        brain = ScriptedBrain(
            BrainDecision(
                action=BrainAction.DELEGATE, reason="x", delegate_to="writing_agent"
            )
        )
        loop, _ = loop_with(brain, agents=(research, writing))
        outcome = loop.run(a_task("research something"))

        assert not outcome.agreed
        assert outcome.routed_to == "research_agent"
        assert outcome.decision.delegate_to == "writing_agent"

    def test_agreement_executes_the_agreed_agent(self):
        research, writing = self._two_agents()
        loop, _ = loop_with(agents=(research, writing))
        outcome = loop.run(a_task("research something"))

        assert outcome.executed
        assert len(research.executions) == 1
        assert writing.executions == []

    def test_a_hallucinated_agent_is_refused(self):
        research = SpyAgent()
        brain = ScriptedBrain(
            BrainDecision(
                action=BrainAction.DELEGATE, reason="x", delegate_to="ghost_agent"
            )
        )
        loop, _ = loop_with(brain, agents=(research,))
        outcome = loop.run(a_task())

        assert outcome.action.status is ActionStatus.REFUSED
        assert research.executions == []

    def test_the_brain_never_executes_a_specialist_itself(self):
        research = SpyAgent()
        brain = ScriptedBrain()
        loop, _ = loop_with(brain, agents=(research,))
        loop.run(a_task())

        # The brain saw specs, never agents - nothing it held could be run.
        assert all(isinstance(a, AgentSpec) for a in brain.calls[0]["agents"])
        assert not any(hasattr(a, "execute") for a in brain.calls[0]["agents"])


# ----------------------------------------------------------------------
# 6. Non-delegating actions execute nothing
# ----------------------------------------------------------------------


class TestNonDelegatingActions:
    @pytest.mark.parametrize(
        "action", [BrainAction.REPORT, BrainAction.ESCALATE, BrainAction.UNKNOWN]
    )
    def test_no_specialist_is_executed(self, action):
        agent = SpyAgent()
        brain = ScriptedBrain(BrainDecision(action=action, reason="not delegation"))
        loop, _ = loop_with(brain, agents=(agent,))
        outcome = loop.run(a_task())

        assert not outcome.executed
        assert agent.executions == []
        assert outcome.result is None

    @pytest.mark.parametrize(
        "action,status",
        [
            (BrainAction.REPORT, ActionStatus.NO_ACTION),
            (BrainAction.ESCALATE, ActionStatus.ESCALATED),
            (BrainAction.UNKNOWN, ActionStatus.NO_ACTION),
        ],
    )
    def test_the_status_is_correct(self, action, status):
        brain = ScriptedBrain(BrainDecision(action=action, reason="not delegation"))
        loop, _ = loop_with(brain)
        assert loop.run(a_task()).action.status is status

    @pytest.mark.parametrize(
        "action", [BrainAction.REPORT, BrainAction.ESCALATE, BrainAction.UNKNOWN]
    )
    def test_nothing_is_recorded(self, action):
        brain = ScriptedBrain(BrainDecision(action=action, reason="not delegation"))
        loop, manager = loop_with(brain)
        loop.run(a_task())
        assert len(manager.memory) == 0


# ----------------------------------------------------------------------
# 7-8. The Assessor
# ----------------------------------------------------------------------


class TestExecutionReachesTheAssessor:
    def _recording_assessor(self):
        seen = []

        class RecordingAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                seen.append((agent_name, result))
                return super().assess(task, agent_name, result)

        return RecordingAssessor(), seen

    def test_a_successful_execution_is_assessed(self):
        assessor, seen = self._recording_assessor()
        loop, _ = loop_with(assessor=assessor)
        outcome = loop.run(a_task())

        assert len(seen) == 1 and seen[0][0] == "research_agent"
        assert outcome.result.status is TaskStatus.COMPLETED

    def test_a_failed_execution_is_assessed(self):
        assessor, seen = self._recording_assessor()
        agent = SpyAgent(
            result=lambda task: TaskResult.failure(task, "research_agent", "upstream down")
        )
        loop, _ = loop_with(agents=(agent,), assessor=assessor)
        outcome = loop.run(a_task())

        assert len(seen) == 1
        assert outcome.result.status is TaskStatus.FAILED
        assert "upstream down" in outcome.result.error

    def test_a_crashing_specialist_is_assessed_as_failed(self):
        agent = SpyAgent(
            result=lambda task: (_ for _ in ()).throw(RuntimeError("agent crashed"))
        )
        loop, _ = loop_with(agents=(agent,))
        outcome = loop.run(a_task())

        assert outcome.executed
        assert outcome.result.status is TaskStatus.FAILED

    def test_a_malformed_result_is_rejected_by_the_assessor(self):
        agent = SpyAgent(result=lambda task: {"not": "a result"})
        loop, _ = loop_with(agents=(agent,))
        assert loop.run(a_task()).result.status is TaskStatus.REJECTED

    def test_the_assessor_is_not_called_when_nothing_ran(self):
        assessor, seen = self._recording_assessor()
        brain = ScriptedBrain(BrainDecision(action=BrainAction.REPORT, reason="x"))
        loop, _ = loop_with(brain, assessor=assessor)
        loop.run(a_task())
        assert seen == []


# ----------------------------------------------------------------------
# 9. Recording after assessment
# ----------------------------------------------------------------------


class TestRecordingHappensAfterAssessment:
    def test_a_successful_delegation_is_recorded(self):
        loop, manager = loop_with()
        loop.run(a_task())

        records = manager.memory.recall(limit=10)
        assert len(records) == 1
        assert records[0].kind == "delegation_outcome"

    def test_the_record_names_the_specialist(self):
        loop, manager = loop_with()
        loop.run(a_task())
        assert manager.memory.recall(limit=10)[0].context["agent"] == "research_agent"

    def test_a_failed_delegation_is_recorded(self):
        agent = SpyAgent(
            result=lambda task: TaskResult.failure(task, "research_agent", "down")
        )
        loop, manager = loop_with(agents=(agent,))
        loop.run(a_task())

        records = manager.memory.recall(limit=10)
        assert len(records) == 1
        assert records[0].context["outcome"] == "retry"

    def test_a_refusal_is_not_recorded(self):
        """A mission that never ran is not a delegation outcome."""
        brain = ScriptedBrain(
            BrainDecision(action=BrainAction.DELEGATE, reason="x", delegate_to="ghost_agent")
        )
        loop, manager = loop_with(brain)
        loop.run(a_task())
        assert len(manager.memory) == 0

    def test_recording_happens_after_assessment(self):
        order = []

        class OrderedAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                order.append("assess")
                return super().assess(task, agent_name, result)

        class WatchingMemory(EphemeralMemory):
            def remember(self, record):
                order.append("remember")
                return super().remember(record)

        loop, _ = loop_with(memory=WatchingMemory(), assessor=OrderedAssessor())
        loop.run(a_task())
        assert order == ["assess", "remember"]

    def test_recording_goes_through_the_policy(self):
        from src.services.memory.policy import MemoryPolicy, PolicyVerdict

        class RefuseEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="refused for this test")

        loop, manager = loop_with(memory_policy=RefuseEverything())
        outcome = loop.run(a_task())

        assert outcome.executed          # the work still happened
        assert len(manager.memory) == 0  # the policy still governs retention

    def test_repeated_runs_record_independently(self):
        loop, manager = loop_with()
        for _ in range(3):
            loop.run(a_task())
        assert len(manager.memory) == 3


# ----------------------------------------------------------------------
# 10. Fault isolation
# ----------------------------------------------------------------------


class TestFaultIsolation:
    def test_a_brain_failure_does_not_crash_the_loop(self):
        loop, _ = loop_with(ScriptedBrain(fail=RuntimeError("kaboom")))
        outcome = loop.run(a_task())

        assert isinstance(outcome, CognitiveOutcome)
        assert outcome.decision.action is BrainAction.UNKNOWN
        assert "kaboom" in outcome.decision.reason

    def test_a_brain_failure_executes_nothing(self):
        agent = SpyAgent()
        loop, _ = loop_with(ScriptedBrain(fail=RuntimeError("kaboom")), agents=(agent,))
        outcome = loop.run(a_task())

        assert not outcome.executed
        assert agent.executions == []

    def test_a_brain_returning_nonsense_is_refused(self):
        class RogueBrain(ManagerBrain):
            @property
            def name(self):
                return "rogue"

            def deliberate(self, task, agents=(), notes=()):
                return "just run it"

        agent = SpyAgent()
        loop, _ = loop_with(RogueBrain(), agents=(agent,))
        outcome = loop.run(a_task())

        assert not outcome.executed
        assert agent.executions == []

    def test_a_memory_failure_does_not_crash_the_loop(self):
        class ExplodingMemory(Memory):
            def remember(self, record):
                raise RuntimeError("the store is on fire")

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                raise RuntimeError("the store is on fire")

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        loop, _ = loop_with(memory=ExplodingMemory())
        outcome = loop.run(a_task())
        assert outcome.executed
        assert outcome.result.status is TaskStatus.COMPLETED

    def test_the_default_null_brain_runs_nothing_but_does_not_crash(self):
        agent = SpyAgent()
        loop, _ = loop_with(NullBrain(), agents=(agent,))
        outcome = loop.run(a_task())

        assert outcome.decision.action is BrainAction.UNKNOWN
        assert agent.executions == []

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        agent = SpyAgent(result=lambda task: (_ for _ in ()).throw(KeyboardInterrupt()))
        loop, _ = loop_with(agents=(agent,))
        with pytest.raises(KeyboardInterrupt):
            loop.run(a_task())

    def test_a_system_exit_is_not_swallowed(self):
        loop, _ = loop_with(ScriptedBrain(fail=SystemExit(1)))
        with pytest.raises(SystemExit):
            loop.run(a_task())


# ----------------------------------------------------------------------
# 11. handle_task is unchanged
# ----------------------------------------------------------------------


class TestHandleTaskIsUnchanged:
    def test_handle_task_still_works(self):
        agent = SpyAgent()
        _, manager = loop_with(agents=(agent,))
        result = manager.handle_task(a_task())

        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "research_agent"

    def test_handle_task_never_consults_the_brain(self):
        brain = ScriptedBrain()
        _, manager = loop_with(brain)
        manager.handle_task(a_task())
        assert brain.calls == []

    def test_handle_task_ignores_a_brain_that_would_refuse(self):
        """The mechanical path is unaffected by what the brain would say."""
        research = SpyAgent(name="research_agent", capabilities=("research",))
        brain = ScriptedBrain(BrainDecision(action=BrainAction.ESCALATE, reason="no"))
        _, manager = loop_with(brain, agents=(research,))

        result = manager.handle_task(a_task())
        assert result.status is TaskStatus.COMPLETED
        assert len(research.executions) == 1

    def test_handle_task_still_records_its_own_outcome(self):
        _, manager = loop_with()
        manager.handle_task(a_task())
        assert manager.memory.recall(limit=5)[0].kind == "delegation_outcome"

    def test_the_manager_module_does_not_import_the_loop(self):
        import src.manager.manager as module

        code = code_of(module)
        assert "cognitive_loop" not in code
        assert "actionexecutor" not in code

    def test_both_paths_produce_the_same_task_result(self):
        mechanical = SpyAgent()
        cognitive = SpyAgent()
        _, manager_a = loop_with(agents=(mechanical,))
        loop_b, _ = loop_with(agents=(cognitive,))

        task = a_task()
        by_hand = manager_a.handle_task(task)
        by_brain = loop_b.run(task).result

        assert by_hand.status == by_brain.status
        assert by_hand.agent_name == by_brain.agent_name
        assert by_hand.output == by_brain.output

    def test_a_specialist_cannot_tell_which_path_invoked_it(self):
        agent = SpyAgent()
        loop, manager = loop_with(agents=(agent,))
        task = a_task()

        manager.handle_task(task)
        loop.run(task)

        assert agent.executions == [task, task]   # the same Task, unmodified


# ----------------------------------------------------------------------
# 12-13. The specialist boundary
# ----------------------------------------------------------------------


class TestTheSpecialistBoundary:
    def test_no_specialist_receives_manager_memory(self):
        seen = {}

        def capture(task):
            seen["task"] = task
            return TaskResult.success(task, "research_agent", "done")

        agent = SpyAgent(result=capture)
        loop, manager = loop_with(agents=(agent,))
        manager.memory.remember(remembered("something only the manager knows"))

        loop.run(a_task())

        delivered = seen["task"]
        assert not hasattr(delivered, "memory")
        assert not hasattr(delivered, "brain")
        assert not hasattr(delivered, "manager")

    def test_a_cognitive_specialist_keeps_its_own_memory(self):
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
        loop, manager = loop_with(agents=(agent,))
        manager.memory.remember(remembered("something only the manager knows"))

        loop.run(a_task())

        assert agent.memory is not manager.memory
        assert agent.memory.recall("something") == ()

    def test_no_specialist_can_reach_the_manager_brain(self):
        agent = SpyAgent()
        brain = ScriptedBrain()
        loop, _ = loop_with(brain, agents=(agent,))
        loop.run(a_task())

        assert not hasattr(agent, "brain") or agent.brain is not brain

    def test_the_loop_hands_the_specialist_only_a_task(self):
        import inspect

        source = inspect.getsource(CognitiveLoop.run)
        assert ".execute(" not in source.replace("self._executor.execute(", "")


# ----------------------------------------------------------------------
# 14-15. Nothing new was connected
# ----------------------------------------------------------------------


class TestNothingNewWasConnected:
    def test_the_loop_imports_no_provider(self):
        import src.manager.cognitive_loop as module

        code = code_of(module)
        assert "from src.providers" not in code
        assert "anthropic" not in code

    def test_no_anthropic_sdk_is_imported(self):
        loop, _ = loop_with()
        loop.run(a_task())
        assert "anthropic" not in sys.modules

    def test_the_loop_reaches_no_mail_or_gmail(self):
        import src.manager.cognitive_loop as module

        code = code_of(module)
        for word in ("gmail", "mailbox", "email", "smtp"):
            assert word not in code, word

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        loop, _ = loop_with()
        assert loop.run(a_task()).executed

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        loop, _ = loop_with()
        loop.run(a_task())
        assert list(tmp_path.iterdir()) == []

    def test_the_loop_holds_no_state_between_runs(self):
        loop, _ = loop_with()
        before = dict(vars(loop))
        for _ in range(5):
            loop.run(a_task())
        assert vars(loop) == before

    def test_the_contracts_are_unchanged(self):
        import inspect

        from src.services.memory.memory import Memory as MemoryContract

        assert set(ManagerBrain.__abstractmethods__) == {"name", "deliberate"}
        assert set(MemoryContract.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }
        params = set(inspect.signature(ManagerBrain.deliberate).parameters)
        assert params == {"self", "task", "agents", "notes"}

    def test_the_outcome_is_deterministic(self):
        loop_a, _ = loop_with()
        loop_b, _ = loop_with()
        task = a_task()

        first = loop_a.run(task)
        second = loop_b.run(task)

        assert (first.routed_to, first.action.status, first.action.reason) == (
            second.routed_to,
            second.action.status,
            second.action.reason,
        )
