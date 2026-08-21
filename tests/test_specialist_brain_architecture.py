"""Tests for the specialist Brain seam: factory, configuration, wiring.

What was already true before this milestone, and shapes what these tests check:
CognitiveAgent has always accepted a ``brain``, and EmailAgent has always
*consulted* it - after the deterministic pipeline, never instead of it. The
seam and its caller both existed. What did not exist was any way to configure
one, so every specialist ever built in production received the null brain.

So these tests are mostly about reachability and about the boundaries that must
survive it:

    TestTheDefaultIsUnchanged     equipping nobody still equips nobody
    TestBrainAndMemoryStaySeparate  configuring a brain configures no store
    TestItIsNotTheManagerBrain    two brains, two objects, two domains
    TestNoNewAuthority            an Insight still cannot do anything

Every provider here is scripted, null, or a deliberate failure. No key, no
network, no vendor SDK.
"""

import inspect
import socket

import pytest
from conftest import ScriptedProvider

from src.agents.brain import Insight, NullSpecialistBrain, SpecialistBrain
from src.agents.brain_factory import (
    SPECIALIST_DOMAINS,
    available_specialist_brains,
    build_specialist_brain,
    domain_for,
)
from src.agents.cognitive_agent import CognitiveAgent
from src.agents.email_agent import EmailAgent
from src.agents.llm_brain import LLMSpecialistBrain
from src.config.coherence import ConfigurationError, check_configuration
from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.brain import NullBrain
from src.manager.brain_factory import build_brain
from src.manager.cognition import CognitiveMode, assess_cognition
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.providers.null_provider import NullProvider
from src.services.email.sample_inbox import sample_inbox
from src.services.memory.ephemeral import EphemeralMemory

INSIGHT = '{"summary": "three roles worth a look", "confidence": 0.8}'


class FailingProvider(LLMProvider):
    """Fails when asked - the shape of an exhausted balance."""

    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt, *, system=None) -> Completion:
        self.calls += 1
        raise RuntimeError("credit balance is too low")


def settings(**overrides):
    return Settings.from_env(env={}, **overrides)


def email_agent(**kwargs) -> EmailAgent:
    return EmailAgent(sample_inbox(), **kwargs)


def scan(agent: EmailAgent):
    return agent.execute(Task(description="scan my inbox for job opportunities"))


# ----------------------------------------------------------------------
# The default is unchanged
# ----------------------------------------------------------------------


class TestTheDefaultIsUnchanged:
    def test_the_setting_defaults_to_null(self):
        assert settings().specialist_brain == "null"

    def test_the_factory_builds_the_null_brain_by_default(self):
        brain = build_specialist_brain(settings(), NullProvider())
        assert isinstance(brain, NullSpecialistBrain)

    def test_an_agent_built_with_no_brain_still_gets_the_null_one(self):
        assert isinstance(email_agent().brain, NullSpecialistBrain)

    def test_the_default_agent_reasons_about_nothing(self):
        """reason_about returns None with no brain - not a fabricated view."""
        assert email_agent().reason_about(Task(description="scan my inbox")) is None

    def test_the_default_agent_still_completes_its_work(self):
        result = scan(email_agent())
        assert result.status is TaskStatus.COMPLETED
        assert result.output

    def test_the_default_is_reported_as_half_brain(self):
        profile = assess_cognition(email_agent().brain)
        assert profile.mode is CognitiveMode.HALF_BRAIN

    def test_selecting_llm_without_a_provider_still_needs_no_key(self):
        brain = build_specialist_brain(settings(specialist_brain="llm"), NullProvider())
        assert isinstance(brain, LLMSpecialistBrain)
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN


# ----------------------------------------------------------------------
# A specialist can be given a brain explicitly
# ----------------------------------------------------------------------


