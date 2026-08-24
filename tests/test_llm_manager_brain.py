"""Tests for the LLM-backed ManagerBrain.

The central claim under test is architectural rather than behavioural: this
brain can recommend delegating to an agent, and has no ability whatsoever to
call that agent. See TestTheBrainOnlyThinks - the rest of this file exists to
support it.

No Anthropic, no network, no key. Every provider here is a scripted double.
"""

import json
import socket

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.brain import BrainAction, BrainDecision, BrainError, ManagerBrain
from src.manager.brain_prompts import (
    BRAIN_SYSTEM_PROMPT,
    build_deliberation_prompt,
    parse_decision,
    parse_decision_or_unknown,
)
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent, MissionOutcome
from src.providers.base import Completion, LLMProvider
from src.providers.null_provider import NullProvider
from src.registry.agent_registry import AgentRegistry


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------


class ScriptedProvider(LLMProvider):
    """Returns prepared answers and records exactly what it was asked."""

    def __init__(self, *answers, name="scripted", model="scripted-1", fail=None):
        self._answers = list(answers) or ["{}"]
        self._name = name
        self._model = model
        self._fail = fail
        self.prompts = []
        self.systems = []
        self.call_count = 0

    @property
    def name(self):
        return self._name

    def complete(self, prompt, *, system=None):
        self.call_count += 1
        self.prompts.append(prompt)
        self.systems.append(system)
        if self._fail is not None:
            raise self._fail
        answer = self._answers[min(self.call_count - 1, len(self._answers) - 1)]
        return Completion(text=answer, provider=self._name, model=self._model)


class SpyAgent(BaseAgent):
    """Records any attempt to execute it. Must never be called by a brain."""

    def __init__(self, name="email_agent", capabilities=("email", "opportunities")):
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
            description=f"handles {self._name} work",
            capabilities=self._capabilities,
        )

    def execute(self, task):
        self.executions.append(task)
        return TaskResult.success(task, agent_name=self._name, output="done")


def spec(name, description="does things", capabilities=("research",)):
    return AgentSpec(name=name, description=description, capabilities=capabilities)


ROSTER = (
    spec("email_agent", "reads and analyses mail", ("email", "opportunities")),
    spec("research_agent", "looks things up", ("research",)),
)


def a_task(description="find me freelance work this week"):
    return Task(id="t-1", description=description)


def answer(**fields):
    """A valid model response with the given fields overridden."""
    payload = {"action": "report", "reason": "there is something worth telling you"}
    payload.update(fields)
    return json.dumps(payload)


def brain_returning(*answers, **kwargs):
    return LLMManagerBrain(ScriptedProvider(*answers, **kwargs))


def deliberate(*answers, task=None, agents=ROSTER, notes=(), **kwargs):
    return brain_returning(*answers, **kwargs).deliberate(task or a_task(), agents, notes)


# ----------------------------------------------------------------------
# 1-4. Injection and prompt construction
# ----------------------------------------------------------------------


class TestTheProviderIsInjected:
    def test_a_fake_provider_can_be_injected(self):
        provider = ScriptedProvider(answer())
        brain = LLMManagerBrain(provider)
        brain.deliberate(a_task(), ROSTER)
        assert provider.call_count == 1

    def test_it_satisfies_the_brain_contract(self):
        assert isinstance(brain_returning(answer()), ManagerBrain)

    def test_it_never_constructs_a_provider_itself(self):
        with pytest.raises(TypeError):
            LLMManagerBrain()

    def test_the_name_reports_the_backing_provider(self):
        assert brain_returning(answer(), name="scripted").name == "llm:scripted"

    def test_it_works_with_the_default_null_provider(self):
        """No key, no model, no network - and an honest answer."""
        decision = LLMManagerBrain(NullProvider()).deliberate(a_task(), ROSTER)
        assert decision.action is BrainAction.UNKNOWN
        assert not decision.is_decided


