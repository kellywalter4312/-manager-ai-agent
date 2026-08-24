"""Tests that the production composition roots reach the real implementations.

This file exists because of a defect class that kept recurring:

    wired  !=  reachable  !=  durable

A dependency can have a seam, a real implementation, and a full set of passing
unit tests, and still never be supplied by any composition root. The specialist
brain had that shape. So did Manager memory. So did OpportunityInterpreter,
whose seam turned out to be missing one layer up: analyze_opportunities had
always accepted an ``interpreter``, and JobOpportunityAnalyzer had no way to
pass one, so the real interpreter could be constructed only by a test.

The rule these tests follow, and the reason they are worth their length:
**they drive main() and heartbeat's factory, and inspect what those actually
built.** A test that constructs OpportunityInterpreter itself proves the class
works; it proves nothing about whether production ever sees it.

No Gmail, no model, no network. The provider is scripted, paths are temporary.
"""

import inspect

import pytest
from conftest import ScriptedProvider

from src.agents.brain_factory import build_opportunity_interpreter
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.agents.email.llm_interpreter import OpportunityInterpreter
from src.agents.email_agent import EmailAgent
from src.config.settings import Settings
from src.manager.manager import ManagerAgent
from src.services.memory.persistent import PersistentMemory

JUDGEMENT = '{"verdict": "promising", "rationale": "matches the stated skills"}'


def settings(**overrides):
    return Settings.from_env(env={}, **overrides)


def drive(module, monkeypatch, tmp_path, argv=None, llm_provider=None, **overrides):
    # Named llm_provider, not provider: `provider` is also a Settings
    # field name, and a collision here silently swallowed the
    # provider="anthropic" override into this parameter instead.
    """Run a composition root and capture the agents it constructed.

    build_provider is replaced with a ScriptedProvider so the settings can say
    anthropic - exercising the real gating - while nothing goes near a network.
    """
    # The agent and analyzer classes are intercepted where production now
    # constructs them: inside the email specialist's builder. The entry point
    # is still driven end to end - which is the whole point of these tests.
    # They must observe what production actually builds, wherever it builds it.
    import src.agents.specialists as specialists
    import src.composition as composition

    captured = {}
    real_email_agent = specialists.EmailAgent
    real_analyzer = specialists.JobOpportunityAnalyzer

    def capturing_agent(*args, **kwargs):
        agent = real_email_agent(*args, **kwargs)
        captured["agent"] = agent
        return agent

    def capturing_analyzer(*args, **kwargs):
        analyzer = real_analyzer(*args, **kwargs)
        captured["analyzer"] = analyzer
        return analyzer

    overrides.setdefault("state_path", str(tmp_path / "state" / "processed.json"))
    overrides.setdefault("reports_dir", str(tmp_path / "reports"))
    overrides.setdefault("memory_dir", str(tmp_path / "memory"))
    config = settings(log_level="CRITICAL", **overrides)

    monkeypatch.setattr(specialists, "EmailAgent", capturing_agent)
    monkeypatch.setattr(specialists, "JobOpportunityAnalyzer", capturing_analyzer)
    monkeypatch.setattr(module.Settings, "from_env", staticmethod(lambda *a, **k: config))
    monkeypatch.setattr(module, "configure_logging", lambda *a, **k: None)
    scripted = llm_provider if llm_provider is not None else ScriptedProvider(JUDGEMENT)
    monkeypatch.setattr(composition, "build_provider", lambda *a, **k: scripted)

    module.main(argv if argv is not None else [])
    return captured


CONFIGURED = dict(provider="anthropic", specialist_brain="llm", api_key="test-key")


# ----------------------------------------------------------------------
# The defect: the interpreter never reached production
# ----------------------------------------------------------------------


