"""Tests for the bounded tool-use architecture.

    brain requests -> loop validates -> toolset permits and runs -> brain observes

The claims that carry the weight:

    TestTheLoopIsBounded     - no input produces an unbounded run
    TestPermissionIsMembership - an ungranted capability is not a capability
    TestTheBrainCannotAct    - requesting is not doing

Every brain and every tool here is a deterministic test double. No Anthropic,
no network, no files, no Gmail.
"""

import ast
import socket
import sys

import pytest

from src.agents.brain import Insight, SpecialistBrain
from src.agents.cognitive_agent import CognitiveAgent
from src.agents.email_agent import EmailAgent
from src.agents.loop import (
    DEFAULT_MAX_ITERATIONS,
    AgentLoop,
    LoopError,
    LoopOutcome,
    NextStep,
    StepKind,
    Termination,
    ToolRequest,
    ToolUsingBrain,
)
from src.agents.tools.tool import Tool, ToolError, ToolResult, ToolSpec, ToolStatus
from src.agents.tools.toolset import ToolSet
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.services.email.sample_inbox import sample_inbox
from src.services.memory.ephemeral import EphemeralMemory


def executable_imports(module) -> set[str]:
    """Every module actually imported, from the syntax tree rather than the text.

    Grepping raw source would match the word "anthropic" in a docstring
    explaining that it is deliberately absent, which is exactly backwards.
    """
    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


# ----------------------------------------------------------------------
# Fake tools - entirely in memory, no side effects of any kind
# ----------------------------------------------------------------------


class CountingTool(Tool):
    """Returns a fixed answer and records that it was called."""

    def __init__(self, name="lookup", answer="the answer", schema=None):
        self._name = name
        self._answer = answer
        self._schema = {"subject": str} if schema is None else schema
        self.calls = []

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description=f"a fake {self._name} tool used only in tests",
            schema=self._schema,
        )

    def run(self, **arguments):
        self.calls.append(arguments)
        return self._answer


class ExplodingTool(Tool):
    def __init__(self, error=None):
        self.error = error or RuntimeError("the tool broke")

    @property
    def spec(self):
        return ToolSpec(name="exploding", description="raises when called")

    def run(self, **arguments):
        raise self.error


class WrongTypeTool(Tool):
    @property
    def spec(self):
        return ToolSpec(name="wrong_type", description="returns a non-string")

    def run(self, **arguments):
        return {"not": "text"}


# ----------------------------------------------------------------------
# Scripted brain - the deterministic stand-in for a model
# ----------------------------------------------------------------------


class ScriptedLoopBrain(ToolUsingBrain):
    """Replays a fixed sequence of steps, recording what it was shown."""

    def __init__(self, *steps, fail=None):
        self._steps = list(steps)
        self.fail = fail
        self.calls = []

    @property
    def name(self):
        return "scripted"

    def next_step(self, task, tools=(), notes=(), observations=()):
        self.calls.append(
            {
                "task": task,
                "tools": tuple(tools),
                "notes": tuple(notes),
                "observations": tuple(observations),
            }
        )
        if self.fail is not None:
            raise self.fail
        if not self._steps:
            return NextStep.finish(Insight(summary="nothing left to do", confidence=0.5))
        return self._steps.pop(0)

    @property
    def last_observations(self):
        return self.calls[-1]["observations"]


class NeverFinishingBrain(ToolUsingBrain):
    """Always asks for another tool. The adversarial case."""

    def __init__(self):
        self.calls = 0

    @property
    def name(self):
        return "never-finishing"

    def next_step(self, task, tools=(), notes=(), observations=()):
        self.calls += 1
        return NextStep.use_tool("lookup", subject="anything")


def a_task(description="work out what this posting requires"):
    return Task(description=description)


def done(summary="I have what I need", confidence=0.8):
    return NextStep.finish(Insight(summary=summary, confidence=confidence))


def loop_with(*steps, tools=None, max_iterations=DEFAULT_MAX_ITERATIONS, brain=None):
    brain = brain if brain is not None else ScriptedLoopBrain(*steps)
    return AgentLoop(brain, tools, max_iterations=max_iterations), brain