class TestThePrompt:
    def test_the_provider_receives_the_manager_system_prompt(self):
        provider = ScriptedProvider(answer())
        LLMManagerBrain(provider).deliberate(a_task(), ROSTER)
        assert provider.systems[0] == BRAIN_SYSTEM_PROMPT

    def test_the_system_prompt_states_the_ceo_role(self):
        lowered = BRAIN_SYSTEM_PROMPT.lower()
        assert "chief executive" in lowered
        assert "delegate" in lowered

    def test_the_system_prompt_forbids_execution(self):
        """The model must not believe it is about to act."""
        lowered = BRAIN_SYSTEM_PROMPT.lower()
        assert "do not execute" in lowered or "you do not execute" in lowered
        assert "recommendation" in lowered

    def test_the_system_prompt_asks_for_honest_uncertainty(self):
        lowered = BRAIN_SYSTEM_PROMPT.lower()
        assert "unknown" in lowered and "uncertainty" in lowered

    def test_the_task_appears_in_the_user_prompt(self):
        provider = ScriptedProvider(answer())
        LLMManagerBrain(provider).deliberate(a_task("summarise my inbox"), ROSTER)
        assert "summarise my inbox" in provider.prompts[0]

    def test_every_available_agent_appears_in_the_prompt(self):
        provider = ScriptedProvider(answer())
        LLMManagerBrain(provider).deliberate(a_task(), ROSTER)
        prompt = provider.prompts[0]
        for agent in ROSTER:
            assert agent.name in prompt
            assert agent.description in prompt

    def test_agent_capabilities_appear_in_the_prompt(self):
        prompt = build_deliberation_prompt(a_task(), ROSTER)
        assert "opportunities" in prompt and "research" in prompt

    def test_notes_appear_in_the_prompt(self):
        prompt = build_deliberation_prompt(a_task(), ROSTER, ["the last attempt timed out"])
        assert "the last attempt timed out" in prompt

    def test_an_empty_roster_is_stated_not_hidden(self):
        prompt = build_deliberation_prompt(a_task(), ())
        assert "none are available" in prompt

    def test_the_prompt_is_deterministic(self):
        """Same inputs, byte-identical prompt - regardless of roster order."""
        forwards = build_deliberation_prompt(a_task(), ROSTER)
        backwards = build_deliberation_prompt(a_task(), tuple(reversed(ROSTER)))
        assert forwards == backwards

    def test_the_task_payload_is_not_sent(self):
        """It may carry credentials; a decision does not need it."""
        task = Task(id="t-9", description="check my mail", payload={"token": "s3cret"})
        prompt = build_deliberation_prompt(task, ROSTER)
        assert "s3cret" not in prompt

    def test_building_a_prompt_touches_no_provider(self):
        assert build_deliberation_prompt(a_task(), ROSTER)  # pure function, no I/O


# ----------------------------------------------------------------------
# 5-10. Valid responses become the right decision
# ----------------------------------------------------------------------


class TestValidResponsesParse:
    def test_handle_self_parses(self):
        decision = deliberate(answer(action="handle_self", reason="routine coordination"))
        assert decision.action is BrainAction.HANDLE_SELF
        assert decision.reason == "routine coordination"

    def test_delegate_parses(self):
        decision = deliberate(
            answer(action="delegate", reason="mail work", delegate_to="email_agent")
        )
        assert decision.action is BrainAction.DELEGATE
        assert decision.delegate_to == "email_agent"

    def test_report_parses(self):
        decision = deliberate(answer(action="report", reason="worth telling you"))
        assert decision.action is BrainAction.REPORT

    def test_escalate_parses(self):
        decision = deliberate(answer(action="escalate", reason="this needs your authority"))
        assert decision.action is BrainAction.ESCALATE

    def test_unknown_parses(self):
        decision = deliberate(answer(action="unknown", reason="not enough context"))
        assert decision.action is BrainAction.UNKNOWN
        assert not decision.is_decided

    def test_uppercase_actions_are_accepted(self):
        """The schema shows lowercase; models routinely shout."""
        decision = deliberate(
            answer(action="DELEGATE", reason="mail work", delegate_to="email_agent")
        )
        assert decision.action is BrainAction.DELEGATE

    def test_confidence_and_steps_are_carried_through(self):
        decision = deliberate(
            answer(
                action="report",
                reason="worth telling you",
                confidence=0.75,
                steps=["read the summary", "decide whether to apply"],
            )
        )
        assert decision.confidence == 0.75
        assert decision.steps == ("read the summary", "decide whether to apply")

    def test_a_fenced_response_is_read(self):
        """Markdown fences are packaging, not content."""
        fenced = f"Here is my answer:\n```json\n{answer(action='report')}\n```"
        assert deliberate(fenced).action is BrainAction.REPORT

    def test_provenance_comes_from_the_completion(self):
        decision = deliberate(answer(), name="scripted", model="scripted-1")
        assert decision.source == "llm:scripted-1"

    def test_the_model_cannot_claim_its_own_provenance(self):
        """Same rule parse_judgement enforces: provenance is caller-owned."""
        decision = deliberate(answer(source="a-more-impressive-model"))
        assert decision.source == "llm:scripted-1"

    def test_only_one_model_call_is_made(self):
        provider = ScriptedProvider(answer())
        LLMManagerBrain(provider).deliberate(a_task(), ROSTER)
        assert provider.call_count == 1


