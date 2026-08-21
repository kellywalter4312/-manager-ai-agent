"""Tests for --deliberate: the Manager's judgement, finally reachable.

Until now ``manager.deliberate()`` had no caller outside this suite. The brain
could be configured, could be handed a provider, and would never once be asked
anything - so "Full Brain" described a capability nothing exercised.

This milestone gives it exactly one caller, and the tests are shaped around the
boundary that caller must not cross:

    TestAdviceIsNeverExecuted   the advice is printed and nothing acts on it
    TestHandleTaskIsUnaffected  asking for judgement cannot change routing

That separation is the whole reason deliberate() was kept out of handle_task.
A flag that quietly reintroduced probabilistic reasoning into the mechanical
DATA loop would undo the design it is meant to expose.

No real provider, no key, no network. Full Brain runs on ScriptedProvider.
"""

import io
import json
from contextlib import redirect_stdout

import pytest
from conftest import ScriptedProvider

import main as main_module
from src.agents.echo_agent import EchoAgent
from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.brain import BrainAction, NullBrain
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.fallback_router import FallbackRouter
from src.manager.llm_brain import LLMManagerBrain
from src.manager.llm_router import LLMRouter
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter
from src.providers.base import Completion, LLMProvider


ADVICE = json.dumps(
    {
        "action": "delegate",
        "reason": "the email agent owns inbox work",
        "confidence": 0.8,
        "delegate_to": "alpha_agent",
        "steps": ["scan the inbox", "summarise what matters"],
    }
)


class FailingProvider(LLMProvider):
    """Fails when asked - the shape of an exhausted balance."""

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt, *, system=None) -> Completion:
        raise RuntimeError("credit balance is too low")


def agents():
    return [
        EchoAgent(name="alpha_agent", capabilities={"email"}),
        EchoAgent(name="beta_agent", capabilities={"calendar"}),
    ]


def manager_with(brain=None, router=None) -> ManagerAgent:
    manager = ManagerAgent(brain=brain, router=router)
    for agent in agents():
        manager.register_agent(agent)
    return manager


def run_main(argv=None, **settings_overrides) -> str:
    """Drive main() with a fixed configuration and capture what it printed.

    configure_logging is stubbed out for the same reason every other test that
    drives main() stubs it: it reconfigures handlers on the shared ``src``
    logger, and that change outlives the test. Leaving it in place silences
    caplog in whatever runs next, which shows up as unrelated "*_is_logged"
    tests failing depending on file order.
    """
    config = Settings.from_env(env={}, log_level="CRITICAL", **settings_overrides)

    original_from_env = main_module.Settings.from_env
    original_configure = main_module.configure_logging
    main_module.Settings.from_env = staticmethod(lambda *a, **k: config)
    main_module.configure_logging = lambda *a, **k: None

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            main_module.main(argv)
    finally:
        main_module.Settings.from_env = original_from_env
        main_module.configure_logging = original_configure
    return buffer.getvalue()


# ----------------------------------------------------------------------
# The flag is off by default
# ----------------------------------------------------------------------


class TestTheFlagIsOptIn:
    def test_the_default_run_prints_no_advice(self):
        assert "advice" not in run_main()

    def test_the_default_run_does_not_announce_deliberation(self):
        assert "deliberation is ON" not in run_main()

    def test_main_is_still_callable_with_no_arguments(self):
        """Several existing tests drive main() directly. Adding argparse must
        not make it reach for pytest's own command line."""
        assert run_main() != ""

    def test_none_means_no_arguments_not_sys_argv(self):
        assert main_module.parse_args([]).deliberate is False

    def test_the_flag_turns_it_on(self):
        assert main_module.parse_args(["--deliberate"]).deliberate is True

    def test_the_flag_is_advertised_as_advisory(self):
        """The help text has to say what the flag does NOT do. Someone reading
        --help is deciding whether it is safe to turn on."""
        import argparse
        import contextlib

        buffer = io.StringIO()
        with contextlib.suppress(SystemExit), redirect_stdout(buffer):
            main_module.parse_args(["--help"])
        help_text = buffer.getvalue()

        assert "--deliberate" in help_text
        assert "never executed" in help_text


# ----------------------------------------------------------------------
# Half Brain declines honestly rather than failing
# ----------------------------------------------------------------------