# ----------------------------------------------------------------------
# The tool contract
# ----------------------------------------------------------------------


class TestTheToolContract:
    def test_a_tool_declares_name_description_and_schema(self):
        spec = CountingTool().spec
        assert spec.name == "lookup"
        assert spec.description
        assert spec.schema == {"subject": str}

    def test_a_tool_must_have_a_name(self):
        with pytest.raises(ToolError):
            ToolSpec(name="  ", description="x")

    def test_a_tool_must_describe_itself(self):
        with pytest.raises(ToolError):
            ToolSpec(name="x", description="   ")

    def test_a_schema_must_declare_types(self):
        with pytest.raises(ToolError):
            ToolSpec(name="x", description="y", schema={"a": "string"})

    def test_the_tool_module_imports_no_provider_or_vendor(self):
        import src.agents.tools.tool as module

        imports = executable_imports(module)
        assert not any(i.startswith("src.providers") for i in imports)
        assert "anthropic" not in imports and "openai" not in imports

    def test_the_tool_module_imports_no_framework(self):
        import src.agents.tools.tool as module

        imports = executable_imports(module)
        for framework in ("langchain", "crewai", "llama_index", "autogen"):
            assert not any(framework in i for i in imports)

    def test_a_tool_receives_no_toolset(self):
        """A tool cannot reach another tool: it is handed arguments only."""
        import inspect

        params = set(inspect.signature(Tool.run).parameters)
        assert params == {"self", "arguments"}

    def test_a_tool_holds_no_reference_to_the_system(self):
        tool = CountingTool()
        for attribute in ("tools", "toolset", "manager", "registry", "agent"):
            assert not hasattr(tool, attribute), attribute


class TestTheToolResultContract:
    def test_it_distinguishes_three_outcomes(self):
        assert {s.value for s in ToolStatus} == {"succeeded", "rejected", "failed"}

    def test_a_result_is_frozen(self):
        result = ToolResult.succeeded("lookup", "output")
        with pytest.raises(Exception):
            result.status = ToolStatus.FAILED

    def test_a_failure_must_explain_itself(self):
        with pytest.raises(ToolError):
            ToolResult(tool="lookup", status=ToolStatus.FAILED, error="  ")

    def test_a_rejection_must_explain_itself(self):
        with pytest.raises(ToolError):
            ToolResult(tool="lookup", status=ToolStatus.REJECTED)

    def test_a_result_names_its_tool(self):
        with pytest.raises(ToolError):
            ToolResult(tool="  ", status=ToolStatus.SUCCEEDED)

    def test_describe_excludes_the_output(self):
        """Tool output may be domain content; logs must not carry it."""
        result = ToolResult.succeeded("lookup", "salary is 50000 at acme")
        assert "50000" not in result.describe()

    def test_rejected_means_it_never_ran(self):
        assert not ToolStatus.REJECTED.ran
        assert ToolStatus.FAILED.ran and ToolStatus.SUCCEEDED.ran


# ----------------------------------------------------------------------
# PERMISSION IS MEMBERSHIP
# ----------------------------------------------------------------------


