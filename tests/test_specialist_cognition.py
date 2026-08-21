"""Tests for the specialist cognitive boundary.

Each specialist owns a brain and a memory, and neither is ever the
orchestrator's. The classes that carry the weight:

    TestTheManagerIsUnreachable   - a delegated agent has no route to the CEO
    TestSpecialistsAreIsolated    - and no route to each other
    TestABrainCannotCommand       - a specialist brain has no action vocabulary

The third is why a specialist cannot steer the Manager: it is given nothing to
steer with. An Insight has no DELEGATE, no ESCALATE, no action of any kind.

No Anthropic, no Gmail, no network, no files.
"""

import socket
import sys

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.brain import (
    Insight,
    NullSpecialistBrain,
    SpecialistBrain,
    SpecialistBrainError,
)
from src.agents.cognitive_agent import CognitiveAgent
from src.agents.echo_agent import EchoAgent
from src.agents.email_agent import EmailAgent
from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus
from src.manager.brain import BrainDecision, ManagerBrain, NullBrain
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter
from src.registry.agent_registry import AgentRegistry
from src.services.email.in_memory_mailbox import InMemoryMailbox
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


class Specialist(CognitiveAgent):
    """A minimal specialist that owns cognition."""

    def __init__(self, name="research_agent", capabilities=("research",), **kwargs):
        super().__init__(**kwargs)
        self._name = name
        self._capabilities = tuple(capabilities)
        self.executions = []

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


class ScriptedBrain(SpecialistBrain):
    def __init__(self, insight=None):
        self.insight = insight or Insight(
            summary="this looks routine", confidence=0.7, source="scripted"
        )
        self.calls = []

    @property
    def name(self):
        return "scripted"

    def consider(self, task, notes=()):
        self.calls.append((task, tuple(notes)))
        return self.insight


def a_task(description="research something"):
    return Task(description=description)


def remembered(content):
    return MemoryRecord(content=content, kind="observation")


# ----------------------------------------------------------------------
# The brain contract
# ----------------------------------------------------------------------


class TestTheSpecialistBrainContract:
    def test_it_is_an_abstract_interface(self):
        with pytest.raises(TypeError):
            SpecialistBrain()

    def test_it_declares_exactly_two_members(self):
        assert set(SpecialistBrain.__abstractmethods__) == {"name", "consider"}

    def test_it_is_not_the_manager_brain(self):
        """Separate abstractions, not one reused in two places."""
        assert not issubclass(SpecialistBrain, ManagerBrain)
        assert not issubclass(ManagerBrain, SpecialistBrain)

    def test_a_manager_brain_is_not_a_specialist_brain(self):
        assert not isinstance(NullBrain(), SpecialistBrain)

    def test_a_specialist_brain_is_not_a_manager_brain(self):
        assert not isinstance(NullSpecialistBrain(), ManagerBrain)

    def test_the_method_names_differ_so_they_cannot_be_confused(self):
        assert not hasattr(NullSpecialistBrain(), "deliberate")
        assert not hasattr(NullBrain(), "consider")

    def test_it_receives_no_roster_of_agents(self):
        """A specialist has no business knowing who else exists."""
        import inspect

        params = set(inspect.signature(SpecialistBrain.consider).parameters)
        assert params == {"self", "task", "notes"}


class TestABrainCannotCommand:
    """An Insight has no vocabulary for directing anyone."""

    def test_an_insight_has_no_action(self):
        fields = set(Insight.__dataclass_fields__)
        assert fields == {"summary", "confidence", "source", "unknowns"}
        assert "action" not in fields
        assert "delegate_to" not in fields

    def test_an_insight_is_not_a_brain_decision(self):
        insight = Insight(summary="x", confidence=0.5)
        assert not isinstance(insight, BrainDecision)

    def test_the_brain_module_declares_no_actions(self):
        import src.agents.brain as module

        code = code_of(module)
        for word in ("delegate", "escalate", "handle_self", "brainaction"):
            assert word not in code, word

    def test_an_insight_is_frozen(self):
        insight = Insight(summary="x")
        with pytest.raises(Exception):
            insight.summary = "y"

    def test_an_insight_must_say_something(self):
        for blank in ("", "   "):
            with pytest.raises(SpecialistBrainError):
                Insight(summary=blank)

    def test_confidence_is_bounded(self):
        for bad in (-0.1, 1.5, 42):
            with pytest.raises(SpecialistBrainError):
                Insight(summary="x", confidence=bad)

    def test_unknowns_are_cleaned_and_deduplicated(self):
        insight = Insight(summary="x", unknowns=(" a  b ", "", "a b", "c"))
        assert insight.unknowns == ("a b", "c")

    def test_describe_excludes_the_summary(self):
        """A specialist reasons over its own domain content; logs must not."""
        insight = Insight(summary="the sender's salary was 50000", confidence=0.5)
        assert "50000" not in insight.describe()