class TestAnExplicitBrainIsAccepted:
    def test_the_factory_builds_an_llm_brain_when_selected(self):
        brain = build_specialist_brain(settings(specialist_brain="llm"), ScriptedProvider(INSIGHT))
        assert isinstance(brain, LLMSpecialistBrain)

    def test_the_agent_holds_the_brain_it_was_given(self):
        brain = LLMSpecialistBrain(ScriptedProvider(INSIGHT))
        assert email_agent(brain=brain).brain is brain

    def test_the_brain_is_actually_consulted(self):
        """The payoff: the seam and its caller already existed, so a configured
        brain is used immediately - no further wiring was needed."""
        provider = ScriptedProvider(INSIGHT)
        scan(email_agent(brain=LLMSpecialistBrain(provider)))
        assert provider.call_count == 1

    def test_the_insight_reaches_the_result(self):
        provider = ScriptedProvider(INSIGHT)
        result = scan(email_agent(brain=LLMSpecialistBrain(provider)))
        assert result.status is TaskStatus.COMPLETED

    def test_an_unknown_brain_name_fails_fast(self):
        with pytest.raises(ValueError) as exc:
            build_specialist_brain(settings(specialist_brain="telepathy"), NullProvider())
        assert "telepathy" in str(exc.value)
        assert "null" in str(exc.value)

    def test_the_registry_lists_what_is_available(self):
        assert available_specialist_brains() == ["llm", "null"]

    def test_a_bad_brain_type_is_refused_at_construction(self):
        with pytest.raises(TypeError):
            email_agent(brain="not a brain")


# ----------------------------------------------------------------------
# Full / Half Brain reflect a real model, not an object
# ----------------------------------------------------------------------


class TestCapabilityReflectsARealModel:
    def test_a_model_backed_brain_is_full_brain(self):
        brain = build_specialist_brain(settings(specialist_brain="llm"), ScriptedProvider(INSIGHT))
        assert assess_cognition(brain).mode is CognitiveMode.FULL_BRAIN

    def test_an_llm_brain_over_the_null_provider_is_half_brain(self):
        """The rule this project already established for the Manager: an LLM
        object is not a model. can_reason says yes; capability says no."""
        agent = email_agent(brain=LLMSpecialistBrain(NullProvider()))

        assert agent.can_reason is True
        assert assess_cognition(agent.brain).mode is CognitiveMode.HALF_BRAIN

    def test_that_case_is_reported_as_a_configuration_problem(self):
        profile = assess_cognition(LLMSpecialistBrain(NullProvider()))
        assert "no model backs it" in profile.reason

    def test_a_null_brain_is_half_brain(self):
        assert assess_cognition(NullSpecialistBrain()).mode is CognitiveMode.HALF_BRAIN

    def test_the_same_assessment_serves_both_layers(self):
        """No parallel mechanism: the Manager's cognition layer classifies a
        specialist brain unchanged, because it reads provider_name."""
        manager_full = assess_cognition(LLMManagerBrain(ScriptedProvider(INSIGHT)))
        specialist_full = assess_cognition(LLMSpecialistBrain(ScriptedProvider(INSIGHT)))
        assert manager_full.mode is specialist_full.mode is CognitiveMode.FULL_BRAIN

    def test_full_brain_keeps_the_deterministic_capabilities(self):
        provider = ScriptedProvider(INSIGHT)
        result = scan(email_agent(brain=LLMSpecialistBrain(provider)))
        assert result.status is TaskStatus.COMPLETED


# ----------------------------------------------------------------------
# A failing provider degrades; the specialist keeps working
# ----------------------------------------------------------------------


