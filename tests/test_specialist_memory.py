"""Tests for specialist automatic memory: policy, candidate, recording.

    domain work -> reflection -> proposed record -> POLICY -> own memory

The claims that carry the weight:

    TestRoutineWorkIsNotRemembered - a memory must not become a log
    TestThePolicyIsTheGate         - no write path skips admission
    TestMemoriesDoNotLeak          - across specialists, and to/from the Manager

No Anthropic, no Gmail, no network, no files.
"""

import socket
import sys

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.brain import Insight, NullSpecialistBrain, SpecialistBrain
from src.agents.cognitive_agent import CognitiveAgent
from src.agents.email_agent import EmailAgent
from src.agents.memory_policy import (
    MANAGER_CATEGORIES,
    MAX_CONTENT_CHARS,
    MIN_CONFIDENCE,
    SpecialistMemoryCategory,
    SpecialistMemoryPolicy,
)
from src.core.contracts import FINDINGS_KEY, AgentSpec, Task, TaskResult, TaskStatus
from src.manager.assessor import ContractAssessor
from src.manager.manager import ManagerAgent
from src.manager.memory_policy import ManagerMemoryPolicy, MemoryCategory
from src.manager.router import CapabilityRouter
from src.registry.agent_registry import AgentRegistry
from src.services.email.sample_inbox import sample_inbox
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord
from src.services.memory.policy import MemoryPolicy, PolicyVerdict


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


POLICY = SpecialistMemoryPolicy()

LESSON = "postings that only list a skill as desirable rarely require it"


class ScriptedBrain(SpecialistBrain):
    def __init__(self, insight=None, fail=None):
        self.insight = insight if insight is not None else Insight(
            summary=LESSON, confidence=0.8, source="scripted"
        )
        self.fail = fail
        self.calls = []

    @property
    def name(self):
        return "scripted"

    def consider(self, task, notes=()):
        self.calls.append(task)
        if self.fail is not None:
            raise self.fail
        return self.insight


def scan_task(description="scan my inbox for job opportunities"):
    return Task(description=description)


def record(content=LESSON, category=SpecialistMemoryCategory.DOMAIN_LESSON, **fields):
    kind = category.value if isinstance(category, SpecialistMemoryCategory) else category
    fields.setdefault("context", {"confidence": 0.8})
    return MemoryRecord(content=content, kind=kind, **fields)


def agent_with(brain=None, memory=None, **kwargs):
    return EmailAgent(sample_inbox(), brain=brain, memory=memory, **kwargs)


# ----------------------------------------------------------------------
# 1-3. A separate policy with its own vocabulary
# ----------------------------------------------------------------------