# ----------------------------------------------------------------------
# 11-15. Bad responses degrade honestly
# ----------------------------------------------------------------------


class TestMalformedResponsesBecomeUnknown:
    @pytest.mark.parametrize(
        "response",
        [
            "{broken json",
            "",
            "   ",
            "I think you should check your email.",
            "[]",
            '{"action": "report"}',                      # no reason
            '{"reason": "because"}',                     # no action
            '{"action": "report", "reason": "  "}',      # blank reason
        ],
    )
    def test_unusable_responses_become_unknown(self, response):
        decision = deliberate(response)
        assert decision.action is BrainAction.UNKNOWN
        assert not decision.is_decided

    def test_an_invalid_action_becomes_unknown(self):
        decision = deliberate(answer(action="panic"))
        assert decision.action is BrainAction.UNKNOWN
        assert "panic" in decision.reason

    @pytest.mark.parametrize("bad", [1.5, -0.2, 42, "very high", True])
    def test_invalid_confidence_becomes_unknown(self, bad):
        decision = deliberate(answer(confidence=bad))
        assert decision.action is BrainAction.UNKNOWN
        assert decision.confidence == 0.0

    def test_delegate_with_no_target_becomes_unknown(self):
        decision = deliberate(answer(action="delegate", reason="someone else should"))
        assert decision.action is BrainAction.UNKNOWN
        assert decision.delegate_to is None

    def test_delegate_to_an_unregistered_agent_becomes_unknown(self):
        """A model must not be able to invent an agent into existence."""
        decision = deliberate(
            answer(action="delegate", reason="x", delegate_to="ghost_agent")
        )
        assert decision.action is BrainAction.UNKNOWN
        assert "ghost_agent" in decision.reason
        assert decision.delegate_to is None

    def test_a_provider_failure_becomes_unknown(self):
        decision = deliberate(answer(), fail=RuntimeError("quota exceeded"))
        assert decision.action is BrainAction.UNKNOWN
        assert "quota exceeded" in decision.reason

    def test_a_failure_is_still_attributable(self):
        decision = deliberate(answer(), fail=RuntimeError("boom"), name="scripted")
        assert decision.source == "llm:scripted"

    @pytest.mark.parametrize(
        "failure", [ValueError("bad"), TimeoutError("slow"), MemoryError()]
    )
    def test_no_provider_exception_escapes(self, failure):
        assert deliberate(answer(), fail=failure).action is BrainAction.UNKNOWN

    def test_a_keyboard_interrupt_is_deliberately_not_swallowed(self):
        """Ctrl-C must still work. `except Exception` is the right catch here;
        `except BaseException` would make the process unkillable mid-call."""
        with pytest.raises(KeyboardInterrupt):
            deliberate(answer(), fail=KeyboardInterrupt())

    def test_every_failure_explains_itself(self):
        for response in ("{broken", "", answer(action="nonsense")):
            assert deliberate(response).reason.strip()

    def test_no_confident_guess_is_ever_invented(self):
        """An honest 'I don't know' beats a plausible-looking recommendation."""
        for response in ("{broken", "", answer(action="nonsense")):
            decision = deliberate(response)
            assert decision.confidence == 0.0
            assert decision.steps == ()