class TestTheNullBrainIsHonest:
    def test_it_satisfies_the_contract(self):
        assert isinstance(NullSpecialistBrain(), SpecialistBrain)

    def test_it_reports_having_no_view(self):
        insight = NullSpecialistBrain().consider(a_task())
        assert not insight.is_useful
        assert insight.confidence == 0.0
        assert "no reasoning capability" in insight.summary

    def test_it_names_what_it_could_not_do(self):
        assert NullSpecialistBrain().consider(a_task()).unknowns == ("no reasoning",)

    def test_it_never_invents_a_conclusion(self):
        for description in ("research something", "anything at all"):
            insight = NullSpecialistBrain().consider(a_task(description))
            assert insight.confidence == 0.0


# ----------------------------------------------------------------------
# Injection and defaults
# ----------------------------------------------------------------------


class TestCognitionIsInjected:
    def test_a_brain_can_be_injected(self):
        brain = ScriptedBrain()
        assert Specialist(brain=brain).brain is brain

    def test_a_memory_can_be_injected(self):
        memory = EphemeralMemory()
        assert Specialist(memory=memory).memory is memory

    def test_both_default_safely(self):
        agent = Specialist()
        assert isinstance(agent.brain, NullSpecialistBrain)
        assert isinstance(agent.memory, Memory)
        assert len(agent.memory) == 0

    def test_a_specialist_reports_whether_it_can_reason(self):
        assert not Specialist().can_reason
        assert Specialist(brain=ScriptedBrain()).can_reason

    def test_a_non_brain_is_rejected_at_construction(self):
        for bad in ("a string", 42, NullBrain()):
            with pytest.raises(TypeError):
                Specialist(brain=bad)

    def test_a_non_memory_is_rejected_at_construction(self):
        for bad in ("a string", 42, {}):
            with pytest.raises(TypeError):
                Specialist(memory=bad)

    def test_any_memory_implementation_is_accepted(self):
        class MinimalMemory(Memory):
            def remember(self, record):
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                return ()

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        assert isinstance(Specialist(memory=MinimalMemory()).memory, Memory)

    def test_a_cognitive_agent_is_still_a_base_agent(self):
        """So the Manager's contract is unchanged."""
        assert isinstance(Specialist(), BaseAgent)

    def test_cognitive_agent_remains_abstract(self):
        with pytest.raises(TypeError):
            CognitiveAgent()

    def test_agents_that_do_not_want_cognition_are_unaffected(self):
        agent = EchoAgent(name="alpha_agent", capabilities={"email"})
        assert isinstance(agent, BaseAgent)
        assert not isinstance(agent, CognitiveAgent)
        assert not hasattr(agent, "brain")


# ----------------------------------------------------------------------
# THE BOUNDARY: the Manager is unreachable
# ----------------------------------------------------------------------