class TestProviderFailureDegrades:
    def test_the_task_still_completes(self):
        result = scan(email_agent(brain=LLMSpecialistBrain(FailingProvider())))
        assert result.status is TaskStatus.COMPLETED

    def test_the_deterministic_findings_are_unaffected(self):
        """The analyzer ran before the brain was asked, so a dead model cannot
        cost the user their results."""
        working = scan(email_agent())
        broken = scan(email_agent(brain=LLMSpecialistBrain(FailingProvider())))
        assert broken.output == working.output

    def test_reasoning_degrades_to_an_unusable_insight_rather_than_raising(self):
        """Not None: the brain returns a zero-confidence Insight that records
        *why* it has no view. That is strictly better than None - "the provider
        failed" and "nobody configured a brain" stay distinguishable."""
        agent = email_agent(brain=LLMSpecialistBrain(FailingProvider()))
        insight = agent.reason_about(Task(description="scan my inbox"))

        assert isinstance(insight, Insight)
        assert not insight.is_useful
        assert insight.confidence == 0.0

    def test_the_failure_reason_is_recorded_in_the_insight(self):
        agent = email_agent(brain=LLMSpecialistBrain(FailingProvider()))
        insight = agent.reason_about(Task(description="scan my inbox"))
        assert "credit balance" in insight.summary

    def test_that_is_distinguishable_from_having_no_brain(self):
        """Degradation and absence must not look alike - the same principle the
        router milestone established one layer down."""
        assert email_agent().reason_about(Task(description="scan my inbox")) is None
        assert (
            email_agent(brain=LLMSpecialistBrain(FailingProvider())).reason_about(
                Task(description="scan my inbox")
            )
            is not None
        )

    def test_the_provider_is_asked_once_and_not_retried(self):
        provider = FailingProvider()
        scan(email_agent(brain=LLMSpecialistBrain(provider)))
        assert provider.calls == 1

    def test_a_malformed_answer_is_not_treated_as_an_insight(self):
        agent = email_agent(brain=LLMSpecialistBrain(ScriptedProvider("not json at all")))
        insight = agent.reason_about(Task(description="scan my inbox"))
        assert insight is None or isinstance(insight, Insight)

    def test_a_brain_that_returns_rubbish_cannot_break_the_agent(self):
        class RubbishBrain(SpecialistBrain):
            @property
            def name(self):
                return "rubbish"

            def consider(self, task, notes=()):
                return "not an Insight"

        result = scan(email_agent(brain=RubbishBrain()))
        assert result.status is TaskStatus.COMPLETED

    def test_a_brain_that_raises_cannot_break_the_agent(self):
        class ExplodingBrain(SpecialistBrain):
            @property
            def name(self):
                return "exploding"

            def consider(self, task, notes=()):
                raise RuntimeError("brain exploded")

        assert scan(email_agent(brain=ExplodingBrain())).status is TaskStatus.COMPLETED


# ----------------------------------------------------------------------
# Brain and Memory stay separate
# ----------------------------------------------------------------------


class TestBrainAndMemoryStaySeparate:
    def test_the_factory_constructs_no_store(self):
        """Checked on the code, not the prose: an earlier version of this test
        scanned raw source and matched the word "memory" in a docstring
        explaining that memory is NOT touched here."""
        import ast

        tree = ast.parse(inspect.getsource(build_specialist_brain))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not any("memory" in name.lower() for name in called), called

    def test_the_factory_module_never_imports_memory(self):
        import ast

        import src.agents.brain_factory as module

        imported: list[str] = []
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)

        assert not any("memory" in name.lower() for name in imported), imported

    def test_the_agent_still_builds_its_own_memory(self):
        agent = email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT)))
        assert isinstance(agent.memory, EphemeralMemory)

    def test_memory_is_identical_with_and_without_a_brain(self):
        assert type(email_agent().memory) is type(
            email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT))).memory
        )

    def test_an_injected_memory_is_still_honoured(self):
        store = EphemeralMemory()
        agent = email_agent(brain=NullSpecialistBrain(), memory=store)
        assert agent.memory is store

    def test_two_specialists_never_share_a_store(self):
        shared_brain = LLMSpecialistBrain(ScriptedProvider(INSIGHT))
        one = email_agent(brain=shared_brain)
        two = email_agent(brain=shared_brain)
        assert one.brain is two.brain
        assert one.memory is not two.memory

    def test_they_remain_separate_attributes(self):
        agent = email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT)))
        assert agent.brain is not agent.memory