class TestTheStrictParser:
    """The strict parser exists for callers that want to know a response failed."""

    def test_it_raises_on_unusable_input(self):
        with pytest.raises(BrainError):
            parse_decision("{broken", valid_names={"email_agent"})

    def test_it_raises_on_an_unregistered_target(self):
        with pytest.raises(BrainError):
            parse_decision(
                answer(action="delegate", reason="x", delegate_to="ghost"),
                valid_names={"email_agent"},
            )

    def test_the_forgiving_parser_does_not_raise(self):
        assert parse_decision_or_unknown("{broken").action is BrainAction.UNKNOWN

    def test_a_target_resolves_to_the_registered_spelling(self):
        """Downstream only ever sees names the registry knows."""
        decision = parse_decision(
            answer(action="delegate", reason="x", delegate_to="EMAIL_AGENT"),
            valid_names={"email_agent"},
        )
        assert decision.delegate_to == "email_agent"


# ----------------------------------------------------------------------
# THE ARCHITECTURAL TEST: the brain only thinks
# ----------------------------------------------------------------------


class TestTheBrainOnlyThinks:
    """The claim this whole module exists to guarantee."""

    def test_a_delegate_recommendation_does_not_call_the_agent(self):
        agent = SpyAgent("email_agent")
        brain = brain_returning(
            answer(action="delegate", reason="mail work", delegate_to="email_agent")
        )

        decision = brain.deliberate(a_task(), (agent.spec,))

        assert decision.action is BrainAction.DELEGATE
        assert decision.delegate_to == "email_agent"
        assert agent.executions == []          # <- the point

    def test_the_brain_returns_a_decision_and_nothing_else(self):
        decision = deliberate(
            answer(action="delegate", reason="mail work", delegate_to="email_agent")
        )
        assert isinstance(decision, BrainDecision)
        assert not isinstance(decision, TaskResult)

    def test_the_brain_receives_specs_not_agents(self):
        """It is handed descriptions of agents, never anything executable."""
        agent = SpyAgent("email_agent")
        provider = ScriptedProvider(answer())
        LLMManagerBrain(provider).deliberate(a_task(), (agent.spec,))
        assert not hasattr(agent.spec, "execute")

    def test_the_brain_cannot_reach_a_registry(self):
        brain = brain_returning(answer())
        for attribute in ("registry", "agents", "router", "assessor", "_registry"):
            assert not hasattr(brain, attribute), attribute

    def test_the_brain_exposes_no_way_to_act(self):
        public = {n for n in dir(LLMManagerBrain) if not n.startswith("_")}
        forbidden = {"execute", "run", "delegate", "handle_task", "send", "route", "assess"}
        assert public.isdisjoint(forbidden)

    def test_a_delegate_recommendation_through_the_manager_executes_nothing(self):
        """End to end: Manager.deliberate() consults, and stops there."""
        agent = SpyAgent("email_agent")
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(
            registry=registry,
            brain=brain_returning(
                answer(action="delegate", reason="mail work", delegate_to="email_agent")
            ),
        )

        decision = manager.deliberate(a_task())

        assert decision.action is BrainAction.DELEGATE
        assert decision.delegate_to == "email_agent"
        assert agent.executions == []          # <- the point, one layer up


# ----------------------------------------------------------------------
# 19-21. Nothing else changed
# ----------------------------------------------------------------------


class TestExistingBehaviourIsUntouched:
    def _manager(self):
        registry = AgentRegistry()
        registry.register(SpyAgent("email_agent"))
        return registry

    def test_routing_is_unaffected_by_the_brain(self):
        """The brain says escalate; the router routes anyway."""
        registry = self._manager()
        manager = ManagerAgent(
            registry=registry,
            brain=brain_returning(answer(action="escalate", reason="ask the human")),
        )
        result = manager.handle_task(Task(id="t-2", description="check my email"))
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "email_agent"

    def test_the_brain_is_not_consulted_during_handle_task(self):
        provider = ScriptedProvider(answer())
        manager = ManagerAgent(registry=self._manager(), brain=LLMManagerBrain(provider))
        manager.handle_task(Task(id="t-3", description="check my email"))
        assert provider.call_count == 0

    def test_assessment_is_unaffected_by_the_brain(self):
        manager = ManagerAgent(
            registry=self._manager(),
            brain=brain_returning(answer(action="report", reason="looks great", confidence=1.0)),
        )
        task = Task(id="t-4", description="check my email")
        failed = TaskResult.failure(task, agent_name="email_agent", error="upstream down")
        assert manager.review_mission(task, failed) is MissionOutcome.RETRY

    def test_the_brain_remains_independently_testable(self):
        """No Manager, no registry, no router - just a brain and a fake."""
        decision = LLMManagerBrain(ScriptedProvider(answer())).deliberate(a_task())
        assert decision.action is BrainAction.REPORT

    def test_no_persistent_memory_is_introduced(self):
        """Two identical calls; the second knows nothing of the first."""
        provider = ScriptedProvider(answer())
        brain = LLMManagerBrain(provider)
        brain.deliberate(a_task(), ROSTER)
        brain.deliberate(a_task(), ROSTER)
        assert provider.prompts[0] == provider.prompts[1]

    def test_the_brain_holds_no_state_between_calls(self):
        """No conversation, no history, no accumulated context.

        ``_availability`` is excluded deliberately. It records whether the last
        call to the model succeeded, which is an observation about the
        *provider* rather than state about the conversation: nothing in
        deliberate() reads it, so it cannot change an answer. Recording it is
        what stops Full Brain being claimed for a model nobody has managed to
        reach.
        """
        ignored = {"_availability"}
        brain = brain_returning(answer())
        before = {k: v for k, v in vars(brain).items() if k not in ignored}
        brain.deliberate(a_task(), ROSTER)
        after = {k: v for k, v in vars(brain).items() if k not in ignored}
        assert after == before