class TestTheManagerIsUnreachable:
    def _delegating_manager(self, agent):
        registry = AgentRegistry()
        registry.register(agent)
        return ManagerAgent(registry=registry)

    def test_specialist_and_manager_brains_are_separate_objects(self):
        agent = Specialist(brain=ScriptedBrain())
        manager = self._delegating_manager(agent)
        assert manager.brain is not agent.brain

    def test_specialist_and_manager_memories_are_separate_objects(self):
        agent = Specialist()
        manager = self._delegating_manager(agent)
        assert manager.memory is not agent.memory

    def test_delegation_does_not_hand_over_the_managers_memory(self):
        """The central claim: a Task carries work, never access."""
        agent = Specialist()
        manager = self._delegating_manager(agent)
        manager.memory.remember(remembered("something only the manager knows"))

        manager.handle_task(a_task())

        assert len(agent.executions) == 1
        delivered = agent.executions[0]
        assert isinstance(delivered, Task)
        assert not hasattr(delivered, "memory")
        assert not hasattr(delivered, "brain")
        assert not hasattr(delivered, "manager")

    def test_the_specialist_cannot_recall_the_managers_records(self):
        agent = Specialist()
        manager = self._delegating_manager(agent)
        manager.memory.remember(remembered("something only the manager knows"))

        manager.handle_task(a_task())

        assert agent.memory.recall("something") == ()
        assert len(agent.memory) == 0

    def test_the_manager_does_not_absorb_the_specialists_records(self):
        """The reverse direction: a specialist's memory is its own."""
        agent = Specialist()
        manager = self._delegating_manager(agent)
        agent.memory.remember(remembered("something only the specialist knows"))

        assert manager.memory.recall("specialist") == ()

    def test_a_specialist_holds_no_reference_to_the_orchestrator(self):
        agent = Specialist()
        self._delegating_manager(agent).handle_task(a_task())
        for attribute in ("manager", "registry", "router", "assessor", "own_skills"):
            assert not hasattr(agent, attribute), attribute

    def test_a_specialist_cannot_reach_the_manager_brain(self):
        agent = Specialist(brain=ScriptedBrain())
        manager = self._delegating_manager(agent)
        manager.handle_task(a_task())
        assert not isinstance(agent.brain, ManagerBrain)
        assert not hasattr(agent.brain, "deliberate")

    def test_the_specialist_modules_never_import_the_manager(self):
        import src.agents.brain as brain_module
        import src.agents.cognitive_agent as cognitive_module

        for module in (brain_module, cognitive_module):
            assert "from src.manager" not in code_of(module)

    def test_a_specialist_cannot_create_or_run_other_agents(self):
        import src.agents.cognitive_agent as module

        code = code_of(module)
        for forbidden in ("from src.registry", "agentregistry", ".execute("):
            assert forbidden not in code, forbidden


# ----------------------------------------------------------------------
# Specialists are isolated from each other
# ----------------------------------------------------------------------


class TestSpecialistsAreIsolated:
    def test_two_specialists_have_separate_memories(self):
        one = Specialist(name="research_agent")
        two = Specialist(name="writing_agent", capabilities=("write",))
        assert one.memory is not two.memory

    def test_two_specialists_have_separate_brains(self):
        one, two = Specialist(), Specialist(name="writing_agent")
        assert one.brain is not two.brain

    def test_one_specialists_records_do_not_appear_in_anothers(self):
        one = Specialist(name="research_agent")
        two = Specialist(name="writing_agent", capabilities=("write",))

        one.memory.remember(remembered("a research finding"))

        assert len(one.memory) == 1
        assert len(two.memory) == 0
        assert two.memory.recall("research") == ()

    def test_clearing_one_specialists_memory_leaves_the_other_alone(self):
        one, two = Specialist(), Specialist(name="writing_agent")
        one.memory.remember(remembered("a"))
        two.memory.remember(remembered("b"))

        one.memory.clear()
        assert len(one.memory) == 0 and len(two.memory) == 1

    def test_specialists_under_one_manager_stay_separate(self):
        one = Specialist(name="research_agent", capabilities=("research",))
        two = Specialist(name="writing_agent", capabilities=("write",))
        registry = AgentRegistry()
        registry.register(one)
        registry.register(two)
        manager = ManagerAgent(registry=registry)

        manager.handle_task(a_task("research something"))
        one.memory.remember(remembered("a research finding"))

        assert len(two.memory) == 0
        assert two.memory.recall("finding") == ()

    def test_there_is_no_module_level_shared_memory(self):
        """No global singleton anywhere in the specialist package."""
        import src.agents.cognitive_agent as module

        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            assert not isinstance(value, (Memory, SpecialistBrain)), name

    def test_default_specialists_do_not_share_anything(self):
        agents = [Specialist(name=f"agent_{n}") for n in range(5)]
        assert len({id(a.memory) for a in agents}) == 5
        assert len({id(a.brain) for a in agents}) == 5


# ----------------------------------------------------------------------
# The mail specialist, wired
# ----------------------------------------------------------------------