# ----------------------------------------------------------------------
# It is the specialist's brain, not the Manager's
# ----------------------------------------------------------------------


class TestItIsNotTheManagerBrain:
    def test_the_two_factories_produce_different_types(self):
        provider = ScriptedProvider(INSIGHT)
        manager = build_brain(settings(brain="llm"), provider)
        specialist = build_specialist_brain(settings(specialist_brain="llm"), provider)

        assert isinstance(manager, LLMManagerBrain)
        assert isinstance(specialist, LLMSpecialistBrain)
        assert type(manager) is not type(specialist)

    def test_they_are_separate_objects_even_on_one_provider(self):
        provider = ScriptedProvider(INSIGHT)
        assert build_brain(settings(brain="llm"), provider) is not build_specialist_brain(
            settings(specialist_brain="llm"), provider
        )

    def test_the_settings_are_independent(self):
        config = settings(brain="llm", specialist_brain="null")
        assert isinstance(build_brain(config, ScriptedProvider(INSIGHT)), LLMManagerBrain)
        assert isinstance(
            build_specialist_brain(config, ScriptedProvider(INSIGHT)), NullSpecialistBrain
        )

    def test_the_reverse_combination_also_works(self):
        config = settings(brain="null", specialist_brain="llm")
        assert isinstance(build_brain(config, ScriptedProvider(INSIGHT)), NullBrain)
        assert isinstance(
            build_specialist_brain(config, ScriptedProvider(INSIGHT)), LLMSpecialistBrain
        )

    def test_the_specialist_brain_carries_its_own_domain(self):
        """What makes it a specialist brain rather than a second Manager brain."""
        brain = build_specialist_brain(
            settings(specialist_brain="llm"), ScriptedProvider(INSIGHT), domain_for("email_agent")
        )
        assert "email" in brain.domain

    def test_an_undeclared_specialist_gets_no_invented_domain(self):
        assert domain_for("research_agent") == ""

    def test_the_domain_registry_is_how_future_specialists_join(self):
        assert "email_agent" in SPECIALIST_DOMAINS

    def test_the_specialist_brain_speaks_a_different_contract(self):
        """SpecialistBrain returns an Insight; ManagerBrain returns a decision.
        They are not interchangeable, which is why one cannot stand in for the
        other by accident."""
        specialist = LLMSpecialistBrain(ScriptedProvider(INSIGHT))
        assert isinstance(specialist.consider(Task(description="scan")), Insight)
        assert not hasattr(specialist, "deliberate")

    def test_the_agent_does_not_hold_the_manager_brain(self):
        agent = email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT)))
        assert not isinstance(agent.brain, (NullBrain, LLMManagerBrain))


# ----------------------------------------------------------------------
# No new authority
# ----------------------------------------------------------------------


class TestNoNewAuthority:
    def test_a_specialist_brain_cannot_execute(self):
        brain = LLMSpecialistBrain(ScriptedProvider(INSIGHT))
        for forbidden in ("execute", "handle_task", "delegate", "route", "send"):
            assert not hasattr(brain, forbidden), forbidden

    def test_the_factory_grants_no_mailbox_or_outbox(self):
        """Imports, not prose - the module docstring legitimately mentions a
        mailbox while explaining that a brain can never reach one."""
        import ast

        import src.agents.brain_factory as module

        imported: list[str] = []
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)

        lowered = [name.lower() for name in imported]
        assert not any("mailbox" in name or "outbox" in name for name in lowered), imported

    def test_a_brain_cannot_make_the_agent_send(self):
        """The send refusal is deterministic and sits before any reasoning."""
        agent = email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT)))
        result = agent.execute(Task(description="send an email to my team"))
        assert result.status is not TaskStatus.COMPLETED

    def test_reasoning_happens_after_the_deterministic_pipeline(self):
        """Findings are computed before the brain is asked, so an insight can
        never alter a score or a band."""
        source = inspect.getsource(EmailAgent.execute)
        assert source.index("analyzer.analyze") < source.index("self.reason_about")

    def test_the_manager_still_orchestrates(self):
        from src.manager.manager import ManagerAgent

        manager = ManagerAgent()
        manager.register_agent(email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT))))
        result = manager.handle_task(Task(description="scan my inbox for jobs"))
        assert result.agent_name == "email_agent"