# ----------------------------------------------------------------------
# 16-17. No vendor coupling, no network
# ----------------------------------------------------------------------


class TestNoVendorCouplingOrNetwork:
    def _source(self, module):
        return open(module.__file__, encoding="utf-8").read().lower()

    def test_no_anthropic_sdk_is_imported(self):
        import sys

        deliberate(answer())
        assert "anthropic" not in sys.modules

    def test_the_brain_module_names_no_vendor(self):
        import src.manager.llm_brain as module

        source = self._source(module)
        for word in ("anthropic", "openai", "api_key", "requests", "httpx"):
            assert word not in source, word

    def test_the_brain_never_imports_a_concrete_provider(self):
        import src.manager.llm_brain as module

        source = self._source(module)
        assert "anthropic_provider" not in source
        assert "null_provider" not in source
        assert "from src.providers.base import llmprovider" in source

    def test_the_brain_imports_no_orchestration_machinery(self):
        import src.manager.llm_brain as module

        source = self._source(module)
        for forbidden in (
            "from src.registry",
            "from src.manager.router",
            "from src.manager.assessor",
            "from src.services.email",
            "from src.agents.email",
        ):
            assert forbidden not in source, forbidden

    def test_the_brain_reaches_no_specialist_domain(self):
        import src.manager.brain_prompts as prompts_module

        source = self._source(prompts_module)
        # Import statements, not prose - the docstring legitimately discusses
        # credentials when explaining why task.payload is withheld.
        for word in ("gmail", "smtp", "inbox", "opportunity", "from src.agents"):
            assert word not in source, word

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert deliberate(answer()).action is BrainAction.REPORT

    def test_the_manager_still_names_no_provider(self):
        """The existing structural guard must survive this layer."""
        import src.manager.manager as manager_module

        assert "provider" not in self._source(manager_module)


# ----------------------------------------------------------------------
# Provider injection, proven at runtime rather than by reading imports
# ----------------------------------------------------------------------