class TestTheEmailAgentOwnsCognition:
    def _agent(self, **kwargs):
        return EmailAgent(InMemoryMailbox([]), **kwargs)

    def test_it_is_a_cognitive_agent(self):
        assert isinstance(self._agent(), CognitiveAgent)

    def test_it_defaults_to_a_null_brain_and_empty_memory(self):
        agent = self._agent()
        assert isinstance(agent.brain, NullSpecialistBrain)
        assert len(agent.memory) == 0

    def test_a_brain_can_be_injected(self):
        brain = ScriptedBrain()
        assert self._agent(brain=brain).brain is brain

    def test_a_memory_can_be_injected(self):
        memory = EphemeralMemory()
        assert self._agent(memory=memory).memory is memory

    def test_two_email_agents_do_not_share_memory(self):
        one, two = self._agent(), self._agent()
        one.memory.remember(remembered("an observation about mail"))
        assert len(two.memory) == 0

    def test_its_memory_is_not_the_managers(self):
        agent = self._agent()
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(registry=registry)
        assert agent.memory is not manager.memory

    def test_mail_handling_is_unchanged(self):
        """The deterministic path must behave exactly as before."""
        agent = self._agent()
        result = agent.execute(Task(description="scan my inbox for opportunities"))
        assert result.status is TaskStatus.COMPLETED

    def test_sending_is_still_refused(self):
        agent = self._agent()
        assert agent.can_send is False

    def test_a_configured_brain_is_consulted_during_execute(self):
        """Retargeted: the cognitive loop now runs. See
        test_specialist_cognitive_loop.py for what it does."""
        brain = ScriptedBrain()
        agent = self._agent(brain=brain)
        agent.execute(Task(description="scan my inbox for opportunities"))
        assert len(brain.calls) == 1

    def test_the_default_null_brain_is_never_called(self):
        """No brain configured means no reasoning was attempted, honestly."""
        agent = self._agent()
        result = agent.execute(Task(description="scan my inbox for opportunities"))
        assert "insight" not in (result.payload or {})

    def test_a_default_agent_records_nothing_during_execute(self):
        """Retargeted: recording now exists, but a specialist with no brain
        forms no reflection and so proposes nothing."""
        agent = self._agent()
        agent.execute(Task(description="scan my inbox for opportunities"))
        assert len(agent.memory) == 0


# ----------------------------------------------------------------------
# Nothing else changed
# ----------------------------------------------------------------------


class TestNothingElseChanged:
    def test_the_base_agent_contract_is_unchanged(self):
        assert set(BaseAgent.__abstractmethods__) == {"spec", "execute"}

    def test_the_memory_contract_is_unchanged(self):
        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }

    def test_the_manager_brain_contract_is_unchanged(self):
        assert set(ManagerBrain.__abstractmethods__) == {"name", "deliberate"}

    def test_routing_is_unchanged(self):
        agent = Specialist()
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(registry=registry)

        assert isinstance(manager.router, CapabilityRouter)
        assert manager.diagnose(a_task()).agent_name == "research_agent"

    def test_assessment_is_unchanged(self):
        agent = Specialist()
        registry = AgentRegistry()
        registry.register(agent)
        result = ManagerAgent(registry=registry).handle_task(a_task())
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "research_agent"

    def test_the_manager_still_records_delegation_outcomes(self):
        agent = Specialist()
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(registry=registry)
        manager.handle_task(a_task())

        assert len(manager.memory) == 1
        assert manager.memory.recall(limit=5)[0].kind == "delegation_outcome"


class TestNothingLeaksOut:
    def _sources(self):
        import src.agents.brain as brain_module
        import src.agents.cognitive_agent as cognitive_module

        return {
            "brain.py": code_of(brain_module),
            "cognitive_agent.py": code_of(cognitive_module),
        }

    def test_no_vendor_is_named(self):
        for name, code in self._sources().items():
            for word in ("anthropic", "openai", "gmail", "api_key"):
                assert word not in code, f"{word} in {name}"

    def test_no_provider_is_imported(self):
        for name, code in self._sources().items():
            assert "from src.providers" not in code, name

    def test_no_anthropic_sdk_is_imported(self):
        agent = Specialist(brain=ScriptedBrain())
        agent.brain.consider(a_task())
        assert "anthropic" not in sys.modules

    def test_no_persistent_storage_is_introduced(self):
        for name, code in self._sources().items():
            for word in ("sqlite", "redis", "open(", "json.dump", "path("):
                assert word not in code, f"{word} in {name}"

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = Specialist(brain=ScriptedBrain())
        agent.brain.consider(a_task())
        agent.memory.remember(remembered("an observation"))
        assert list(tmp_path.iterdir()) == []

    def test_nothing_survives_a_new_instance(self):
        Specialist().memory.remember(remembered("an observation"))
        assert len(Specialist().memory) == 0

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        agent = Specialist(brain=ScriptedBrain())
        assert agent.brain.consider(a_task()).is_useful

    def test_the_brain_receives_inert_notes(self):
        """Context is text - a brain cannot query or write any store."""
        brain = ScriptedBrain()
        agent = Specialist(brain=brain)
        agent.brain.consider(a_task(), notes=["something recalled"])

        _, notes = brain.calls[0]
        assert all(isinstance(note, str) for note in notes)
        assert not any(isinstance(note, Memory) for note in notes)