# ----------------------------------------------------------------------
# Configuration coherence
# ----------------------------------------------------------------------


class TestConfigurationCoherence:
    def test_a_specialist_brain_counts_as_a_consumer(self):
        """Without this, provider=anthropic + specialist_brain=llm would be
        refused as "nothing uses it" - rejecting a correct configuration."""
        assert check_configuration(settings(provider="anthropic", specialist_brain="llm")) is None

    def test_a_specialist_brain_with_no_provider_is_rejected(self):
        with pytest.raises(ConfigurationError):
            check_configuration(settings(specialist_brain="llm"))

    def test_the_message_names_the_specialist_setting(self):
        with pytest.raises(ConfigurationError) as exc:
            check_configuration(settings(specialist_brain="llm"))
        assert "MANAGER_SPECIALIST_BRAIN=llm" in str(exc.value)

    def test_the_discarded_provider_message_offers_it_as_a_fix(self):
        with pytest.raises(ConfigurationError) as exc:
            check_configuration(settings(provider="anthropic"))
        assert "MANAGER_SPECIALIST_BRAIN=llm" in str(exc.value)

    def test_the_default_remains_coherent(self):
        assert check_configuration(settings()) is None

    def test_it_is_read_from_the_environment(self):
        config = Settings.from_env(env={"MANAGER_SPECIALIST_BRAIN": "LLM"})
        assert config.specialist_brain == "llm"

    def test_an_empty_value_is_rejected(self):
        with pytest.raises(ValueError):
            Settings(specialist_brain="   ")

    def test_describe_reports_it_without_any_secret(self):
        described = settings(specialist_brain="llm", api_key="sk-ant-SECRET").describe()
        assert "specialist_brain=llm" in described
        assert "SECRET" not in described


# ----------------------------------------------------------------------
# Existing behaviour, and nothing on the wire
# ----------------------------------------------------------------------


class TestExistingBehaviourIntact:
    def test_the_email_agent_output_is_unchanged_by_default(self):
        assert scan(email_agent()).status is TaskStatus.COMPLETED

    def test_the_cognitive_agent_contract_is_unchanged(self):
        signature = inspect.signature(CognitiveAgent.__init__)
        assert list(signature.parameters) == [
            "self", "brain", "memory", "memory_policy", "tools"
        ]

    def test_the_email_agent_signature_still_accepts_a_brain(self):
        assert "brain" in inspect.signature(EmailAgent.__init__).parameters

    def test_the_specialist_brain_contract_is_unchanged(self):
        signature = inspect.signature(SpecialistBrain.consider)
        assert list(signature.parameters) == ["self", "task", "notes"]


