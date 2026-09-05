"""Tests for automatic DELEGATION_OUTCOME recording.

Three claims carry the weight here:

    TestTheRecordIsSafe          - no message content, no secrets, no reasoning
    TestMemoryFailureIsContained - a broken store cannot break a task
    TestOnlyDelegationIsRecorded - self-handled work and the other three
                                   categories stay out

No Anthropic, no Gmail, no files, no network. Every agent and memory here is a
test double.
"""

import socket

import pytest

from src.agents.base_agent import BaseAgent
from src.core.contracts import (
    FINDINGS_KEY,
    AgentSpec,
    Task,
    TaskResult,
    TaskStatus,
)
from src.manager.manager import RECORDED_OUTCOMES, ManagerAgent, MissionOutcome
from src.manager.memory_policy import ManagerMemoryPolicy, MemoryCategory
from src.registry.agent_registry import AgentRegistry
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


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------


class SpecialistAgent(BaseAgent):
    """A specialist whose result can be scripted."""

    def __init__(self, name="research_agent", capabilities=("research",), result=None):
        self._name = name
        self._capabilities = tuple(capabilities)
        self._result = result

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
        if callable(self._result):
            return self._result(task)
        if self._result is not None:
            return self._result
        return TaskResult.success(task, agent_name=self._name, output="done")


class ManagerSkillsStub(BaseAgent):
    """Stands in for the Manager's own capabilities."""

    @property
    def name(self):
        return "manager"

    @property
    def spec(self):
        return AgentSpec(
            name="manager",
            description="the manager's own coordination skills",
            capabilities=("coordinate", "report"),
        )

    def execute(self, task):
        return TaskResult.success(task, agent_name="manager", output="coordinated")


def manager_with(agents=(), memory=None, **kwargs):
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return ManagerAgent(
        registry=registry,
        memory=memory if memory is not None else EphemeralMemory(),
        **kwargs,
    )


def a_task(description="research something", **fields):
    return Task(description=description, **fields)


def records_of(manager):
    return manager.memory.recall(limit=100)


def delegate_once(agent=None, task=None, **kwargs):
    """Run one delegated task and return (manager, records)."""
    manager = manager_with(agents=(agent or SpecialistAgent(),), **kwargs)
    manager.handle_task(task or a_task())
    return manager, records_of(manager)


# ----------------------------------------------------------------------
# 1-4. One record, correctly shaped
# ----------------------------------------------------------------------


class TestADelegationIsRecorded:
    def test_exactly_one_record_is_created(self):
        _, records = delegate_once()
        assert len(records) == 1

    def test_the_category_is_delegation_outcome(self):
        _, records = delegate_once()
        assert records[0].kind == MemoryCategory.DELEGATION_OUTCOME.value

    def test_the_record_identifies_the_specialist(self):
        _, records = delegate_once(SpecialistAgent(name="research_agent"))
        record = records[0]
        assert "research_agent" in record.content
        assert record.context["agent"] == "research_agent"
        assert "research_agent" in record.tags

    def test_the_record_captures_the_outcome(self):
        _, records = delegate_once()
        record = records[0]
        assert record.context["outcome"] == MissionOutcome.ACCEPTED.value
        assert record.context["status"] == TaskStatus.COMPLETED.value

    def test_the_record_identifies_the_task(self):
        task = a_task("research the freelance market")
        _, records = delegate_once(task=task)
        assert records[0].context["task_id"] == task.id
        assert "research the freelance market" in records[0].content

    def test_the_record_is_timestamped(self):
        _, records = delegate_once()
        assert records[0].created_at is not None

    def test_a_failed_delegation_is_recorded(self):
        agent = SpecialistAgent(
            result=lambda task: TaskResult.failure(
                task, agent_name="research_agent", error="upstream unavailable"
            )
        )
        _, records = delegate_once(agent)
        assert len(records) == 1
        assert records[0].context["outcome"] == MissionOutcome.RETRY.value
        assert "upstream unavailable" in records[0].content

    def test_a_rejected_delegation_is_recorded(self):
        """A broken contract is exactly the kind of thing worth remembering."""
        agent = SpecialistAgent(result=lambda task: "not a TaskResult at all")
        _, records = delegate_once(agent)
        assert len(records) == 1
        assert records[0].context["outcome"] == MissionOutcome.REJECTED.value

    def test_findings_are_counted_not_copied(self):
        agent = SpecialistAgent(
            result=lambda task: TaskResult.success(
                task,
                agent_name="research_agent",
                output="a long rendered report",
                payload={FINDINGS_KEY: ("one", "two", "three")},
            )
        )
        _, records = delegate_once(agent)
        record = records[0]
        assert record.context["findings"] == 3
        assert "3 finding(s)" in record.content
        assert "one" not in record.content        # the findings themselves stay out

    def test_a_result_with_no_payload_records_no_finding_count(self):
        _, records = delegate_once()
        assert "findings" not in records[0].context

    def test_the_recorded_outcomes_are_explicit(self):
        assert RECORDED_OUTCOMES == {
            MissionOutcome.ACCEPTED,
            MissionOutcome.RETRY,
            MissionOutcome.FAILED,
            MissionOutcome.REJECTED,
        }

    def test_nothing_new_is_not_recorded(self):
        """A run that found nothing changes no future decision."""
        agent = SpecialistAgent(
            result=lambda task: TaskResult.success(
                task, agent_name="research_agent", output="nothing found",
                payload={FINDINGS_KEY: ()},
            )
        )
        manager, records = delegate_once(agent)
        assert records == ()
        assert MissionOutcome.NOTHING_NEW not in RECORDED_OUTCOMES