class TestTheInterpreterReachesProduction:
    def test_the_analyzer_now_accepts_one(self):
        """The missing seam. Without this parameter no composition root could
        supply an interpreter, whatever else was configured."""
        assert "interpreter" in inspect.signature(JobOpportunityAnalyzer.__init__).parameters

    def test_main_supplies_a_real_interpreter(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = drive(module, monkeypatch, tmp_path, **CONFIGURED)
        capsys.readouterr()

        assert isinstance(captured["analyzer"].interpreter, OpportunityInterpreter)

    def test_the_heartbeat_supplies_a_real_interpreter(self, monkeypatch, capsys, tmp_path):
        import sys

        sys.path.insert(0, ".")
        import scripts.heartbeat as module

        captured = drive(module, monkeypatch, tmp_path, argv=["--dry-run"], **CONFIGURED)
        capsys.readouterr()

        assert isinstance(captured["analyzer"].interpreter, OpportunityInterpreter)

    def test_the_interpreter_holds_the_configured_provider(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = drive(module, monkeypatch, tmp_path, **CONFIGURED)
        capsys.readouterr()

        assert captured["analyzer"].interpreter.provider_name == "scripted"

    def test_the_analyzer_reaches_the_email_agent(self, monkeypatch, capsys, tmp_path):
        """Reachability is a chain: an interpreter on an analyzer nobody
        registered would be just as unreachable."""
        import main as module

        captured = drive(module, monkeypatch, tmp_path, **CONFIGURED)
        capsys.readouterr()

        agent = captured["agent"]
        assert captured["analyzer"] in agent._analyzers

    def test_the_interpreter_is_actually_consulted(self, monkeypatch, capsys, tmp_path):
        """The end of the chain: the model is asked *about an opportunity*.

        Identified by the interpreter's own system prompt rather than by a bare
        call count. With the specialist brain also switched on, the provider is
        called either way - so counting calls would pass even if the analyzer
        stopped forwarding the interpreter entirely. Mutation testing found
        exactly that hole in an earlier version of this test.
        """
        import main as module
        from src.agents.email.llm_interpreter import SYSTEM_PROMPT

        provider = ScriptedProvider(JUDGEMENT)
        drive(module, monkeypatch, tmp_path, llm_provider=provider, **CONFIGURED)
        capsys.readouterr()

        assert SYSTEM_PROMPT in provider.systems, (
            "the provider was called, but never with the interpreter's prompt - "
            "the interpreter was wired and not consulted"
        )

    def test_the_judgement_reaches_the_analysis(self, monkeypatch, capsys, tmp_path):
        """Behavioural counterpart: the interpreter's output lands on an
        opportunity, rather than being produced and dropped."""
        from src.services.email.sample_inbox import sample_inbox
        from src.services.profile.loader import load_profile

        analyzer = JobOpportunityAnalyzer(
            profile=load_profile("USER.example.md"),
            interpreter=OpportunityInterpreter(ScriptedProvider(JUDGEMENT)),
        )
        report = analyzer.analyze(sample_inbox().fetch(limit=10))
        judged = [f for f in report.findings if getattr(f, "judgement", None) is not None]
        assert judged, "no finding carried a judgement"


# ----------------------------------------------------------------------
# The deterministic default is untouched
# ----------------------------------------------------------------------


class TestTheDefaultStaysDeterministic:
    def test_no_interpreter_without_a_specialist_brain(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = drive(module, monkeypatch, tmp_path)
        capsys.readouterr()

        assert captured["analyzer"].interpreter is None

    def test_the_factory_returns_none_by_default(self):
        assert build_opportunity_interpreter(settings(), ScriptedProvider(JUDGEMENT)) is None

    def test_the_factory_builds_one_when_the_specialist_brain_is_on(self):
        config = settings(provider="anthropic", specialist_brain="llm", api_key="k")
        built = build_opportunity_interpreter(config, ScriptedProvider(JUDGEMENT))
        assert isinstance(built, OpportunityInterpreter)

    def test_it_is_gated_on_the_same_switch_as_the_specialist_brain(self):
        """One switch, not a fourth. Turning on the specialist's reasoning
        turns on all of it, so no deployment is surprised by the cost of half."""
        on = settings(provider="anthropic", specialist_brain="llm", api_key="k")
        off = settings(provider="anthropic", specialist_brain="null", api_key="k")

        assert build_opportunity_interpreter(on, ScriptedProvider(JUDGEMENT)) is not None
        assert build_opportunity_interpreter(off, ScriptedProvider(JUDGEMENT)) is None

    def test_the_default_run_calls_no_provider(self, monkeypatch, capsys, tmp_path):
        import main as module

        provider = ScriptedProvider(JUDGEMENT)
        drive(module, monkeypatch, tmp_path, llm_provider=provider)
        capsys.readouterr()

        assert provider.call_count == 0

    def test_no_vendor_sdk_is_imported_by_the_default_path(self, monkeypatch, capsys, tmp_path):
        import sys

        import main as module

        drive(module, monkeypatch, tmp_path)
        capsys.readouterr()
        assert "anthropic" not in sys.modules

    def test_analysis_still_works_with_no_interpreter(self):
        """The deterministic pipeline is unchanged when nothing is supplied."""
        from src.services.email.sample_inbox import sample_inbox
        from src.services.profile.loader import load_profile

        analyzer = JobOpportunityAnalyzer(profile=load_profile("USER.example.md"))
        report = analyzer.analyze(sample_inbox().fetch(limit=10))
        assert report.considered > 0


# ----------------------------------------------------------------------
# The rest of the matrix: supplied, or deliberately absent
# ----------------------------------------------------------------------


class TestTheOtherDependenciesAreReachable:
    def _built(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = drive(module, monkeypatch, tmp_path, memory="persistent", **CONFIGURED)
        capsys.readouterr()
        return captured

    def test_the_specialist_gets_a_real_brain(self, monkeypatch, capsys, tmp_path):
        from src.agents.llm_brain import LLMSpecialistBrain

        agent = self._built(monkeypatch, capsys, tmp_path)["agent"]
        assert isinstance(agent.brain, LLMSpecialistBrain)

    def test_the_specialist_gets_a_durable_memory(self, monkeypatch, capsys, tmp_path):
        agent = self._built(monkeypatch, capsys, tmp_path)["agent"]
        assert isinstance(agent.memory, PersistentMemory)

    def test_the_specialist_gets_the_configured_calendar(self, monkeypatch, capsys, tmp_path):
        agent = self._built(monkeypatch, capsys, tmp_path)["agent"]
        assert agent.calendar.zone is not None

    def test_the_specialist_gets_a_profile_backed_analyzer(self, monkeypatch, capsys, tmp_path):
        analyzer = self._built(monkeypatch, capsys, tmp_path)["analyzer"]
        assert analyzer._profile is not None


class TestDeliberatelyDeferredCapabilities:
    """Not defects. These are absent because the capability is withheld, and a
    test that demanded them would be asking for autonomy this project has
    deliberately not granted.
    """

    def test_no_outbox_is_supplied(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = drive(module, monkeypatch, tmp_path, **CONFIGURED)
        capsys.readouterr()
        assert captured["agent"]._outbox is None

    def test_no_outbox_implementation_exists_to_supply(self):
        """Structural, not a matter of wiring: there is nothing to inject."""
        import src.services.email.mailbox as module

        concrete = [
            name for name, obj in vars(module).items()
            if isinstance(obj, type) and issubclass(obj, module.Outbox) and obj is not module.Outbox
        ]
        assert concrete == []

    def test_the_specialist_gets_no_tools(self, monkeypatch, capsys, tmp_path):
        import main as module

        captured = drive(module, monkeypatch, tmp_path, **CONFIGURED)
        capsys.readouterr()
        assert len(captured["agent"].tools) == 0

    def test_send_words_are_still_refused(self):
        from src.core.contracts import Task, TaskStatus
        from src.services.email.sample_inbox import sample_inbox

        agent = EmailAgent(sample_inbox())
        result = agent.execute(Task(description="send an email to my team"))
        assert result.status is not TaskStatus.COMPLETED

    def test_the_manager_still_uses_the_only_assessor(self):
        """Its default is the only implementation, so 'not supplied' is not a
        reachability defect - there is nothing else to reach."""
        from src.manager.assessor import ContractAssessor

        assert isinstance(ManagerAgent().assessor, ContractAssessor)