class TestHalfBrainDeclinesHonestly:
    def test_it_prints_no_view(self):
        assert "NO VIEW" in run_main(["--deliberate"])

    def test_it_explains_why(self):
        assert "no reasoning capability is configured" in run_main(["--deliberate"])

    def test_it_is_not_an_error(self):
        """The Manager is not broken without a model - it says so and carries on."""
        output = run_main(["--deliberate"])
        assert "Traceback" not in output
        assert "status   : completed" in output

    def test_the_tasks_still_complete(self):
        output = run_main(["--deliberate"])
        assert output.count("status   : completed") >= 3

    def test_the_run_is_reported_as_half_brain(self):
        assert "HALF BRAIN" in run_main(["--deliberate"])

    def test_a_null_brain_decision_is_still_a_valid_decision(self):
        decision = manager_with().deliberate(Task(description="anything"))
        assert decision.action is BrainAction.UNKNOWN
        assert decision.reason


# ----------------------------------------------------------------------
# Full Brain actually produces advice
# ----------------------------------------------------------------------


class TestFullBrainProducesAdvice:
    def test_the_brain_is_consulted(self):
        provider = ScriptedProvider(ADVICE)
        manager = manager_with(brain=LLMManagerBrain(provider))
        manager.deliberate(Task(description="check my email"))
        assert provider.call_count == 1

    def test_the_advice_is_decided(self):
        manager = manager_with(brain=LLMManagerBrain(ScriptedProvider(ADVICE)))
        decision = manager.deliberate(Task(description="check my email"))
        assert decision.is_decided
        assert decision.action is BrainAction.DELEGATE

    def test_the_advice_carries_its_reasoning(self):
        manager = manager_with(brain=LLMManagerBrain(ScriptedProvider(ADVICE)))
        decision = manager.deliberate(Task(description="check my email"))
        assert "email agent" in decision.reason

    def test_the_advice_is_attributable(self):
        manager = manager_with(brain=LLMManagerBrain(ScriptedProvider(ADVICE)))
        decision = manager.deliberate(Task(description="check my email"))
        assert "scripted" in decision.source

    def test_such_a_manager_reads_as_full_brain(self):
        manager = manager_with(brain=LLMManagerBrain(ScriptedProvider(ADVICE)))
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

    def test_full_brain_is_no_longer_an_unexercised_claim(self):
        """The gap this milestone closes: a Full Brain Manager can now be
        asked for judgement through a real caller, not only through a test."""
        manager = manager_with(brain=LLMManagerBrain(ScriptedProvider(ADVICE)))
        profile = assess_manager(manager)

        assert "deliberation" in profile.capabilities
        assert manager.deliberate(Task(description="check my email")).is_decided


# ----------------------------------------------------------------------
# The safety boundary: advice is never executed
# ----------------------------------------------------------------------


class TestAdviceIsNeverExecuted:
    def test_a_delegate_recommendation_does_not_run_the_agent(self):
        """The brain names beta_agent; capability routing would pick
        alpha_agent for email work. The result must come from routing."""
        advice = json.dumps(
            {"action": "delegate", "reason": "try beta", "delegate_to": "beta_agent"}
        )
        manager = manager_with(brain=LLMManagerBrain(ScriptedProvider(advice)))

        result = manager.handle_task(Task(description="check my email"))
        decision = manager.deliberate(Task(description="check my email"))

        assert decision.delegate_to == "beta_agent"   # the advice
        assert result.agent_name == "alpha_agent"     # what actually happened

    def test_no_action_executor_is_imported_by_the_entry_point(self):
        source = open(main_module.__file__, encoding="utf-8").read()
        assert "ActionExecutor" not in source
        assert "actions" not in source

    def test_no_cognitive_loop_is_imported_by_the_entry_point(self):
        source = open(main_module.__file__, encoding="utf-8").read()
        assert "CognitiveLoop" not in source
        assert "cognitive_loop" not in source

    def test_the_output_states_the_boundary(self):
        assert "never executed" in run_main(["--deliberate"])

    def test_deliberating_causes_no_extra_execution(self, monkeypatch):
        """The load-bearing test of this milestone.

        Printed output alone cannot prove the boundary: advice that was acted
        on would re-run a task whose result was already printed, and the page
        would look identical. So count the executions instead - turning the
        flag on must not cause a single additional one.
        """
        calls: list[str] = []
        original = ManagerAgent.handle_task

        def counting(self, task):
            calls.append(task.description)
            return original(self, task)

        monkeypatch.setattr(ManagerAgent, "handle_task", counting)

        run_main()
        without = list(calls)
        calls.clear()
        run_main(["--deliberate"])
        with_flag = list(calls)

        assert with_flag == without
        assert len(with_flag) == len(set(with_flag)), "a task was executed twice"

    def test_no_execution_call_appears_inside_the_deliberate_branch(self):
        """Structural backstop for the same rule. Walks the ``if
        args.deliberate:`` blocks in main() and fails if anything that runs
        work is called inside one."""
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(main_module.main)))
        forbidden = {"handle_task", "execute", "take_action", "run", "tick"}
        found: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = ast.dump(node.test)
            if "deliberate" not in test:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    if inner.func.attr in forbidden:
                        found.append(inner.func.attr)

        assert found == [], f"execution inside the deliberate branch: {found}"

    def test_steps_are_printed_not_performed(self):
        """A brain proposing steps must not cause anything to happen."""
        decision = manager_with(
            brain=LLMManagerBrain(ScriptedProvider(ADVICE))
        ).deliberate(Task(description="check my email"))
        assert decision.steps == ("scan the inbox", "summarise what matters")


