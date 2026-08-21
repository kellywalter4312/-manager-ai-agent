"""Tests for the Email Specialist's cognitive loop.

    task -> recall own memory -> domain work -> reason about it -> result

The claims that carry the weight:

    TestTheDeterministicPipelineIsAuthoritative - reasoning never edits findings
    TestNoMemoryLeaks                           - own store only, in both directions
    TestTheBrainCannotOrchestrate               - no delegation, no registry, no router
    TestReasoningFailureIsContained             - a broken brain costs judgement, not the task

No Anthropic, no network, no files. Every brain here is scripted.
"""

import dataclasses
import socket
import sys

import pytest

from src.agents.brain import Insight, NullSpecialistBrain, SpecialistBrain
from src.agents.cognitive_agent import MEMORY_NOTE_PREFIX, CognitiveAgent
from src.agents.email_agent import EmailAgent
from src.core.contracts import FINDINGS_KEY, AgentSpec, Task, TaskResult, TaskStatus
from src.manager.assessor import ContractAssessor
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter
from src.registry.agent_registry import AgentRegistry
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox, MailboxError
from src.services.email.sample_inbox import sample_inbox
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


class ScriptedBrain(SpecialistBrain):
    def __init__(self, insight=None, fail=None):
        self.insight = insight if insight is not None else Insight(
            summary="two postings look worth a closer read",
            confidence=0.6,
            source="scripted",
            unknowns=("salary not stated",),
        )
        self.fail = fail
        self.calls = []

    @property
    def name(self):
        return "scripted"

    def consider(self, task, notes=()):
        self.calls.append({"task": task, "notes": tuple(notes)})
        if self.fail is not None:
            raise self.fail
        return self.insight

    @property
    def last_notes(self):
        return self.calls[-1]["notes"]


class BrokenMailbox(Mailbox):
    def fetch(self, limit=20, label=None):
        raise MailboxError("the mailbox is unavailable")


def scan_task(description="scan my inbox for job opportunities", **fields):
    return Task(description=description, **fields)


def remembered(content, kind="observation"):
    return MemoryRecord(content=content, kind=kind)


def agent_with(brain=None, memory=None, mailbox=None):
    return EmailAgent(
        mailbox if mailbox is not None else sample_inbox(),
        brain=brain,
        memory=memory,
    )


def insight_of(result):
    return (result.payload or {}).get("insight")


# ----------------------------------------------------------------------
# The loop runs
# ----------------------------------------------------------------------