# ----------------------------------------------------------------------
# 5, 16, 17. Only delegation, only this category
# ----------------------------------------------------------------------


class TestOnlyDelegationIsRecorded:
    def test_work_the_manager_did_itself_is_not_recorded(self):
        skills = ManagerSkillsStub()
        manager = ManagerAgent(memory=EphemeralMemory(), own_skills=skills)
        result = manager.handle_task(a_task("coordinate the work"))

        assert result.agent_name == "manager"
        assert result.status is TaskStatus.COMPLETED
        assert records_of(manager) == ()

    def test_an_unroutable_task_is_not_recorded(self):
        """Nothing was delegated, so there is no delegation outcome."""
        manager = manager_with()
        result = manager.handle_task(a_task("something nobody can do"))
        assert result.status is TaskStatus.NO_AGENT_FOUND
        assert records_of(manager) == ()

    def test_user_context_is_never_populated(self):
        _, records = delegate_once()
        assert all(r.kind != MemoryCategory.USER_CONTEXT.value for r in records)

    def test_decision_is_never_populated(self):
        _, records = delegate_once()
        assert all(r.kind != MemoryCategory.DECISION.value for r in records)

    def test_lesson_is_never_populated(self):
        _, records = delegate_once()
        assert all(r.kind != MemoryCategory.LESSON.value for r in records)

    def test_deliberating_records_nothing(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        manager.deliberate(a_task())
        assert records_of(manager) == ()

    def test_reviewing_a_mission_records_nothing(self):
        """review_mission is a query, not a write."""
        manager = manager_with()
        task = a_task()
        manager.review_mission(task, TaskResult.success(task, "research_agent", "ok"))
        assert records_of(manager) == ()

    def test_no_specialist_memory_is_introduced(self):
        agent = SpecialistAgent()
        manager = manager_with(agents=(agent,))
        manager.handle_task(a_task())

        assert not hasattr(agent, "memory")
        assert len(manager.memory) == 1     # the memory belongs to the Manager

    def test_the_specialist_cannot_reach_manager_memory(self):
        """Nothing is handed to the agent but the task."""
        seen = {}

        def capture(task):
            seen["args"] = task
            return TaskResult.success(task, agent_name="research_agent", output="ok")

        manager = manager_with(agents=(SpecialistAgent(result=capture),))
        manager.handle_task(a_task())
        assert isinstance(seen["args"], Task)
        assert not hasattr(seen["args"], "memory")


# ----------------------------------------------------------------------
# 11-13. The record is safe
# ----------------------------------------------------------------------


class TestTheRecordIsSafe:
    def test_the_rendered_output_is_never_stored(self):
        """For the mail specialist this string is a report full of message text."""
        report = (
            "STRONG MATCH - Backend Developer at Acme\n"
            "  from      : recruiter@acme.example\n"
            "  details   : salary: 50000; deadline: Friday\n"
        )
        agent = SpecialistAgent(
            result=lambda task: TaskResult.success(
                task, agent_name="research_agent", output=report
            )
        )
        _, records = delegate_once(agent)

        stored = records[0].content + str(records[0].context)
        for leaked in ("recruiter@acme.example", "Backend Developer", "50000", "Friday"):
            assert leaked not in stored, leaked

    def test_no_raw_message_content_reaches_memory(self):
        raw = "From: someone@example.com\nSubject: We are hiring\n\nFull body text here."
        agent = SpecialistAgent(
            result=lambda task: TaskResult.success(
                task, agent_name="research_agent", output=raw,
                payload={FINDINGS_KEY: ("x",), "raw": raw},
            )
        )
        _, records = delegate_once(agent)
        stored = records[0].content + str(records[0].context)
        assert "someone@example.com" not in stored
        assert "Full body text here" not in stored

    def test_the_payload_is_not_copied_into_memory(self):
        agent = SpecialistAgent(
            result=lambda task: TaskResult.success(
                task, agent_name="research_agent", output="ok",
                payload={FINDINGS_KEY: ("x",), "secret_detail": "should not travel"},
            )
        )
        _, records = delegate_once(agent)
        assert "should not travel" not in str(records[0].context)

    def test_a_secret_in_the_task_description_is_refused(self):
        """The policy screens what the Manager builds, not just what a caller
        hands it directly."""
        manager, records = delegate_once(task=a_task("rotate api_key=sk-ant-abc123def456ghi"))
        assert records == ()

    def test_a_secret_in_an_error_is_refused(self):
        agent = SpecialistAgent(
            result=lambda task: TaskResult.failure(
                task, agent_name="research_agent",
                error="auth failed with password: hunter2",
            )
        )
        _, records = delegate_once(agent)
        assert records == ()

    def test_no_chain_of_thought_is_stored(self):
        """The brain's working-out never reaches the record."""
        manager = manager_with(agents=(SpecialistAgent(),))
        manager.deliberate(a_task())
        manager.handle_task(a_task())

        stored = " ".join(r.content for r in records_of(manager)).lower()
        for word in ("let me think", "step by step", "reasoning", "thinking"):
            assert word not in stored

    def test_stored_content_is_bounded(self):
        long_description = "research " + ("x" * 5000)
        _, records = delegate_once(task=a_task(long_description))
        assert len(records[0].content) <= ManagerMemoryPolicy().max_content_chars

    def test_a_long_error_is_summarised_not_dumped(self):
        agent = SpecialistAgent(
            result=lambda task: TaskResult.failure(
                task, agent_name="research_agent", error="failure detail " * 500
            )
        )
        _, records = delegate_once(agent)
        assert len(records) == 1
        assert len(records[0].content) < 400

    def test_the_policy_is_consulted_for_every_record(self):
        seen = []

        class RecordingPolicy(MemoryPolicy):
            def __init__(self):
                self._real = ManagerMemoryPolicy()

            def evaluate(self, record):
                seen.append(record)
                return self._real.evaluate(record)

        manager = manager_with(agents=(SpecialistAgent(),), memory_policy=RecordingPolicy())
        manager.handle_task(a_task())
        assert len(seen) == 1
        assert seen[0].kind == MemoryCategory.DELEGATION_OUTCOME.value

    def test_a_refusing_policy_leaves_memory_empty(self):
        class RefuseEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="refused for this test")

        manager = manager_with(agents=(SpecialistAgent(),), memory_policy=RefuseEverything())
        result = manager.handle_task(a_task())
        assert result.status is TaskStatus.COMPLETED
        assert records_of(manager) == ()