class TestPermissionIsMembership:
    def test_a_permitted_tool_executes(self):
        tool = CountingTool()
        result = ToolSet([tool]).invoke("lookup", {"subject": "acme"})

        assert result.ok
        assert result.output == "the answer"
        assert tool.calls == [{"subject": "acme"}]

    def test_an_unknown_tool_is_rejected(self):
        result = ToolSet([CountingTool()]).invoke("nonexistent", {})
        assert result.status is ToolStatus.REJECTED
        assert "nonexistent" in result.error

    def test_the_rejection_lists_what_is_available(self):
        result = ToolSet([CountingTool()]).invoke("nonexistent", {})
        assert "lookup" in result.error

    def test_a_tool_not_granted_is_rejected_even_though_it_exists(self):
        """The tool object exists; this specialist simply was not given it."""
        granted = CountingTool(name="granted")
        ungranted = CountingTool(name="ungranted")

        result = ToolSet([granted]).invoke("ungranted", {"subject": "x"})

        assert result.status is ToolStatus.REJECTED
        assert ungranted.calls == []

    def test_an_empty_toolset_permits_nothing(self):
        empty = ToolSet()
        assert empty.is_empty and empty.names() == ()
        assert empty.invoke("lookup", {}).status is ToolStatus.REJECTED

    def test_a_toolset_cannot_be_extended(self):
        """Structural: there is no way to add a tool after construction."""
        tools = ToolSet([CountingTool()])
        for method in ("add", "register", "grant", "update", "extend", "append"):
            assert not hasattr(tools, method), method

    def test_a_tool_cannot_add_itself(self):
        granted = [CountingTool()]
        tools = ToolSet(granted)
        granted.append(CountingTool(name="sneaky"))
        assert tools.names() == ("lookup",)

    def test_executing_a_tool_grants_nothing_further(self):
        tools = ToolSet([CountingTool()])
        before = tools.names()
        tools.invoke("lookup", {"subject": "x"})
        assert tools.names() == before

    def test_duplicate_names_are_refused(self):
        with pytest.raises(ToolError):
            ToolSet([CountingTool(), CountingTool()])

    def test_a_non_tool_is_refused(self):
        with pytest.raises(ToolError):
            ToolSet(["not a tool"])

    def test_the_toolset_does_not_import_the_manager(self):
        import src.agents.tools.toolset as module

        imports = executable_imports(module)
        assert not any(i.startswith("src.manager") for i in imports)

    def test_specs_are_handed_out_not_tools(self):
        """A caller can see what exists without holding anything callable."""
        specs = ToolSet([CountingTool()]).specs()
        assert all(isinstance(spec, ToolSpec) for spec in specs)
        assert not any(hasattr(spec, "run") for spec in specs)


class TestArgumentValidation:
    def test_missing_arguments_are_rejected(self):
        result = ToolSet([CountingTool()]).invoke("lookup", {})
        assert result.status is ToolStatus.REJECTED
        assert "missing" in result.error and "subject" in result.error

    def test_unexpected_arguments_are_rejected(self):
        result = ToolSet([CountingTool()]).invoke(
            "lookup", {"subject": "a", "extra": "b"}
        )
        assert result.status is ToolStatus.REJECTED
        assert "unexpected" in result.error

    def test_wrong_types_are_rejected(self):
        result = ToolSet([CountingTool()]).invoke("lookup", {"subject": 42})
        assert result.status is ToolStatus.REJECTED
        assert "str" in result.error

    def test_a_bool_is_not_a_number(self):
        tool = CountingTool(schema={"count": int})
        result = ToolSet([tool]).invoke("lookup", {"count": True})
        assert result.status is ToolStatus.REJECTED

    def test_a_rejected_call_never_runs_the_tool(self):
        tool = CountingTool()
        ToolSet([tool]).invoke("lookup", {})
        assert tool.calls == []

    def test_a_tool_with_no_parameters_takes_none(self):
        tool = CountingTool(name="ping", answer="pong", schema={})
        assert ToolSet([tool]).invoke("ping", {}).output == "pong"


# ----------------------------------------------------------------------
# Fault isolation at the boundary
# ----------------------------------------------------------------------


class TestToolFailuresAreContained:
    def test_a_raising_tool_becomes_a_failed_result(self):
        result = ToolSet([ExplodingTool()]).invoke("exploding", {})
        assert result.status is ToolStatus.FAILED
        assert "the tool broke" in result.error

    def test_the_failure_names_the_exception_type(self):
        result = ToolSet([ExplodingTool(ValueError("bad input"))]).invoke("exploding", {})
        assert "ValueError" in result.error

    def test_a_non_text_return_is_refused(self):
        """A live object handed to a reasoning step would be a capability."""
        result = ToolSet([WrongTypeTool()]).invoke("wrong_type", {})
        assert result.status is ToolStatus.FAILED
        assert "not text" in result.error

    def test_a_keyboard_interrupt_propagates(self):
        tools = ToolSet([ExplodingTool(KeyboardInterrupt())])
        with pytest.raises(KeyboardInterrupt):
            tools.invoke("exploding", {})

    def test_a_system_exit_propagates(self):
        tools = ToolSet([ExplodingTool(SystemExit(1))])
        with pytest.raises(SystemExit):
            tools.invoke("exploding", {})

    def test_one_failing_tool_does_not_break_the_set(self):
        tools = ToolSet([ExplodingTool(), CountingTool()])
        tools.invoke("exploding", {})
        assert tools.invoke("lookup", {"subject": "x"}).ok