class TestTheLoopRuns:
    def test_a_configured_brain_is_consulted(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        assert len(brain.calls) == 1

    def test_it_is_consulted_at_most_once_per_task(self):
        """Bounded: reasoning is a step, never a loop."""
        brain = ScriptedBrain()
        agent = agent_with(brain)
        agent.execute(scan_task())
        agent.execute(scan_task())
        assert len(brain.calls) == 2      # once each, never more

    def test_the_insight_reaches_the_result(self):
        brain = ScriptedBrain()
        result = agent_with(brain).execute(scan_task())
        assert insight_of(result) is brain.insight

    def test_the_brain_receives_the_assigned_task(self):
        brain = ScriptedBrain()
        task = scan_task()
        agent_with(brain).execute(task)
        assert brain.calls[0]["task"] is task

    def test_the_brain_is_told_what_the_rules_established(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        joined = " ".join(brain.last_notes)
        assert "job_opportunity" in joined
        assert "confident match" in joined

    def test_the_brain_is_told_where_the_rules_were_uncertain(self):
        """Ambiguity is where judgement is worth most, so it is named."""
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        joined = " ".join(brain.last_notes)
        assert "could not place" in joined

    def test_no_message_content_is_sent_to_the_brain(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        joined = " ".join(brain.last_notes)
        for leaked in ("@", "subject:", "from:"):
            assert leaked not in joined.lower()

    def test_reasoning_is_skipped_when_no_work_happened(self):
        """A send refusal did no domain work; there is nothing to judge."""
        brain = ScriptedBrain()
        result = agent_with(brain).execute(scan_task("send an email to my team"))
        assert brain.calls == []
        assert result.status is TaskStatus.FAILED      # refusing to send

    def test_reasoning_is_skipped_when_the_mailbox_fails(self):
        brain = ScriptedBrain()
        result = agent_with(brain, mailbox=BrokenMailbox()).execute(scan_task())
        assert brain.calls == []
        assert result.status is TaskStatus.FAILED


# ----------------------------------------------------------------------
# Memory feeds the brain as evidence
# ----------------------------------------------------------------------


class TestOwnMemoryFeedsReasoning:
    def test_recalled_records_reach_the_brain(self):
        brain = ScriptedBrain()
        memory = EphemeralMemory()
        memory.remember(remembered("job digests from this sender are usually stale"))

        agent_with(brain, memory).execute(scan_task("scan for job opportunities"))
        assert any("usually stale" in note for note in brain.last_notes)

    def test_recalled_records_are_labelled_as_recalled(self):
        brain = ScriptedBrain()
        memory = EphemeralMemory()
        memory.remember(remembered("job digests are usually stale"))

        agent_with(brain, memory).execute(scan_task("scan for job opportunities"))
        recalled = [n for n in brain.last_notes if n.startswith(MEMORY_NOTE_PREFIX)]
        assert len(recalled) == 1

    def test_domain_notes_are_not_labelled_as_recalled(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        assert not any(n.startswith(MEMORY_NOTE_PREFIX) for n in brain.last_notes)

    def test_irrelevant_records_are_not_passed(self):
        brain = ScriptedBrain()
        memory = EphemeralMemory()
        memory.remember(remembered("the calendar sync failed last Tuesday"))

        agent_with(brain, memory).execute(scan_task("scan for job opportunities"))
        assert not any(MEMORY_NOTE_PREFIX in note for note in brain.last_notes)

    def test_an_empty_memory_yields_no_recalled_notes(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        assert not any(n.startswith(MEMORY_NOTE_PREFIX) for n in brain.last_notes)

    def test_the_brain_receives_inert_strings(self):
        brain = ScriptedBrain()
        memory = EphemeralMemory()
        memory.remember(remembered("job digests are usually stale"))

        agent_with(brain, memory).execute(scan_task("scan for job opportunities"))
        assert all(isinstance(note, str) for note in brain.last_notes)
        assert not any(isinstance(note, Memory) for note in brain.last_notes)

    def test_recall_uses_its_own_store(self):
        agent = agent_with(ScriptedBrain(), EphemeralMemory())
        agent.memory.remember(remembered("job digests are usually stale"))
        assert agent.recall_context(scan_task("scan for job opportunities"))

    def test_recall_is_deterministic(self):
        agent = agent_with(ScriptedBrain(), EphemeralMemory())
        agent.memory.remember(remembered("job digests are usually stale"))
        task = scan_task("scan for job opportunities")
        assert agent.recall_context(task) == agent.recall_context(task)


# ----------------------------------------------------------------------
# THE BOUNDARY: no memory leaks, either direction
# ----------------------------------------------------------------------


class TestNoMemoryLeaks:
    def _delegating(self, agent):
        registry = AgentRegistry()
        registry.register(agent)
        return ManagerAgent(registry=registry)

    def test_manager_memory_never_reaches_the_specialist_brain(self):
        brain = ScriptedBrain()
        agent = agent_with(brain)
        manager = self._delegating(agent)
        manager.memory.remember(remembered("something only the manager knows"))

        manager.handle_task(scan_task())

        assert brain.calls
        assert not any("only the manager" in note for note in brain.last_notes)

    def test_the_specialist_recalls_only_its_own_store(self):
        agent = agent_with(ScriptedBrain())
        manager = self._delegating(agent)
        manager.memory.remember(remembered("job digests are usually stale"))

        assert agent.recall_context(scan_task("scan for job opportunities")) == ()

    def test_another_specialists_memory_never_reaches_this_one(self):
        brain = ScriptedBrain()
        mine = agent_with(brain, EphemeralMemory())
        theirs = EphemeralMemory()
        theirs.remember(remembered("job digests are usually stale"))

        mine.execute(scan_task("scan for job opportunities"))
        assert not any("usually stale" in note for note in brain.last_notes)

    def test_two_email_agents_reason_from_separate_histories(self):
        one_brain, two_brain = ScriptedBrain(), ScriptedBrain()
        one = agent_with(one_brain, EphemeralMemory())
        two = agent_with(two_brain, EphemeralMemory())
        one.memory.remember(remembered("job digests are usually stale"))

        one.execute(scan_task("scan for job opportunities"))
        two.execute(scan_task("scan for job opportunities"))

        assert any("usually stale" in n for n in one_brain.last_notes)
        assert not any("usually stale" in n for n in two_brain.last_notes)

    def test_the_specialist_does_not_write_into_manager_memory(self):
        agent = agent_with(ScriptedBrain())
        manager = self._delegating(agent)
        before = len(manager.memory)
        agent.memory.remember(remembered("an observation of my own"))
        # Only the Manager's own delegation recording may add to its store.
        assert len(manager.memory) == before

    def test_the_cognitive_module_cannot_reach_the_orchestrator(self):
        import src.agents.cognitive_agent as module

        code = code_of(module)
        assert "from src.manager" not in code
        assert "from src.registry" not in code


# ----------------------------------------------------------------------
# The brain cannot orchestrate
# ----------------------------------------------------------------------


class TestTheBrainCannotOrchestrate:
    def test_an_insight_carries_no_action(self):
        fields = set(Insight.__dataclass_fields__)
        assert fields == {"summary", "confidence", "source", "unknowns"}

    def test_the_brain_receives_no_registry_or_roster(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        call = brain.calls[0]
        assert set(call) == {"task", "notes"}
        assert not hasattr(call["task"], "registry")

    def test_the_brain_holds_no_route_to_anything(self):
        brain = ScriptedBrain()
        agent = agent_with(brain)
        agent.execute(scan_task())
        for attribute in ("registry", "router", "assessor", "manager", "memory"):
            assert not hasattr(brain, attribute), attribute

    def test_reasoning_does_not_change_routing(self):
        opinionated = ScriptedBrain(
            Insight(summary="another agent should handle this", confidence=1.0)
        )
        agent = agent_with(opinionated)
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(registry=registry)

        result = manager.handle_task(scan_task())
        assert result.agent_name == "email_agent"
        assert isinstance(manager.router, CapabilityRouter)

    def test_reasoning_does_not_bypass_the_assessor(self):
        seen = []

        class RecordingAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                seen.append(result)
                return super().assess(task, agent_name, result)

        registry = AgentRegistry()
        registry.register(agent_with(ScriptedBrain()))
        manager = ManagerAgent(registry=registry, assessor=RecordingAssessor())

        result = manager.handle_task(scan_task())
        assert len(seen) == 1
        assert result.status is TaskStatus.COMPLETED

    def test_the_manager_still_records_its_own_outcome(self):
        registry = AgentRegistry()
        registry.register(agent_with(ScriptedBrain()))
        manager = ManagerAgent(registry=registry)
        manager.handle_task(scan_task())

        assert manager.memory.recall(limit=5)[0].kind == "delegation_outcome"


# ----------------------------------------------------------------------
# The deterministic pipeline stays authoritative
# ----------------------------------------------------------------------


class TestTheDeterministicPipelineIsAuthoritative:
    def _findings(self, result):
        return (result.payload or {}).get(FINDINGS_KEY, ())

    def test_findings_are_identical_with_and_without_a_brain(self):
        plain = self._findings(agent_with().execute(scan_task()))
        judged = self._findings(agent_with(ScriptedBrain()).execute(scan_task()))

        assert plain      # the sample inbox really does produce findings
        assert plain == judged

    def test_every_rule_derived_field_is_untouched(self):
        plain = self._findings(agent_with().execute(scan_task()))
        judged = self._findings(agent_with(ScriptedBrain()).execute(scan_task()))
        assert plain and len(plain) == len(judged)

        for before, after in zip(plain, judged):
            for field in dataclasses.fields(before):
                assert getattr(before, field.name) == getattr(after, field.name), field.name

    def test_a_confident_insight_does_not_promote_anything(self):
        """A model saying "excellent" must not move a score the rules set."""
        enthusiastic = ScriptedBrain(
            Insight(summary="everything here is excellent", confidence=1.0)
        )
        plain = self._findings(agent_with().execute(scan_task()))
        judged = self._findings(agent_with(enthusiastic).execute(scan_task()))

        assert plain
        assert [f.score for f in plain] == [f.score for f in judged]
        assert [f.signals for f in plain] == [f.signals for f in judged]

    def test_the_rendered_summary_is_unchanged(self):
        assert (
            agent_with().execute(scan_task()).output
            == agent_with(ScriptedBrain()).execute(scan_task()).output
        )

    def test_the_existing_payload_keys_are_unchanged(self):
        plain = agent_with().execute(scan_task()).payload
        judged = agent_with(ScriptedBrain()).execute(scan_task()).payload
        assert set(judged) - set(plain) == {"insight"}

    def test_counts_are_unchanged(self):
        plain = agent_with().execute(scan_task()).payload
        judged = agent_with(ScriptedBrain()).execute(scan_task()).payload
        assert plain["considered"] == judged["considered"]
        assert plain["analyzer"] == judged["analyzer"]

    def test_the_deterministic_modules_were_not_touched(self):
        import src.agents.email.analyzers as analyzers
        import src.agents.email.opportunity_analysis as pipeline

        for module in (analyzers, pipeline):
            code = code_of(module)
            assert "specialistbrain" not in code
            assert "insight" not in code
            assert "cognitive" not in code


# ----------------------------------------------------------------------
# Failure isolation and honest defaults
# ----------------------------------------------------------------------


class TestReasoningFailureIsContained:
    def test_a_raising_brain_costs_judgement_not_the_task(self):
        result = agent_with(ScriptedBrain(fail=RuntimeError("kaboom"))).execute(scan_task())
        assert result.status is TaskStatus.COMPLETED
        assert insight_of(result) is None

    def test_a_raising_brain_leaves_the_findings_intact(self):
        broken = agent_with(ScriptedBrain(fail=RuntimeError("kaboom"))).execute(scan_task())
        plain = agent_with().execute(scan_task())
        assert broken.payload[FINDINGS_KEY] == plain.payload[FINDINGS_KEY]

    def test_a_brain_returning_nonsense_is_rejected_not_coerced(self):
        class Rogue(SpecialistBrain):
            @property
            def name(self):
                return "rogue"

            def consider(self, task, notes=()):
                return "just apply to everything"

        result = agent_with(Rogue()).execute(scan_task())
        assert result.status is TaskStatus.COMPLETED
        assert insight_of(result) is None

    def test_a_failing_memory_does_not_stop_reasoning(self):
        class ExplodingMemory(Memory):
            def remember(self, record):
                return record

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                raise RuntimeError("the store is on fire")

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        brain = ScriptedBrain()
        result = agent_with(brain, ExplodingMemory()).execute(scan_task())
        assert result.status is TaskStatus.COMPLETED
        assert len(brain.calls) == 1
        assert not any(n.startswith(MEMORY_NOTE_PREFIX) for n in brain.last_notes)

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        with pytest.raises(KeyboardInterrupt):
            agent_with(ScriptedBrain(fail=KeyboardInterrupt())).execute(scan_task())

    def test_a_failed_brain_is_logged(self, caplog):
        import logging

        with caplog.at_level(logging.ERROR):
            agent_with(ScriptedBrain(fail=RuntimeError("kaboom"))).execute(scan_task())
        assert "brain raised" in caplog.text


class TestHonestDefaults:
    def test_the_default_brain_is_never_called(self):
        agent = agent_with()
        assert isinstance(agent.brain, NullSpecialistBrain)
        assert agent.reason_about(scan_task()) is None

    def test_no_insight_key_appears_without_a_brain(self):
        assert "insight" not in agent_with().execute(scan_task()).payload

    def test_a_missing_insight_is_not_an_empty_one(self):
        """Absence means "did not reason", not "had no concerns"."""
        assert insight_of(agent_with().execute(scan_task())) is None

    def test_the_default_memory_is_empty_and_safe(self):
        agent = agent_with()
        assert len(agent.memory) == 0
        assert agent.recall_context(scan_task()) == ()

    def test_an_agent_with_no_configuration_still_works(self):
        result = EmailAgent(InMemoryMailbox([])).execute(scan_task())
        assert result.status is TaskStatus.COMPLETED


# ----------------------------------------------------------------------
# Recording stays unimplemented
# ----------------------------------------------------------------------


class TestRecording:
    """Retargeted: automatic specialist recording is now implemented.

    These asserted the absence of exactly this feature. See
    test_specialist_memory.py for the full coverage; what remains here is the
    boundary that still holds - recording never bypasses the policy, and the
    specialist never deletes.
    """

    def test_a_confident_reflection_is_recorded(self):
        agent = agent_with(ScriptedBrain())
        agent.execute(scan_task())
        assert len(agent.memory) == 1

    def test_recording_happens_once_per_execution(self):
        agent = agent_with(ScriptedBrain())
        for _ in range(5):
            agent.execute(scan_task())
        assert len(agent.memory) == 5

    def test_a_default_agent_still_records_nothing(self):
        """No brain configured means no reflection, so nothing is proposed."""
        agent = agent_with()
        agent.execute(scan_task())
        assert len(agent.memory) == 0

    def test_the_specialist_writes_only_through_the_gated_path(self):
        """One write site, and it is the one that consults the policy."""
        import ast

        import src.agents.cognitive_agent as cognitive
        import src.agents.email_agent as email_agent

        writers = set()
        for module in (cognitive, email_agent):
            tree = ast.parse(open(module.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                        if inner.func.attr == "remember":
                            writers.add(node.name)
                        assert inner.func.attr not in ("forget", "clear")

        assert writers == {"remember_experience"}

    def test_a_specialist_memory_policy_exists_and_is_not_the_managers(self):
        from src.agents.memory_policy import SpecialistMemoryPolicy
        from src.manager.memory_policy import ManagerMemoryPolicy

        agent = agent_with(ScriptedBrain())
        assert isinstance(agent.memory_policy, SpecialistMemoryPolicy)
        assert not isinstance(agent.memory_policy, ManagerMemoryPolicy)


# ----------------------------------------------------------------------
# Nothing leaks out
# ----------------------------------------------------------------------


class TestNothingLeaksOut:
    def test_no_anthropic_sdk_is_imported(self):
        agent_with(ScriptedBrain()).execute(scan_task())
        assert "anthropic" not in sys.modules

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert agent_with(ScriptedBrain()).execute(scan_task()).status is TaskStatus.COMPLETED

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = agent_with(ScriptedBrain(), EphemeralMemory())
        agent.execute(scan_task())
        agent.memory.remember(remembered("an observation"))
        assert list(tmp_path.iterdir()) == []

    def test_nothing_survives_a_new_instance(self):
        one = agent_with(ScriptedBrain(), EphemeralMemory())
        one.memory.remember(remembered("job digests are usually stale"))
        assert len(agent_with(ScriptedBrain()).memory) == 0

    def test_sending_is_still_refused(self):
        agent = agent_with(ScriptedBrain())
        assert agent.can_send is False
        result = agent.execute(scan_task("send an email to my team"))
        assert "cannot" in result.output.lower() or result.error

    def test_the_cognitive_modules_name_no_vendor(self):
        import src.agents.brain as brain_module
        import src.agents.cognitive_agent as cognitive

        for module in (brain_module, cognitive):
            code = code_of(module)
            for word in ("anthropic", "openai", "gmail", "api_key"):
                assert word not in code, word

    def test_the_contracts_are_unchanged(self):
        import inspect

        assert set(SpecialistBrain.__abstractmethods__) == {"name", "consider"}
        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }
        params = set(inspect.signature(SpecialistBrain.consider).parameters)
        assert params == {"self", "task", "notes"}

    def test_a_cognitive_agent_is_still_routable(self):
        agent = agent_with(ScriptedBrain())
        assert isinstance(agent, CognitiveAgent)
        assert agent.spec.name == "email_agent"