# ----------------------------------------------------------------------
# handle_task is unaffected
# ----------------------------------------------------------------------


class TestHandleTaskIsUnaffected:
    def test_the_data_loop_still_does_not_consult_the_brain(self):
        provider = ScriptedProvider(ADVICE)
        manager = manager_with(brain=LLMManagerBrain(provider))
        manager.handle_task(Task(description="check my email"))
        assert provider.call_count == 0

    def test_handle_task_source_is_unchanged(self):
        import inspect

        source = inspect.getsource(ManagerAgent.handle_task)
        assert "brain" not in source
        assert "deliberate" not in source

    def test_routing_is_identical_with_and_without_the_flag(self):
        without = [l for l in run_main().splitlines() if l.startswith("agent    :")]
        with_flag = [l for l in run_main(["--deliberate"]).splitlines() if l.startswith("agent    :")]
        assert without == with_flag

    def test_task_results_are_identical_with_and_without_the_flag(self):
        without = [l for l in run_main().splitlines() if l.startswith("status   :")]
        with_flag = [l for l in run_main(["--deliberate"]).splitlines() if l.startswith("status   :")]
        assert without == with_flag


# ----------------------------------------------------------------------
# A provider failure degrades rather than crashing
# ----------------------------------------------------------------------


class TestProviderFailureDegrades:
    def test_deliberation_returns_no_view_rather_than_raising(self):
        manager = manager_with(brain=LLMManagerBrain(FailingProvider()))
        decision = manager.deliberate(Task(description="check my email"))
        assert not decision.is_decided

    def test_the_reason_names_the_failure(self):
        manager = manager_with(brain=LLMManagerBrain(FailingProvider()))
        decision = manager.deliberate(Task(description="check my email"))
        assert "credit balance" in decision.reason

    def test_the_manager_still_completes_the_task(self):
        manager = manager_with(brain=LLMManagerBrain(FailingProvider()))
        result = manager.handle_task(Task(description="check my email"))
        assert result.status is TaskStatus.COMPLETED

    def test_a_failing_router_and_a_failing_brain_together_still_work(self):
        """Everything model-shaped is dead; the Manager keeps operating."""
        manager = manager_with(
            brain=LLMManagerBrain(FailingProvider()),
            router=FallbackRouter(
                primary=LLMRouter(FailingProvider()), fallback=CapabilityRouter()
            ),
        )
        result = manager.handle_task(Task(description="check my email"))
        decision = manager.deliberate(Task(description="check my email"))

        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "alpha_agent"
        assert not decision.is_decided
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN


# ----------------------------------------------------------------------
# Nothing reached the network
# ----------------------------------------------------------------------


class TestNothingReachedTheNetwork:
    def test_no_vendor_sdk_is_imported(self):
        import sys

        run_main(["--deliberate"])
        assert "anthropic" not in sys.modules

    def test_the_default_configuration_has_no_provider_to_call(self):
        """The default brain holds no provider at all, so deliberation cannot
        reach one even in principle."""
        manager = manager_with()
        assert isinstance(manager.brain, NullBrain)
        assert not hasattr(manager.brain, "provider_name")
        assert not manager.deliberate(Task(description="anything")).is_decided

    def test_no_api_key_is_required(self):
        assert "api_key=unset" in run_main(["--deliberate"])