# ----------------------------------------------------------------------
# The step contract
# ----------------------------------------------------------------------


class TestTheStepContract:
    def test_only_two_kinds_exist(self):
        assert {k.value for k in StepKind} == {"use_tool", "finish"}

    def test_there_is_no_orchestration_vocabulary(self):
        kinds = {k.value for k in StepKind}
        for forbidden in ("delegate", "escalate", "route", "execute_agent"):
            assert forbidden not in kinds

    def test_the_loop_module_declares_no_orchestration_actions(self):
        import src.agents.loop as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for forbidden in ("DELEGATE", "ESCALATE", "ROUTE", "EXECUTE_AGENT"):
            assert forbidden not in names

    def test_a_tool_step_must_name_a_tool(self):
        with pytest.raises(LoopError):
            NextStep(kind=StepKind.USE_TOOL)

    def test_a_finish_step_must_carry_an_insight(self):
        with pytest.raises(LoopError):
            NextStep(kind=StepKind.FINISH)

    def test_a_step_cannot_both_act_and_conclude(self):
        with pytest.raises(LoopError):
            NextStep(
                kind=StepKind.USE_TOOL,
                request=ToolRequest(tool="lookup"),
                insight=Insight(summary="done", confidence=0.5),
            )

    def test_a_request_must_name_a_tool(self):
        with pytest.raises(LoopError):
            ToolRequest(tool="   ")

    def test_arguments_must_be_a_mapping(self):
        with pytest.raises(LoopError):
            ToolRequest(tool="lookup", arguments=["subject", "acme"])

    def test_insight_gained_no_action_field(self):
        """The existing contract must not have been quietly widened."""
        assert set(Insight.__dataclass_fields__) == {
            "summary",
            "confidence",
            "source",
            "unknowns",
        }

    def test_the_specialist_brain_contract_is_unchanged(self):
        import inspect

        assert set(SpecialistBrain.__abstractmethods__) == {"name", "consider"}
        params = set(inspect.signature(SpecialistBrain.consider).parameters)
        assert params == {"self", "task", "notes"}

    def test_the_tool_using_brain_is_a_separate_contract(self):
        assert not issubclass(ToolUsingBrain, SpecialistBrain)
        assert set(ToolUsingBrain.__abstractmethods__) == {"name", "next_step"}


# ----------------------------------------------------------------------
# THE LOOP IS BOUNDED
# ----------------------------------------------------------------------