class TestTheProviderIsTrulyInjected:
    """The dependency direction, demonstrated rather than asserted.

        ManagerBrain -> LLMProvider -> whichever implementation was supplied

    Reading the import list shows only that a vendor is absent. These prove the
    positive: the brain uses exactly the object it was handed, and swapping
    that object is the only thing that changes what comes out.
    """

    def test_two_providers_produce_two_different_decisions(self):
        """Nothing about the outcome is baked into the brain."""
        escalating = brain_returning(
            answer(action="escalate", reason="this needs the person's authority")
        ).deliberate(a_task(), ROSTER)

        delegating = brain_returning(
            answer(action="delegate", reason="a specialist fits", delegate_to="email_agent")
        ).deliberate(a_task(), ROSTER)

        assert escalating.action is BrainAction.ESCALATE
        assert delegating.action is BrainAction.DELEGATE
        assert delegating.delegate_to == "email_agent"

    def test_the_same_prompt_through_two_providers_differs_only_by_provider(self):
        one = ScriptedProvider(answer(), name="one", model="model-one")
        two = ScriptedProvider(answer(), name="two", model="model-two")

        LLMManagerBrain(one).deliberate(a_task(), ROSTER)
        LLMManagerBrain(two).deliberate(a_task(), ROSTER)

        assert one.prompts[0] == two.prompts[0]      # identical question
        assert one.systems[0] == two.systems[0]

    def test_provenance_distinguishes_the_two(self):
        one = brain_returning(answer(), name="one", model="model-one")
        two = brain_returning(answer(), name="two", model="model-two")

        assert one.deliberate(a_task(), ROSTER).source == "llm:model-one"
        assert two.deliberate(a_task(), ROSTER).source == "llm:model-two"

    def test_a_provider_without_a_model_falls_back_to_its_name(self):
        """NullProvider reports no model; provenance must still be recorded."""
        decision = LLMManagerBrain(NullProvider()).deliberate(a_task(), ROSTER)
        assert decision.source == "llm:null"

    def test_the_null_provider_needs_no_configuration(self):
        brain = LLMManagerBrain(NullProvider())
        assert brain.provider_name == "null"
        assert not brain.deliberate(a_task(), ROSTER).is_decided

    def test_any_llm_provider_implementation_is_accepted(self):
        """Written from scratch against the interface - no inheritance tricks."""

        class MinimalProvider(LLMProvider):
            @property
            def name(self):
                return "minimal"

            def complete(self, prompt, *, system=None):
                return Completion(text=answer(action="report"), provider=self.name)

        decision = LLMManagerBrain(MinimalProvider()).deliberate(a_task(), ROSTER)
        assert decision.action is BrainAction.REPORT
        assert decision.source == "llm:minimal"

    def test_no_environment_variable_is_read(self):
        """No key, no configuration lookup - proven at runtime, not by grep.

        A brain that quietly reached for ANTHROPIC_API_KEY would still pass an
        import check, so the environment itself is watched during a full
        deliberation.
        """
        import os

        class WatchedEnviron(dict):
            def __init__(self, source):
                super().__init__(source)
                self.reads = []

            def __getitem__(self, key):
                self.reads.append(key)
                return super().__getitem__(key)

            def get(self, key, default=None):
                self.reads.append(key)
                return super().get(key, default)

        watched = WatchedEnviron(os.environ)
        real_environ = os.environ
        os.environ = watched
        try:
            deliberate(answer())
        finally:
            os.environ = real_environ

        assert watched.reads == []

    def test_the_brain_never_touches_a_memory_store(self):
        """Structural: memory reaches it as notes, never as an object.

        The behavioural tests show it does not write during the paths they
        exercise; this shows there is no call to a store anywhere in the file.
        """
        import ast

        import src.manager.llm_brain as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("remember", "recall", "forget", "clear"), (
                    node.func.attr
                )

    def test_the_brain_imports_no_memory_module(self):
        import src.manager.llm_brain as module

        source = self._source(module)
        assert "from src.services.memory" not in source
        assert "memorystore" not in source
        assert "ephemeralmemory" not in source

    def _source(self, module):
        return open(module.__file__, encoding="utf-8").read().lower()

    def test_it_depends_on_the_abstraction_only(self):
        """The interface and value types - never a concrete provider.

        The rule being protected is that the brain cannot reach a vendor: it
        holds an ``LLMProvider`` and knows nothing about who implements it.
        ``availability`` is a frozen record describing what the last call did,
        with no client, no credential and no vendor in it, so importing it
        does not weaken that - importing anthropic_provider or null_provider
        would, and this fails if either ever appears.
        """
        import src.manager.llm_brain as module

        source = open(module.__file__, encoding="utf-8").read()
        provider_imports = [
            line.strip()
            for line in source.splitlines()
            if line.startswith(("import ", "from ")) and "providers" in line
        ]
        assert "from src.providers.base import LLMProvider" in provider_imports
        for line in provider_imports:
            assert "_provider" not in line, line
            assert "anthropic" not in line.lower(), line

    def test_the_factory_supplies_the_provider(self):
        """The composition root injects; the brain never reaches for one."""
        from src.config.settings import Settings
        from src.manager.brain_factory import build_brain
        from src.providers.factory import build_provider

        config = Settings.from_env(env={}, brain="llm")
        provider = build_provider(config)
        brain = build_brain(config, provider)

        assert isinstance(brain, LLMManagerBrain)
        assert brain.provider_name == provider.name