class TestTheSpecialistPolicyIsSeparate:
    def test_it_is_not_the_manager_policy(self):
        assert not isinstance(POLICY, ManagerMemoryPolicy)
        assert not issubclass(SpecialistMemoryPolicy, ManagerMemoryPolicy)

    def test_both_implement_the_same_generic_interface(self):
        assert isinstance(POLICY, MemoryPolicy)
        assert isinstance(ManagerMemoryPolicy(), MemoryPolicy)

    def test_it_has_its_own_categories(self):
        assert POLICY.category_names == (
            "audio_recording",
            "domain_lesson",
            "domain_pattern",
            "domain_preference",
            "recording",
            "user_task",
            "video_recording",
        )

    def test_there_are_exactly_seven(self):
        """Stage 12 added RECORDING - the same explicit "record this
        [for X]" capture Alfred has, available to an equipped specialist
        too (capture.py's own docstring: "shared by every agent that
        captures, never duplicated per agent"). Post-Pre-Stage-9 added TASK:
        every agent owns a Workspace task board. T-REC-03 (2026-09-22)
        added AUDIO_RECORDING and VIDEO_RECORDING: a real, genuinely
        captured recording's *artifact* already saved for any equipped
        specialist (the artifact store has no per-agent category gate),
        while the reference to it was silently refused here - found while
        building screen/camera capture, fixed the same "agent-generic on
        purpose" way RECORDING/TASK already are."""
        assert len(SpecialistMemoryCategory) == 7

    def test_no_category_overlaps_the_managers_except_the_shared_recording_concepts(self):
        """Every category still means something exclusive to its own
        vocabulary, with deliberate exceptions: RECORDING, and now
        AUDIO_RECORDING/VIDEO_RECORDING, are the identical concept in
        both - an explicit capture request - applied to each agent's own
        separate store. Stage 12 asks for this symmetry explicitly ("do
        not create separate Alfred and specialist implementations"), so
        the two vocabularies sharing these names is by design, not a
        naming collision."""
        specialist = set(POLICY.category_names)
        manager = set(ManagerMemoryPolicy().category_names)
        # user_task joined recording post-Pre-Stage-9; audio_recording/
        # video_recording joined with T-REC-03 - each names a fact real
        # for both a manager and a specialist, kept in each agent's own store.
        assert specialist & manager == {"recording", "user_task", "audio_recording", "video_recording"}

    @pytest.mark.parametrize("manager_kind", list(MANAGER_CATEGORIES))
    def test_a_manager_category_is_refused(self, manager_kind):
        verdict = POLICY.evaluate(record(category=manager_kind))
        assert not verdict.allowed

    def test_the_refusal_explains_whose_category_it_is(self):
        verdict = POLICY.evaluate(record(category=MemoryCategory.DELEGATION_OUTCOME.value))
        assert "manager" in verdict.reason.lower()

    def test_there_is_no_domain_outcome_category(self):
        """Deliberately absent: it duplicates the Manager and stores history."""
        assert "domain_outcome" not in POLICY.category_names
        assert not POLICY.permits(record(category="domain_outcome"))

    def test_the_specialist_module_does_not_import_the_manager(self):
        import src.agents.memory_policy as module

        assert "from src.manager" not in code_of(module)

    def test_the_memory_contract_is_unchanged(self):
        assert set(Memory.__abstractmethods__) == {
            "remember",
            "recall",
            "forget",
            "clear",
        }

    def test_memory_does_not_import_the_policy(self):
        import src.services.memory.ephemeral as ephemeral
        import src.services.memory.memory as contract

        for module in (contract, ephemeral):
            assert "policy" not in code_of(module)

    def test_each_category_explains_itself(self):
        for category in SpecialistMemoryCategory:
            assert category.meaning.strip() and category.label.strip()

    def test_the_policy_can_describe_itself(self):
        described = POLICY.describe()
        for category in SpecialistMemoryCategory:
            assert category.label in described


# ----------------------------------------------------------------------
# THE PRINCIPLE: routine work is not remembered
# ----------------------------------------------------------------------