class TestTheLoopIsBounded:
    def test_there_is_no_while_loop_in_the_module(self):
        """Proven at the syntax level, not by behaviour."""
        import src.agents.loop as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.While)]

    def test_a_brain_that_never_finishes_is_stopped(self):
        brain = NeverFinishingBrain()
        loop = AgentLoop(brain, ToolSet([CountingTool()]), max_iterations=4)
        outcome = loop.run(a_task())

        assert outcome.termination is Termination.EXHAUSTED
        assert brain.calls == 4
        assert outcome.tool_calls == 4

    def test_the_limit_is_respected_exactly(self):
        for limit in (1, 2, 3, 7):
            brain = NeverFinishingBrain()
            AgentLoop(brain, ToolSet([CountingTool()]), max_iterations=limit).run(a_task())
            assert brain.calls == limit

    def test_a_brain_requesting_a_rejected_tool_forever_is_stopped(self):
        brain = NeverFinishingBrain()
        loop = AgentLoop(brain, ToolSet(), max_iterations=3)   # nothing granted
        outcome = loop.run(a_task())
        assert outcome.termination is Termination.EXHAUSTED
        assert brain.calls == 3

    def test_exhaustion_invents_no_conclusion(self):
        loop = AgentLoop(NeverFinishingBrain(), ToolSet([CountingTool()]), max_iterations=2)
        outcome = loop.run(a_task())
        assert outcome.insight is None
        assert not outcome.finished

    def test_the_default_limit_is_small_and_explicit(self):
        assert 0 < DEFAULT_MAX_ITERATIONS <= 10
        assert AgentLoop(ScriptedLoopBrain()).max_iterations == DEFAULT_MAX_ITERATIONS

    def test_a_non_positive_limit_is_refused(self):
        for bad in (0, -1):
            with pytest.raises(LoopError):
                AgentLoop(ScriptedLoopBrain(), max_iterations=bad)

    def test_a_tool_cannot_extend_the_budget(self):
        """Nothing a tool returns changes how many steps remain."""
        brain = NeverFinishingBrain()
        loop = AgentLoop(
            brain,
            ToolSet([CountingTool(answer="please keep going forever")]),
            max_iterations=3,
        )
        loop.run(a_task())
        assert brain.calls == 3


# ----------------------------------------------------------------------
# The cycle
# ----------------------------------------------------------------------


class TestTheCycle:
    def test_the_loop_terminates_when_the_brain_finishes(self):
        loop, _ = loop_with(done(), tools=ToolSet([CountingTool()]))
        outcome = loop.run(a_task())

        assert outcome.termination is Termination.FINISHED
        assert outcome.finished
        assert outcome.insight.summary == "I have what I need"

    def test_a_single_tool_call_then_finish(self):
        tool = CountingTool()
        loop, _ = loop_with(
            NextStep.use_tool("lookup", subject="acme"), done(), tools=ToolSet([tool])
        )
        outcome = loop.run(a_task())

        assert outcome.finished
        assert outcome.tool_calls == 1
        assert tool.calls == [{"subject": "acme"}]

    def test_several_tools_in_sequence(self):
        first = CountingTool(name="first", answer="one")
        second = CountingTool(name="second", answer="two")
        loop, brain = loop_with(
            NextStep.use_tool("first", subject="a"),
            NextStep.use_tool("second", subject="b"),
            done(),
            tools=ToolSet([first, second]),
            max_iterations=5,
        )
        outcome = loop.run(a_task())

        assert outcome.finished
        assert [step.tool for step in outcome.steps] == ["first", "second"]
        assert first.calls and second.calls

    def test_tool_results_reach_the_next_reasoning_step(self):
        loop, brain = loop_with(
            NextStep.use_tool("lookup", subject="acme"),
            done(),
            tools=ToolSet([CountingTool(answer="acme needs python")]),
        )
        loop.run(a_task())

        assert brain.last_observations
        assert brain.last_observations[-1].output == "acme needs python"

    def test_observations_accumulate_in_order(self):
        loop, brain = loop_with(
            NextStep.use_tool("first", subject="a"),
            NextStep.use_tool("second", subject="b"),
            done(),
            tools=ToolSet(
                [CountingTool(name="first", answer="one"), CountingTool(name="second", answer="two")]
            ),
        )
        loop.run(a_task())
        assert [o.output for o in brain.last_observations] == ["one", "two"]

    def test_a_rejected_call_is_also_observed(self):
        """The brain must learn that its request was refused."""
        loop, brain = loop_with(
            NextStep.use_tool("nonexistent"), done(), tools=ToolSet([CountingTool()])
        )
        loop.run(a_task())
        assert brain.last_observations[-1].status is ToolStatus.REJECTED

    def test_a_failed_call_is_also_observed(self):
        loop, brain = loop_with(
            NextStep.use_tool("exploding"), done(), tools=ToolSet([ExplodingTool()])
        )
        loop.run(a_task())
        assert brain.last_observations[-1].status is ToolStatus.FAILED

    def test_the_brain_is_shown_the_available_tools(self):
        loop, brain = loop_with(done(), tools=ToolSet([CountingTool()]))
        loop.run(a_task())
        assert [spec.name for spec in brain.calls[0]["tools"]] == ["lookup"]

    def test_notes_reach_the_brain_as_inert_text(self):
        loop, brain = loop_with(done(), tools=ToolSet())
        loop.run(a_task(), notes=["recalled: digests are usually stale"])
        assert brain.calls[0]["notes"] == ("recalled: digests are usually stale",)


