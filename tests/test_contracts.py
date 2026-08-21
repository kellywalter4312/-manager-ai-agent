"""Tests for the data contracts."""

import pytest

from src.core.contracts import AgentSpec, Task, TaskResult, TaskStatus


class TestTask:
    def test_gets_a_unique_id(self):
        assert Task(description="a").id != Task(description="a").id

    def test_rejects_blank_description(self):
        with pytest.raises(ValueError):
            Task(description="   ")

    def test_is_immutable(self):
        task = Task(description="do a thing")
        with pytest.raises(Exception):
            task.description = "something else"

    def test_tokenizes_description(self):
        assert Task(description="Read my E-mail!").tokens == {"read", "my", "e", "mail"}


class TestAgentSpec:
    def test_normalizes_capabilities(self):
        spec = AgentSpec(name="a", description="d", capabilities={" Email ", "INBOX"})
        assert spec.capabilities == {"email", "inbox"}

    def test_requires_a_name(self):
        with pytest.raises(ValueError):
            AgentSpec(name="", description="d", capabilities={"x"})

    def test_requires_at_least_one_capability(self):
        with pytest.raises(ValueError):
            AgentSpec(name="a", description="d", capabilities=frozenset())

    def test_match_score_counts_overlapping_capabilities(self):
        spec = AgentSpec(name="a", description="d", capabilities={"email", "inbox"})
        assert spec.match_score(frozenset({"send", "email"})) == 1
        assert spec.match_score(frozenset({"email", "inbox"})) == 2
        assert spec.match_score(frozenset({"weather"})) == 0


class TestTaskResult:
    def test_success_carries_task_id_and_output(self):
        task = Task(description="x")
        result = TaskResult.success(task, agent_name="a", output="done")
        assert result.task_id == task.id
        assert result.status is TaskStatus.COMPLETED
        assert result.succeeded

    def test_failure_is_not_a_success(self):
        task = Task(description="x")
        result = TaskResult.failure(task, agent_name="a", error="boom")
        assert not result.succeeded
        assert result.error == "boom"