class TestRoutineWorkIsNotRemembered:
    @pytest.mark.parametrize(
        "content",
        [
            "examined 4 messages",
            "processed 12 emails from the inbox",
            "analyzed 7 postings this run",
            "found 3 matches in the inbox",
            "processed the morning batch",
            "task t-41 completed successfully",
        ],
    )
    def test_execution_narration_is_refused(self, content):
        verdict = POLICY.evaluate(record(content=content))
        assert not verdict.allowed
        assert "routine" in verdict.reason or "what happened during a run" in verdict.reason

    def test_a_durable_lesson_is_admitted(self):
        assert POLICY.permits(
            record("postings requiring a demonstrated skill repeatedly rejected this profile")
        )

    def test_a_stated_preference_is_admitted(self):
        assert POLICY.permits(
            record(
                "this client only advertises roles involving Python automation",
                category=SpecialistMemoryCategory.DOMAIN_PREFERENCE,
            )
        )

    def test_a_pattern_is_admitted(self):
        assert POLICY.permits(
            record(
                "digests from this board list salary only for senior roles",
                category=SpecialistMemoryCategory.DOMAIN_PATTERN,
            )
        )

    def test_a_tentative_observation_is_refused(self):
        verdict = POLICY.evaluate(record(context={"confidence": 0.2}))
        assert not verdict.allowed
        assert "confidence" in verdict.reason

    def test_the_confidence_threshold_is_explicit(self):
        assert 0.0 < MIN_CONFIDENCE <= 1.0
        assert POLICY.permits(record(context={"confidence": MIN_CONFIDENCE}))
        assert not POLICY.permits(record(context={"confidence": MIN_CONFIDENCE - 0.01}))

    def test_missing_confidence_counts_as_none(self):
        assert not POLICY.permits(record(context={}))

    @pytest.mark.parametrize("bad", [True, "very sure", 1.5, -0.2, None, object()])
    def test_an_unreadable_confidence_is_not_certainty(self, bad):
        """A malformed value must not talk its way in."""
        assert not POLICY.permits(record(context={"confidence": bad}))

    def test_a_long_dump_is_refused(self):
        assert not POLICY.permits(record("x" * (MAX_CONTENT_CHARS + 1)))

    def test_a_secret_is_refused_under_every_category(self):
        for category in SpecialistMemoryCategory:
            assert not POLICY.permits(record("api_key=sk-ant-abc123def456ghi", category=category))

    def test_the_refusal_never_quotes_the_secret(self):
        verdict = POLICY.evaluate(record("password: hunter2"))
        assert "hunter2" not in verdict.reason

    def test_talking_about_credentials_is_not_a_secret(self):
        assert POLICY.permits(
            record("this board asks applicants to store credentials in env vars")
        )

    def test_a_non_record_is_refused_rather_than_crashing(self):
        for bad in ("a string", 42, None, {"content": "x"}):
            assert not POLICY.evaluate(bad).allowed

    def test_the_policy_is_deterministic(self):
        item = record()
        assert POLICY.evaluate(item).reason == POLICY.evaluate(item).reason

    def test_the_policy_holds_no_state(self):
        policy = SpecialistMemoryPolicy()
        before = dict(vars(policy))
        for _ in range(10):
            policy.evaluate(record())
        assert vars(policy) == before

    def test_the_policy_needs_no_model(self):
        import src.agents.memory_policy as module

        code = code_of(module)
        for word in ("provider", "llm", "complete(", "anthropic", "brain"):
            assert word not in code, word


# ----------------------------------------------------------------------
# 4-12. Recording through the agent
# ----------------------------------------------------------------------