# ----------------------------------------------------------------------
# THE BRAIN CANNOT ACT
# ----------------------------------------------------------------------


class TestTheBrainCannotAct:
    def test_the_brain_is_never_given_the_toolset(self):
        loop, brain = loop_with(done(), tools=ToolSet([CountingTool()]))
        loop.run(a_task())

        shown = brain.calls[0]["tools"]
        assert not any(isinstance(item, ToolSet) for item in shown)
        assert all(isinstance(item, ToolSpec) for item in shown)

    def test_the_brain_is_never_given_a_tool(self):
        loop, brain = loop_with(done(), tools=ToolSet([CountingTool()]))
        loop.run(a_task())
        assert not any(isinstance(item, Tool) for item in brain.calls[0]["tools"])

    def test_a_request_alone_executes_nothing(self):
        """Constructing a step calls no tool."""
        tool = CountingTool()
        ToolSet([tool])
        NextStep.use_tool("lookup", subject="acme")
        assert tool.calls == []

    def test_the_brain_holds_no_route_to_anything(self):
        loop, brain = loop_with(done())
        loop.run(a_task())
        for attribute in ("tools", "toolset", "memory", "manager", "registry", "router"):
            assert not hasattr(brain, attribute), attribute

    def test_the_brain_cannot_reach_manager_memory(self):
        loop, brain = loop_with(done())
        loop.run(a_task())
        assert not hasattr(brain, "memory")

    def test_observations_carry_no_callables(self):
        loop, brain = loop_with(
            NextStep.use_tool("lookup", subject="a"), done(), tools=ToolSet([CountingTool()])
        )
        loop.run(a_task())
        for observation in brain.last_observations:
            assert isinstance(observation, ToolResult)
            assert not hasattr(observation, "run")

    def test_the_loop_holds_no_manager_reference(self):
        loop, _ = loop_with(done())
        for attribute in ("manager", "router", "assessor", "registry", "memory"):
            assert not hasattr(loop, attribute), attribute

    def test_the_loop_imports_no_orchestration(self):
        import src.agents.loop as module

        imports = executable_imports(module)
        assert not any(i.startswith("src.manager") for i in imports)
        assert not any(i.startswith("src.registry") for i in imports)


# ----------------------------------------------------------------------
# Fault isolation in the loop
# ----------------------------------------------------------------------


class TestLoopFaultIsolation:
    def test_a_brain_failure_terminates_cleanly(self):
        loop = AgentLoop(ScriptedLoopBrain(fail=RuntimeError("kaboom")), ToolSet())
        outcome = loop.run(a_task())

        assert outcome.termination is Termination.BRAIN_FAILED
        assert "kaboom" in outcome.reason
        assert outcome.insight is None

    def test_a_brain_returning_nonsense_terminates_cleanly(self):
        class RogueBrain(ToolUsingBrain):
            @property
            def name(self):
                return "rogue"

            def next_step(self, task, tools=(), notes=(), observations=()):
                return "just do it"

        outcome = AgentLoop(RogueBrain(), ToolSet()).run(a_task())
        assert outcome.termination is Termination.INVALID_STEP
        assert outcome.insight is None

    def test_a_brain_failure_runs_no_tool(self):
        tool = CountingTool()
        AgentLoop(
            ScriptedLoopBrain(fail=RuntimeError("kaboom")), ToolSet([tool])
        ).run(a_task())
        assert tool.calls == []

    def test_a_keyboard_interrupt_from_the_brain_propagates(self):
        loop = AgentLoop(ScriptedLoopBrain(fail=KeyboardInterrupt()), ToolSet())
        with pytest.raises(KeyboardInterrupt):
            loop.run(a_task())

    def test_a_system_exit_from_the_brain_propagates(self):
        loop = AgentLoop(ScriptedLoopBrain(fail=SystemExit(1)), ToolSet())
        with pytest.raises(SystemExit):
            loop.run(a_task())

    def test_a_tool_failure_does_not_end_the_loop(self):
        loop, _ = loop_with(
            NextStep.use_tool("exploding"), done(), tools=ToolSet([ExplodingTool()])
        )
        outcome = loop.run(a_task())
        assert outcome.finished      # the brain still got to conclude