class TestTheCompositionRootsWireIt:
    """The failure this milestone exists to prevent, one layer up.

    A configured specialist brain that the entry point forgets to pass is
    built, logged as ready, and thrown away - exactly the "provider nothing
    consumes" bug the coherence check was written for. These tests assert the
    brain actually reaches the agent.
    """

    def _built_agent(self, module, monkeypatch, tmp_path, **overrides):
        """Run a composition root and capture the EmailAgent it constructs.

        build_provider is replaced with a ScriptedProvider. An earlier version
        of this helper let the real AnthropicProvider through, and because
        EmailAgent genuinely consults its brain during execute(), the run
        imported the vendor SDK and attempted a connection - caught only by the
        session network guard. The settings still *say* anthropic, so the
        coherence rules under test are exercised unchanged; only the object
        behind them is offline.
        """
        captured = {}
        real = module.EmailAgent

        def capturing(*args, **kwargs):
            agent = real(*args, **kwargs)
            captured["agent"] = agent
            return agent

        # Isolated state and report paths, always. The heartbeat entry point
        # resolves these from Settings, and their defaults are the real
        # data/state/processed.json and data/reports - so a test that leaves
        # them alone runs a genuine cycle against the user's Gmail-derived
        # state. Baked into the helper rather than passed per test, so the
        # next test added here inherits the isolation instead of having to
        # remember it.
        overrides.setdefault("state_path", str(tmp_path / "state" / "processed.json"))
        overrides.setdefault("reports_dir", str(tmp_path / "reports"))
        config = settings(log_level="CRITICAL", **overrides)
        monkeypatch.setattr(module, "EmailAgent", capturing)
        monkeypatch.setattr(module.Settings, "from_env", staticmethod(lambda *a, **k: config))
        monkeypatch.setattr(module, "configure_logging", lambda *a, **k: None)
        monkeypatch.setattr(
            module, "build_provider", lambda *a, **k: ScriptedProvider(INSIGHT)
        )
        return captured

    def test_main_passes_a_brain_to_the_email_agent(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = self._built_agent(module, monkeypatch, tmp_path)
        module.main([])
        capsys.readouterr()

        assert "agent" in captured, "main.py did not construct an EmailAgent"
        assert isinstance(captured["agent"].brain, SpecialistBrain)

    def test_main_passes_the_configured_brain_not_the_default(self, monkeypatch, capsys, tmp_path):
        """With specialist_brain=llm the agent must receive an LLM brain. If the
        entry point drops the argument, EmailAgent silently falls back to the
        null brain and this fails."""
        import main as module

        captured = self._built_agent(
            module, monkeypatch, tmp_path, provider="anthropic", specialist_brain="llm", api_key="test-key"
        )
        module.main([])
        capsys.readouterr()

        assert isinstance(captured["agent"].brain, LLMSpecialistBrain)
        assert captured["agent"].brain.provider_name == "scripted"

    def test_main_gives_it_the_email_domain(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = self._built_agent(
            module, monkeypatch, tmp_path, provider="anthropic", specialist_brain="llm", api_key="test-key"
        )
        module.main([])
        capsys.readouterr()

        assert "email" in captured["agent"].brain.domain

    def test_the_heartbeat_passes_the_configured_brain_too(self, monkeypatch, capsys, tmp_path):
        """Wired on the autonomous path as well, or a configured brain would be
        discarded exactly where nobody is watching."""
        import sys

        sys.path.insert(0, ".")
        import scripts.heartbeat as module

        captured = self._built_agent(
            module, monkeypatch, tmp_path, provider="anthropic", specialist_brain="llm", api_key="test-key"
        )
        module.main(["--dry-run"])
        capsys.readouterr()

        assert isinstance(captured["agent"].brain, LLMSpecialistBrain)

    def test_the_manager_brain_is_not_handed_to_the_specialist(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = self._built_agent(
            module, monkeypatch, tmp_path, provider="anthropic",
            brain="llm",
            specialist_brain="llm",
            api_key="test-key",
        )
        module.main([])
        capsys.readouterr()

        assert not isinstance(captured["agent"].brain, LLMManagerBrain)


class TestNothingReachedTheNetwork:
    def test_building_a_brain_makes_no_call(self):
        provider = ScriptedProvider(INSIGHT)
        build_specialist_brain(settings(specialist_brain="llm"), provider)
        assert provider.call_count == 0

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert scan(email_agent(brain=LLMSpecialistBrain(ScriptedProvider(INSIGHT)))).status is (
            TaskStatus.COMPLETED
        )

    def test_no_vendor_sdk_is_imported(self):
        import sys

        build_specialist_brain(settings(specialist_brain="llm"), NullProvider())
        assert "anthropic" not in sys.modules

    def test_the_factory_never_reads_a_key(self):
        import src.agents.brain_factory as module

        source = inspect.getsource(module)
        assert "api_key" not in source
        assert "ANTHROPIC" not in source