class TestRecordingThroughTheAgent:
    def test_a_routine_run_with_no_brain_records_nothing(self):
        agent = agent_with()
        agent.execute(scan_task())
        assert len(agent.memory) == 0

    def test_a_confident_reflection_is_recorded(self):
        agent = agent_with(ScriptedBrain())
        agent.execute(scan_task())

        records = agent.memory.recall(limit=10)
        assert len(records) == 1
        assert records[0].kind == SpecialistMemoryCategory.DOMAIN_LESSON.value
        assert records[0].content == LESSON

    def test_an_unconfident_reflection_is_not_recorded(self):
        weak = ScriptedBrain(Insight(summary=LESSON, confidence=0.1, source="scripted"))
        agent = agent_with(weak)
        agent.execute(scan_task())
        assert len(agent.memory) == 0

    def test_a_zero_confidence_reflection_proposes_nothing(self):
        none = ScriptedBrain(Insight.none(reason="could not judge", source="scripted"))
        agent = agent_with(none)
        agent.execute(scan_task())
        assert len(agent.memory) == 0

    def test_recording_happens_at_most_once_per_execution(self):
        agent = agent_with(ScriptedBrain())
        agent.execute(scan_task())
        assert len(agent.memory) == 1

    def test_repeated_executions_record_once_each(self):
        agent = agent_with(ScriptedBrain())
        for _ in range(4):
            agent.execute(scan_task())
        assert len(agent.memory) == 4

    def test_no_candidate_means_no_write(self):
        class NoProposal(CognitiveAgent):
            @property
            def spec(self):
                return AgentSpec(name="quiet", description="q", capabilities=("q",))

            def execute(self, task):
                return TaskResult.success(task, "quiet", "done")

        agent = NoProposal(memory=EphemeralMemory())
        assert agent.propose_memory(scan_task(), None) is None
        assert agent.remember_experience(scan_task(), None) is None
        assert len(agent.memory) == 0

    def test_the_default_proposal_is_nothing(self):
        """A specialist remembers nothing until it opts in."""
        assert CognitiveAgent.propose_memory(object(), scan_task(), None) is None

    def test_recording_is_skipped_on_a_send_refusal(self):
        agent = agent_with(ScriptedBrain())
        result = agent.execute(scan_task("send an email to my team"))
        assert result.status is TaskStatus.FAILED
        assert len(agent.memory) == 0

    def test_recording_is_skipped_on_a_mailbox_failure(self):
        from src.services.email.mailbox import Mailbox, MailboxError

        class BrokenMailbox(Mailbox):
            def fetch(self, limit=20, label=None):
                raise MailboxError("unavailable")

        agent = EmailAgent(BrokenMailbox(), brain=ScriptedBrain(), memory=EphemeralMemory())
        result = agent.execute(scan_task())
        assert result.status is TaskStatus.FAILED
        assert len(agent.memory) == 0

    def test_recording_happens_after_domain_work_and_reflection(self):
        order = []

        class OrderedBrain(ScriptedBrain):
            def consider(self, task, notes=()):
                order.append("reflect")
                return super().consider(task, notes)

        class WatchingMemory(EphemeralMemory):
            def remember(self, record):
                order.append("remember")
                return super().remember(record)

        class WatchingAgent(EmailAgent):
            def _select_analyzer(self, tokens):
                order.append("domain work")
                return super()._select_analyzer(tokens)

        agent = WatchingAgent(
            sample_inbox(), brain=OrderedBrain(), memory=WatchingMemory()
        )
        agent.execute(scan_task())
        assert order == ["domain work", "reflect", "remember"]

    def test_the_recorded_content_is_deterministic(self):
        one, two = agent_with(ScriptedBrain()), agent_with(ScriptedBrain())
        one.execute(scan_task())
        two.execute(scan_task())

        first = one.memory.recall(limit=1)[0]
        second = two.memory.recall(limit=1)[0]
        assert (first.content, first.kind, first.tags) == (
            second.content,
            second.kind,
            second.tags,
        )

    def test_no_message_content_is_recorded(self):
        agent = agent_with(ScriptedBrain())
        agent.execute(scan_task())
        stored = agent.memory.recall(limit=10)[0]
        blob = stored.content + str(stored.context)
        for leaked in ("@", "subject:", "from:"):
            assert leaked not in blob.lower()

    def test_the_record_is_tagged_with_the_agent(self):
        agent = agent_with(ScriptedBrain())
        agent.execute(scan_task())
        assert "email_agent" in agent.memory.recall(limit=1)[0].tags


# ----------------------------------------------------------------------
# 7-8. THE GATE
# ----------------------------------------------------------------------