# ----------------------------------------------------------------------
# Observability
# ----------------------------------------------------------------------


class TestObservability:
    def test_the_outcome_is_structured(self):
        loop, _ = loop_with(done())
        outcome = loop.run(a_task())
        assert isinstance(outcome, LoopOutcome)
        assert outcome.task_id and outcome.reason

    def test_each_step_is_recorded(self):
        loop, _ = loop_with(
            NextStep.use_tool("lookup", subject="a"),
            NextStep.use_tool("nonexistent"),
            done(),
            tools=ToolSet([CountingTool()]),
        )
        outcome = loop.run(a_task())

        assert [s.iteration for s in outcome.steps] == [1, 2]
        assert [s.tool for s in outcome.steps] == ["lookup", "nonexistent"]
        assert [s.permitted for s in outcome.steps] == [True, False]
        assert [s.status for s in outcome.steps] == [
            ToolStatus.SUCCEEDED,
            ToolStatus.REJECTED,
        ]

    def test_the_termination_reason_is_always_present(self):
        for loop in (
            AgentLoop(ScriptedLoopBrain(done()), ToolSet()),
            AgentLoop(NeverFinishingBrain(), ToolSet(), max_iterations=1),
            AgentLoop(ScriptedLoopBrain(fail=RuntimeError("x")), ToolSet()),
        ):
            assert loop.run(a_task()).reason.strip()

    def test_step_descriptions_carry_no_tool_output(self):
        loop, _ = loop_with(
            NextStep.use_tool("lookup", subject="a"),
            done(),
            tools=ToolSet([CountingTool(answer="salary is 50000")]),
        )
        outcome = loop.run(a_task())
        assert "50000" not in outcome.steps[0].describe()

    def test_the_run_is_deterministic(self):
        def run_once():
            loop, _ = loop_with(
                NextStep.use_tool("lookup", subject="a"),
                done(),
                tools=ToolSet([CountingTool()]),
            )
            return loop.run(a_task())

        first, second = run_once(), run_once()
        assert (first.termination, first.reason, first.tool_calls) == (
            second.termination,
            second.reason,
            second.tool_calls,
        )


# ----------------------------------------------------------------------
# The specialist grant, and memory
# ----------------------------------------------------------------------


class TestTheSpecialistGrant:
    def test_a_specialist_is_granted_nothing_by_default(self):
        agent = EmailAgent(sample_inbox())
        assert isinstance(agent.tools, ToolSet)
        assert agent.tools.is_empty

    def test_a_toolset_can_be_granted(self):
        tools = ToolSet([CountingTool()])
        assert EmailAgent(sample_inbox(), tools=tools).tools is tools

    def test_a_non_toolset_is_refused(self):
        for bad in ("lookup", [CountingTool()], 42):
            with pytest.raises(TypeError):
                EmailAgent(sample_inbox(), tools=bad)

    def test_two_specialists_hold_separate_grants(self):
        one = EmailAgent(sample_inbox(), tools=ToolSet([CountingTool()]))
        two = EmailAgent(sample_inbox())
        assert one.tools is not two.tools
        assert two.tools.is_empty

    def test_no_email_or_gmail_tool_was_added(self):
        agent = EmailAgent(sample_inbox())
        assert agent.tools.names() == ()

    def test_the_specialist_holds_no_manager_reference(self):
        agent = EmailAgent(sample_inbox(), tools=ToolSet([CountingTool()]))
        for attribute in ("manager", "router", "assessor", "executor", "registry"):
            assert not hasattr(agent, attribute), attribute

    def test_existing_email_behaviour_is_unchanged(self):
        plain = EmailAgent(sample_inbox()).execute(Task(description="scan my inbox"))
        granted = EmailAgent(sample_inbox(), tools=ToolSet([CountingTool()])).execute(
            Task(description="scan my inbox")
        )
        assert plain.status is TaskStatus.COMPLETED
        assert plain.output == granted.output