# ----------------------------------------------------------------------
# 6-8. The DATA loop is unchanged
# ----------------------------------------------------------------------


class TestTheDataLoopIsUnchanged:
    def test_the_returned_result_is_unchanged(self):
        task = a_task()
        with_memory = manager_with(agents=(SpecialistAgent(),)).handle_task(task)

        class RefuseEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="no memory for this run")

        without = manager_with(
            agents=(SpecialistAgent(),), memory_policy=RefuseEverything()
        ).handle_task(task)

        assert with_memory.status == without.status
        assert with_memory.agent_name == without.agent_name
        assert with_memory.output == without.output
        assert with_memory.error == without.error
        assert with_memory.payload == without.payload

    def test_assessment_still_runs_and_is_unchanged(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        task = a_task()
        failed = TaskResult.failure(task, agent_name="research_agent", error="down")
        assert manager.review_mission(task, failed) is MissionOutcome.RETRY

    def test_review_mission_still_returns_every_outcome(self):
        manager = manager_with()
        task = a_task()
        cases = {
            MissionOutcome.REJECTED: TaskResult.rejected(task, "a", "bad shape"),
            MissionOutcome.FAILED: TaskResult.no_agent(task, "nobody"),
            MissionOutcome.RETRY: TaskResult.failure(task, "a", "down"),
            MissionOutcome.ACCEPTED: TaskResult.success(task, "a", "ok"),
        }
        for expected, result in cases.items():
            assert manager.review_mission(task, result) is expected

    def test_recording_happens_after_assessment(self):
        """The record must reflect the assessed verdict, not the raw result.

        A specialist returning a malformed result is rejected by the Assessor;
        the memory shows REJECTED, which is only knowable post-assessment.
        """
        agent = SpecialistAgent(result=lambda task: {"not": "a result"})
        _, records = delegate_once(agent)
        assert records[0].context["outcome"] == MissionOutcome.REJECTED.value
        assert records[0].context["status"] == TaskStatus.REJECTED.value

    def test_recording_happens_after_the_specialist_ran(self):
        calls = []

        def run(task):
            calls.append(task)
            return TaskResult.success(task, agent_name="research_agent", output="ok")

        manager = manager_with(agents=(SpecialistAgent(result=run),))
        manager.handle_task(a_task())
        assert len(calls) == 1 and len(records_of(manager)) == 1

    def test_the_specialist_is_still_executed_exactly_once(self):
        calls = []

        def run(task):
            calls.append(task)
            return TaskResult.success(task, agent_name="research_agent", output="ok")

        manager_with(agents=(SpecialistAgent(result=run),)).handle_task(a_task())
        assert len(calls) == 1

    def test_routing_is_unchanged(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        assert manager.diagnose(a_task()).agent_name == "research_agent"

    def test_the_brain_is_unaffected(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        manager.handle_task(a_task())
        assert not manager.can_reason      # still the null brain


# ----------------------------------------------------------------------
# 9-10. Failure isolation
# ----------------------------------------------------------------------


class TestMemoryFailureIsContained:
    """Specialist failure is not memory failure. Neither corrupts the other."""

    class ExplodingMemory(Memory):
        def remember(self, record):
            raise RuntimeError("the store is on fire")

        def recall(self, query="", *, kind=None, tags=(), limit=10):
            return ()

        def forget(self, record_id):
            return False

        def clear(self):
            return 0

    def test_a_memory_failure_does_not_change_the_result(self):
        manager = manager_with(agents=(SpecialistAgent(),), memory=self.ExplodingMemory())
        result = manager.handle_task(a_task())

        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "research_agent"
        assert result.output == "done"
        assert result.error is None

    def test_a_memory_failure_does_not_change_the_assessment(self):
        manager = manager_with(agents=(SpecialistAgent(),), memory=self.ExplodingMemory())
        task = a_task()
        result = manager.handle_task(task)
        assert manager.review_mission(task, result) is MissionOutcome.ACCEPTED

    def test_a_memory_failure_is_logged(self, caplog):
        import logging

        manager = manager_with(agents=(SpecialistAgent(),), memory=self.ExplodingMemory())
        with caplog.at_level(logging.ERROR):
            manager.handle_task(a_task())
        assert "could not record" in caplog.text

    def test_a_failing_policy_does_not_break_the_task(self):
        class ExplodingPolicy(MemoryPolicy):
            def evaluate(self, record):
                raise RuntimeError("policy blew up")

        manager = manager_with(agents=(SpecialistAgent(),), memory_policy=ExplodingPolicy())
        assert manager.handle_task(a_task()).status is TaskStatus.COMPLETED

    def test_a_specialist_failure_still_produces_a_record(self):
        """The other direction: a failed task does not break memory."""
        agent = SpecialistAgent(
            result=lambda task: (_ for _ in ()).throw(RuntimeError("agent crashed"))
        )
        manager, records = delegate_once(agent)
        assert len(records) == 1
        assert records[0].context["outcome"] == MissionOutcome.RETRY.value

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        """Ctrl-C during a memory write must still stop the program."""

        class InterruptingMemory(Memory):
            def remember(self, record):
                raise KeyboardInterrupt

            def recall(self, query="", *, kind=None, tags=(), limit=10):
                return ()

            def forget(self, record_id):
                return False

            def clear(self):
                return 0

        manager = manager_with(agents=(SpecialistAgent(),), memory=InterruptingMemory())
        with pytest.raises(KeyboardInterrupt):
            manager.handle_task(a_task())


# ----------------------------------------------------------------------
# 14-15. Independence
# ----------------------------------------------------------------------


class TestRecordsAreIndependent:
    def test_multiple_delegations_produce_separate_records(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        for n in range(5):
            manager.handle_task(a_task(f"research topic {n}"))

        records = records_of(manager)
        assert len(records) == 5
        assert len({r.id for r in records}) == 5

    def test_each_record_names_its_own_task(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        tasks = [a_task(f"research topic {n}") for n in range(3)]
        for task in tasks:
            manager.handle_task(task)

        stored = {r.context["task_id"] for r in records_of(manager)}
        assert stored == {t.id for t in tasks}

    def test_two_managers_keep_separate_records(self):
        one = manager_with(agents=(SpecialistAgent(),))
        two = manager_with(agents=(SpecialistAgent(),))

        one.handle_task(a_task())

        assert len(one.memory) == 1
        assert len(two.memory) == 0

    def test_default_managers_do_not_share_records(self):
        one, two = ManagerAgent(), ManagerAgent()
        one.register_agent(SpecialistAgent())
        two.register_agent(SpecialistAgent())

        one.handle_task(a_task())
        assert len(one.memory) == 1 and len(two.memory) == 0

    def test_records_from_different_specialists_are_distinguishable(self):
        manager = manager_with(
            agents=(
                SpecialistAgent(name="research_agent", capabilities=("research",)),
                SpecialistAgent(name="writing_agent", capabilities=("write",)),
            )
        )
        manager.handle_task(a_task("research something"))
        manager.handle_task(a_task("write something"))

        agents = {r.context["agent"] for r in records_of(manager)}
        assert agents == {"research_agent", "writing_agent"}

    def test_records_can_be_recalled_by_specialist(self):
        """What makes this useful for future routing."""
        manager = manager_with(agents=(SpecialistAgent(name="research_agent"),))
        manager.handle_task(a_task())
        assert manager.memory.recall(tags=["research_agent"])


# ----------------------------------------------------------------------
# Structural
# ----------------------------------------------------------------------


class TestTheManagerStaysIndependent:
    def test_the_manager_still_names_no_vendor_or_specialist(self):
        import src.manager.manager as module

        source = open(module.__file__, encoding="utf-8").read().lower()
        for word in ("anthropic", "gmail", "provider", "email", "mailbox"):
            assert word not in source, word

    def test_the_manager_imports_no_specialist_module(self):
        import src.manager.manager as module

        code = code_of(module)
        for forbidden in ("from src.agents.email", "from src.services.email", "from src.providers"):
            assert forbidden not in code, forbidden

    def test_no_anthropic_sdk_is_imported(self):
        import sys

        delegate_once()
        assert "anthropic" not in sys.modules

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        delegate_once()
        assert list(tmp_path.iterdir()) == []

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        _, records = delegate_once()
        assert len(records) == 1

    def test_nothing_persists_between_managers(self):
        delegate_once()
        assert len(ManagerAgent().memory) == 0

    def test_the_record_type_is_the_existing_one(self):
        """No second execution-result model was invented."""
        _, records = delegate_once()
        assert isinstance(records[0], MemoryRecord)


# ----------------------------------------------------------------------
# Repeated execution is deterministic
# ----------------------------------------------------------------------


class TestRecordingIsDeterministic:
    """The same delegation must always produce the same record.

    Recording that varied run to run would make memory unreviewable: a user
    could not tell whether a difference in what was remembered reflected a
    difference in what happened.
    """

    def test_the_same_delegation_produces_the_same_content(self):
        task = a_task()
        first = delegate_once(task=task)[1][0]
        second = delegate_once(task=task)[1][0]
        assert first.content == second.content

    def test_the_same_delegation_produces_the_same_context(self):
        task = a_task()
        first = delegate_once(task=task)[1][0]
        second = delegate_once(task=task)[1][0]
        assert first.context == second.context

    def test_the_same_delegation_produces_the_same_category_and_tags(self):
        task = a_task()
        first = delegate_once(task=task)[1][0]
        second = delegate_once(task=task)[1][0]
        assert first.kind == second.kind
        assert first.tags == second.tags

    def test_repeating_a_task_on_one_manager_is_stable(self):
        manager = manager_with(agents=(SpecialistAgent(),))
        task = a_task()
        for _ in range(5):
            manager.handle_task(task)

        records = records_of(manager)
        assert len(records) == 5
        assert len({r.content for r in records}) == 1     # identical content
        assert len({r.id for r in records}) == 5          # distinct identities

    def test_only_identity_and_time_differ_between_runs(self):
        """Everything the policy judges is reproducible; only id and
        created_at are unique, which is what makes records addressable."""
        task = a_task()
        first = delegate_once(task=task)[1][0]
        second = delegate_once(task=task)[1][0]
        assert first.id != second.id
        assert (first.content, first.kind, first.tags) == (
            second.content,
            second.kind,
            second.tags,
        )

    def test_a_failed_delegation_records_deterministically_too(self):
        def fail(task):
            return TaskResult.failure(task, agent_name="research_agent", error="down")

        task = a_task()
        first = delegate_once(SpecialistAgent(result=fail), task=task)[1][0]
        second = delegate_once(SpecialistAgent(result=fail), task=task)[1][0]
        assert first.content == second.content


# ----------------------------------------------------------------------
# A failure never produces the wrong kind of record
# ----------------------------------------------------------------------


class TestAFailureRecordsNothingInappropriate:
    def test_a_failed_delegation_is_still_a_delegation_outcome(self):
        """Not a LESSON. Concluding that something was *learned* from a
        failure is a judgement, and judgement is the brain's job."""
        agent = SpecialistAgent(
            result=lambda task: TaskResult.failure(
                task, agent_name="research_agent", error="upstream unavailable"
            )
        )
        _, records = delegate_once(agent)
        assert {r.kind for r in records} == {MemoryCategory.DELEGATION_OUTCOME.value}

    def test_a_crashing_specialist_produces_only_a_delegation_outcome(self):
        agent = SpecialistAgent(
            result=lambda task: (_ for _ in ()).throw(RuntimeError("agent crashed"))
        )
        _, records = delegate_once(agent)
        assert {r.kind for r in records} == {MemoryCategory.DELEGATION_OUTCOME.value}

    def test_a_rejected_result_produces_only_a_delegation_outcome(self):
        agent = SpecialistAgent(result=lambda task: {"not": "a result"})
        _, records = delegate_once(agent)
        assert {r.kind for r in records} == {MemoryCategory.DELEGATION_OUTCOME.value}

    def test_repeated_failures_do_not_become_a_lesson(self):
        """Five failures in a row is exactly where a LESSON would be tempting.
        Drawing that conclusion automatically is out of scope."""
        agent = SpecialistAgent(
            result=lambda task: TaskResult.failure(task, "research_agent", "down")
        )
        manager = manager_with(agents=(agent,))
        for n in range(5):
            manager.handle_task(a_task(f"research topic {n}"))

        kinds = {r.kind for r in records_of(manager)}
        assert kinds == {MemoryCategory.DELEGATION_OUTCOME.value}

    def test_an_unroutable_task_records_nothing_at_all(self):
        manager = manager_with()
        manager.handle_task(a_task("something nobody can do"))
        assert records_of(manager) == ()


# ----------------------------------------------------------------------
# STRUCTURAL: the policy cannot be bypassed
# ----------------------------------------------------------------------


class TestThePolicyCannotBeBypassed:
    """Proves by inspecting the code that there is one gated write path.

    The behavioural tests above show the policy is consulted on the paths they
    exercise. This shows there is no *other* path - a second, ungated call to
    memory.remember() somewhere else in the Manager would defeat the policy
    entirely and no behavioural test would necessarily catch it.
    """

    @staticmethod
    def _methods_calling(attribute: str, method: str) -> set[str]:
        """Every ManagerAgent method containing ``self.<attribute>.<method>()``."""
        import ast

        import src.manager.manager as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        found = set()

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == method
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == attribute
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "self"
                ):
                    found.add(node.name)
        return found

    def test_there_is_exactly_one_place_that_writes_to_memory(self):
        """Still exactly one, and now it is the public one.

        The writer used to be `_remember_delegation`, which meant every future
        route into memory would have had to re-implement the policy check.
        Session continuity needed a second caller, so the write was extracted
        to `retain` and the delegation path now goes through it. The invariant
        this test protects is unchanged - one place, not none and not two -
        and a second writer appearing here would still fail.
        """
        writers = self._methods_calling("memory", "remember")
        assert writers == {"retain"}

    def test_that_place_also_consults_the_policy(self):
        judges = self._methods_calling("memory_policy", "evaluate")
        assert "retain" in judges

    def test_the_write_is_guarded_by_the_policy_verdict(self):
        """The evaluate() call must precede the remember() call in the source,
        and the remember() must sit behind a conditional."""
        import inspect
        import textwrap

        source = textwrap.dedent(inspect.getsource(ManagerAgent.retain))
        evaluated = source.index("self.memory_policy.evaluate(")
        remembered = source.index("self.memory.remember(")
        assert evaluated < remembered
        assert "if verdict.allowed:" in source

    def test_the_delegation_path_still_goes_through_the_policy(self):
        """Extracting the write must not have let one caller slip past it."""
        import inspect
        import textwrap

        source = textwrap.dedent(
            inspect.getsource(ManagerAgent._remember_delegation)
        )
        assert "self.retain(" in source
        assert "self.memory.remember(" not in source

    def test_the_manager_never_deletes_from_memory(self):
        assert self._methods_calling("memory", "forget") == set()
        assert self._methods_calling("memory", "clear") == set()

    def test_there_is_exactly_one_place_that_reads_from_memory(self):
        """Retrieval goes through one method, as writing goes through one.

        Retargeted for New Dawn: that one method now reads through the
        temporal lens, so nothing calls self.memory.recall directly. A direct
        call would bypass the day filter and return every day's records.
        """
        import ast

        import src.manager.manager as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        building = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "DayView"
                ):
                    building.add(node.name)

        assert building == {"recall_context"}
        assert self._methods_calling("memory", "recall") == set()

    def test_a_policy_that_refuses_everything_yields_an_empty_store(self):
        """The behavioural counterpart: no path escapes the gate."""

        class RefuseEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="refused for this test")

        manager = manager_with(
            agents=(
                SpecialistAgent(name="research_agent", capabilities=("research",)),
                SpecialistAgent(name="writing_agent", capabilities=("write",)),
            ),
            memory_policy=RefuseEverything(),
        )
        manager.handle_task(a_task("research something"))
        manager.handle_task(a_task("write something"))
        manager.handle_task(a_task("something unroutable entirely"))
        manager.deliberate(a_task())

        assert len(manager.memory) == 0