class TestThePolicyIsTheGate:
    def test_the_policy_is_consulted_before_every_write(self):
        seen = []

        class RecordingPolicy(MemoryPolicy):
            def __init__(self):
                self._real = SpecialistMemoryPolicy()

            def evaluate(self, record):
                seen.append(record)
                return self._real.evaluate(record)

        agent = agent_with(ScriptedBrain(), memory_policy=RecordingPolicy())
        agent.execute(scan_task())

        assert len(seen) == 1
        assert len(agent.memory) == 1

    def test_a_refusing_policy_stores_nothing(self):
        class RefuseEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="refused for this test")

        agent = agent_with(ScriptedBrain(), memory_policy=RefuseEverything())
        result = agent.execute(scan_task())

        assert result.status is TaskStatus.COMPLETED   # the work still ran
        assert len(agent.memory) == 0

    def test_the_policy_is_injectable(self):
        strict = SpecialistMemoryPolicy(min_confidence=0.99)
        assert agent_with(ScriptedBrain(), memory_policy=strict).memory_policy is strict

    def test_a_stricter_policy_changes_what_is_kept(self):
        loose = agent_with(ScriptedBrain(), memory_policy=SpecialistMemoryPolicy())
        strict = agent_with(
            ScriptedBrain(), memory_policy=SpecialistMemoryPolicy(min_confidence=0.99)
        )
        loose.execute(scan_task())
        strict.execute(scan_task())

        assert len(loose.memory) == 1
        assert len(strict.memory) == 0

    def test_there_is_no_write_that_skips_the_policy(self):
        """Structural: every write site consults the policy first.

        Two now, not one - Stage 12 added ``CognitiveAgent.retain``, the
        specialist's own counterpart to ``ManagerAgent.retain`` for an
        explicit request (as opposed to ``remember_experience``, this
        agent's own proposal about its own completed work). What matters
        - ``writers <= judges`` - is unchanged: neither writer calls
        ``remember`` without first calling ``evaluate``.
        """
        import ast

        import src.agents.cognitive_agent as cognitive
        import src.agents.email_agent as email_agent

        writers, judges = set(), set()
        for module in (cognitive, email_agent):
            tree = ast.parse(open(module.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                        if inner.func.attr == "remember":
                            writers.add(node.name)
                        if inner.func.attr == "evaluate":
                            judges.add(node.name)

        assert writers == {"remember_experience", "retain"}
        assert writers <= judges

    def test_the_specialist_never_deletes(self):
        import ast

        import src.agents.cognitive_agent as cognitive
        import src.agents.email_agent as email_agent

        for module in (cognitive, email_agent):
            tree = ast.parse(open(module.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("forget", "clear")

    def test_the_policy_stores_nothing_itself(self):
        public = {n for n in dir(MemoryPolicy) if not n.startswith("_")}
        for storing in ("remember", "store", "save", "write"):
            assert storing not in public


# ----------------------------------------------------------------------
# 13-15. Fault isolation
# ----------------------------------------------------------------------


class TestFaultIsolation:
    def test_a_memory_write_failure_does_not_fail_the_task(self):
        class ExplodingMemory(EphemeralMemory):
            def remember(self, record):
                raise RuntimeError("the store is on fire")

        agent = agent_with(ScriptedBrain(), memory=ExplodingMemory())
        result = agent.execute(scan_task())

        assert result.status is TaskStatus.COMPLETED
        assert result.payload[FINDINGS_KEY] is not None

    def test_a_policy_failure_does_not_fail_the_task(self):
        class ExplodingPolicy(MemoryPolicy):
            def evaluate(self, record):
                raise RuntimeError("policy blew up")

        agent = agent_with(ScriptedBrain(), memory_policy=ExplodingPolicy())
        assert agent.execute(scan_task()).status is TaskStatus.COMPLETED

    def test_a_bad_proposal_is_rejected_not_stored(self):
        class BadProposer(EmailAgent):
            def propose_memory(self, task, insight, outcome=None):
                return "not a memory record"

        agent = BadProposer(sample_inbox(), brain=ScriptedBrain(), memory=EphemeralMemory())
        assert agent.execute(scan_task()).status is TaskStatus.COMPLETED
        assert len(agent.memory) == 0

    def test_a_raising_proposal_is_contained(self):
        class RaisingProposer(EmailAgent):
            def propose_memory(self, task, insight, outcome=None):
                raise RuntimeError("proposal blew up")

        agent = RaisingProposer(sample_inbox(), brain=ScriptedBrain(), memory=EphemeralMemory())
        assert agent.execute(scan_task()).status is TaskStatus.COMPLETED

    def test_a_failure_is_logged(self, caplog):
        import logging

        class ExplodingMemory(EphemeralMemory):
            def remember(self, record):
                raise RuntimeError("the store is on fire")

        agent = agent_with(ScriptedBrain(), memory=ExplodingMemory())
        with caplog.at_level(logging.ERROR):
            agent.execute(scan_task())
        assert "could not record" in caplog.text

    def test_a_keyboard_interrupt_propagates(self):
        class InterruptingMemory(EphemeralMemory):
            def remember(self, record):
                raise KeyboardInterrupt

        agent = agent_with(ScriptedBrain(), memory=InterruptingMemory())
        with pytest.raises(KeyboardInterrupt):
            agent.execute(scan_task())

    def test_a_system_exit_propagates(self):
        class ExitingMemory(EphemeralMemory):
            def remember(self, record):
                raise SystemExit(1)

        agent = agent_with(ScriptedBrain(), memory=ExitingMemory())
        with pytest.raises(SystemExit):
            agent.execute(scan_task())


# ----------------------------------------------------------------------
# 16-21, 30. Isolation
# ----------------------------------------------------------------------


class TestMemoriesDoNotLeak:
    def _managed(self, agent):
        registry = AgentRegistry()
        registry.register(agent)
        return ManagerAgent(registry=registry)

    def test_specialist_a_cannot_see_specialist_b(self):
        one = agent_with(ScriptedBrain(), memory=EphemeralMemory())
        two = agent_with(ScriptedBrain(), memory=EphemeralMemory())

        one.execute(scan_task())

        assert len(one.memory) == 1
        assert len(two.memory) == 0
        assert two.memory.recall("postings") == ()

    def test_a_specialist_cannot_see_manager_memory(self):
        agent = agent_with(ScriptedBrain())
        manager = self._managed(agent)
        manager.memory.remember(
            MemoryRecord(content="something only the manager knows", kind="observation")
        )

        manager.handle_task(scan_task())
        assert agent.memory.recall("something") == ()

    def test_the_manager_cannot_see_specialist_memory(self):
        agent = agent_with(ScriptedBrain())
        manager = self._managed(agent)

        manager.handle_task(scan_task())

        assert agent.memory is not manager.memory
        assert manager.memory.recall("postings") == ()

    def test_each_keeps_its_own_categories(self):
        agent = agent_with(ScriptedBrain())
        manager = self._managed(agent)
        manager.handle_task(scan_task())

        specialist_kinds = {r.kind for r in agent.memory.recall(limit=10)}
        manager_kinds = {r.kind for r in manager.memory.recall(limit=10)}

        assert specialist_kinds == {"domain_lesson"}
        assert manager_kinds == {"delegation_outcome"}
        assert specialist_kinds & manager_kinds == set()

    def test_many_specialists_stay_isolated(self):
        agents = [agent_with(ScriptedBrain(), memory=EphemeralMemory()) for _ in range(5)]
        agents[0].execute(scan_task())

        assert len(agents[0].memory) == 1
        assert all(len(a.memory) == 0 for a in agents[1:])
        assert len({id(a.memory) for a in agents}) == 5

    def test_specialist_memory_does_not_reach_the_router(self):
        seen = {}

        class RecordingRouter(CapabilityRouter):
            def route(self, task, agents):
                seen["args"] = (task, tuple(agents))
                return super().route(task, agents)

        agent = agent_with(ScriptedBrain())
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(registry=registry, router=RecordingRouter())
        manager.handle_task(scan_task())

        task, _ = seen["args"]
        assert not hasattr(task, "memory")

    def test_specialist_memory_does_not_reach_the_assessor(self):
        seen = []

        class RecordingAssessor(ContractAssessor):
            def assess(self, task, agent_name, result):
                seen.append(result)
                return super().assess(task, agent_name, result)

        agent = agent_with(ScriptedBrain())
        registry = AgentRegistry()
        registry.register(agent)
        ManagerAgent(registry=registry, assessor=RecordingAssessor()).handle_task(scan_task())

        assert len(seen) == 1
        assert "memory" not in (seen[0].payload or {})

    def test_the_orchestration_layer_does_not_reach_specialist_memory(self):
        """The boundary, checked against the canonical orchestration path.

        Until 2026-09-15 this was checked against ``src/manager/actions.py``,
        a staged action layer with no production caller, removed with the rest
        of that parallel architecture. The property it protected is real and
        belongs to whatever actually orchestrates - today ``objective_loop``
        and ``collaboration_entry``."""
        import src.manager.collaboration_entry as collaboration
        import src.manager.objective_loop as objective_loop

        for module in (objective_loop, collaboration):
            code = code_of(module)
            assert "from src.services.memory" not in code, module.__name__
            assert ".memory" not in code, module.__name__

    def test_orchestration_never_touches_specialist_memory(self):
        """Run a real delegation through the canonical path and prove the
        specialist's own store grew only by its own reflection."""
        from src.manager.collaboration_entry import answer_with_collaboration

        agent = agent_with(ScriptedBrain())
        registry = AgentRegistry()
        registry.register(agent)
        manager = ManagerAgent(registry=registry)

        before = len(agent.memory)
        answer_with_collaboration(manager, scan_task())
        assert len(agent.memory) == before + 1
        assert all(r.kind == "domain_lesson" for r in agent.memory.recall(limit=10))


# ----------------------------------------------------------------------
# 22-24. Brain boundaries and the deterministic pipeline
# ----------------------------------------------------------------------


class TestBoundariesHold:
    def test_the_brain_cannot_write_memory(self):
        import ast

        import src.agents.brain as brain_module
        import src.agents.llm_brain as llm_brain

        for module in (brain_module, llm_brain):
            tree = ast.parse(open(module.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("remember", "recall", "forget", "clear")

    def test_the_brain_holds_no_store(self):
        brain = ScriptedBrain()
        agent_with(brain).execute(scan_task())
        for attribute in ("memory", "memory_policy", "registry", "router"):
            assert not hasattr(brain, attribute), attribute

    def test_the_brain_receives_only_a_task_and_notes(self):
        import inspect

        params = set(inspect.signature(SpecialistBrain.consider).parameters)
        assert params == {"self", "task", "notes"}

    def test_the_brain_executes_no_agent(self):
        import src.agents.llm_brain as module

        code = code_of(module)
        assert ".execute(" not in code
        assert "from src.registry" not in code

    def test_the_deterministic_analysis_is_unchanged(self):
        plain = EmailAgent(sample_inbox()).execute(scan_task())
        judged = agent_with(ScriptedBrain()).execute(scan_task())

        assert plain.output == judged.output
        assert plain.payload[FINDINGS_KEY] == judged.payload[FINDINGS_KEY]
        assert plain.payload["considered"] == judged.payload["considered"]

    def test_the_deterministic_modules_were_not_touched(self):
        import src.agents.email.analyzers as analyzers
        import src.agents.email.opportunity_analysis as pipeline

        for module in (analyzers, pipeline):
            code = code_of(module)
            for word in ("memory", "policy", "insight", "specialist"):
                assert word not in code, word

    def test_sending_is_still_refused(self):
        assert agent_with(ScriptedBrain()).can_send is False


# ----------------------------------------------------------------------
# 27-28. Nothing new was connected
# ----------------------------------------------------------------------


class TestNothingNewWasConnected:
    def test_no_persistent_storage(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = agent_with(ScriptedBrain())
        agent.execute(scan_task())
        assert len(agent.memory) == 1
        assert list(tmp_path.iterdir()) == []

    def test_nothing_survives_a_new_instance(self):
        agent_with(ScriptedBrain()).execute(scan_task())
        assert len(agent_with(ScriptedBrain()).memory) == 0

    def test_no_database_driver_is_imported(self):
        import src.agents.memory_policy as policy_module
        import src.services.memory.secrets as secrets_module

        for module in (policy_module, secrets_module):
            code = code_of(module)
            for driver in ("sqlite", "redis", "psycopg", "sqlalchemy"):
                assert driver not in code, driver

    def test_no_anthropic_sdk_is_imported(self):
        agent_with(ScriptedBrain()).execute(scan_task())
        assert "anthropic" not in sys.modules

    def test_no_provider_is_imported(self):
        import src.agents.memory_policy as module

        assert "from src.providers" not in code_of(module)

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        agent = agent_with(ScriptedBrain())
        assert agent.execute(scan_task()).status is TaskStatus.COMPLETED

    def test_the_null_brain_still_needs_no_configuration(self):
        agent = EmailAgent(sample_inbox())
        assert isinstance(agent.brain, NullSpecialistBrain)
        assert agent.execute(scan_task()).status is TaskStatus.COMPLETED