class TestToolResultsDoNotBecomeMemories:
    def test_repeated_tool_calls_fill_no_memory(self):
        memory = EphemeralMemory()
        agent = EmailAgent(sample_inbox(), memory=memory, tools=ToolSet([CountingTool()]))
        loop = AgentLoop(NeverFinishingBrain(), agent.tools, max_iterations=5)

        loop.run(a_task())

        assert len(memory) == 0

    def test_the_loop_touches_no_memory(self):
        import src.agents.loop as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("remember", "recall", "forget", "clear")

    def test_the_loop_imports_no_memory_policy(self):
        import src.agents.loop as module

        imports = executable_imports(module)
        assert not any("memory" in i for i in imports)

    def test_the_toolset_touches_no_memory(self):
        import src.agents.tools.toolset as module

        imports = executable_imports(module)
        assert not any("memory" in i for i in imports)


# ----------------------------------------------------------------------
# Nothing new was connected
# ----------------------------------------------------------------------


class TestNothingNewWasConnected:
    def test_no_anthropic_sdk_is_imported(self):
        loop, _ = loop_with(
            NextStep.use_tool("lookup", subject="a"), done(), tools=ToolSet([CountingTool()])
        )
        loop.run(a_task())
        assert "anthropic" not in sys.modules

    def test_no_module_imports_a_provider(self):
        import src.agents.loop as loop_module
        import src.agents.tools.tool as tool_module
        import src.agents.tools.toolset as toolset_module

        for module in (loop_module, tool_module, toolset_module):
            imports = executable_imports(module)
            assert not any(i.startswith("src.providers") for i in imports), module.__name__

    def test_no_module_imports_gmail_or_the_mailbox(self):
        import src.agents.loop as loop_module
        import src.agents.tools.tool as tool_module
        import src.agents.tools.toolset as toolset_module

        for module in (loop_module, tool_module, toolset_module):
            imports = executable_imports(module)
            assert not any("email" in i or "gmail" in i for i in imports), module.__name__

    def test_no_shell_or_filesystem_capability_was_added(self):
        import src.agents.loop as loop_module
        import src.agents.tools.tool as tool_module
        import src.agents.tools.toolset as toolset_module

        for module in (loop_module, tool_module, toolset_module):
            imports = executable_imports(module)
            for dangerous in ("subprocess", "os", "shutil", "pathlib", "socket", "requests"):
                assert dangerous not in imports, f"{dangerous} in {module.__name__}"

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        loop, _ = loop_with(
            NextStep.use_tool("lookup", subject="a"), done(), tools=ToolSet([CountingTool()])
        )
        assert loop.run(a_task()).finished

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        loop, _ = loop_with(
            NextStep.use_tool("lookup", subject="a"), done(), tools=ToolSet([CountingTool()])
        )
        loop.run(a_task())
        assert list(tmp_path.iterdir()) == []

    def test_the_provider_interface_is_unchanged(self):
        import inspect

        from src.providers.base import LLMProvider

        params = set(inspect.signature(LLMProvider.complete).parameters)
        assert params == {"self", "prompt", "system"}
        assert set(LLMProvider.__abstractmethods__) == {"name", "complete"}

    def test_the_loop_holds_no_state_between_runs(self):
        loop, _ = loop_with(brain=NeverFinishingBrain(), tools=ToolSet([CountingTool()]))
        before = {k: v for k, v in vars(loop).items() if k != "_brain"}
        loop.run(a_task())
        after = {k: v for k, v in vars(loop).items() if k != "_brain"}
        assert before == after
