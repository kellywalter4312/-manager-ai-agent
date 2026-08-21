"""Tests for the Assess phase (Assessor strategies)."""

import pytest

from src.core.contracts import Task, TaskResult, TaskStatus
from src.manager.assessor import Assessor, ContractAssessor


@pytest.fixture
def assessor():
    return ContractAssessor()


@pytest.fixture
def task():
    return Task(description="do the thing")


def test_is_an_assessor(assessor):
    assert isinstance(assessor, Assessor)


def test_accepts_a_valid_result(assessor, task):
    result = TaskResult.success(task, agent_name="alpha_agent", output="done")
    assert assessor.assess(task, "alpha_agent", result) is result


def test_rejects_a_non_taskresult(assessor, task):
    verdict = assessor.assess(task, "alpha_agent", {"status": "completed"})
    assert verdict.status is TaskStatus.REJECTED
    assert "expected TaskResult" in verdict.error


def test_rejects_a_result_for_a_different_task(assessor, task):
    other = TaskResult.success(Task(description="a different task"), "alpha_agent", "done")
    verdict = assessor.assess(task, "alpha_agent", other)
    assert verdict.status is TaskStatus.REJECTED
    assert verdict.task_id == task.id


def test_rejects_success_with_no_output(assessor, task):
    empty = TaskResult.success(task, agent_name="alpha_agent", output="")
    assert assessor.assess(task, "alpha_agent", empty).status is TaskStatus.REJECTED


def test_passes_a_genuine_failure_through_untouched(assessor, task):
    """A FAILED result is honest, not a contract breach - it must survive."""
    failure = TaskResult.failure(task, agent_name="alpha_agent", error="upstream timeout")
    verdict = assessor.assess(task, "alpha_agent", failure)
    assert verdict is failure
    assert verdict.status is TaskStatus.FAILED


def test_a_custom_assessor_can_replace_the_default(task):
    """Any Assessor works, no Manager change."""

    class ParanoidAssessor(Assessor):
        def assess(self, task, agent_name, result):
            return TaskResult.rejected(task, agent_name, "trusts nobody")

    verdict = ParanoidAssessor().assess(task, "alpha_agent", None)
    assert verdict.status is TaskStatus.REJECTED
