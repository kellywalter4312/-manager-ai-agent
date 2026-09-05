"""Stage 13 final correction - no double execution on the Brain fallback.

The comprehensive Stage 13 audit found a real, if currently dormant,
production characteristic: when ``answer_with_collaboration`` starts a
collaboration, a candidate succeeds, and Alfred's own Brain then fails to
compose a synthesis (Half Brain, a failed provider call, or an empty
completion), the old fallback called ``manager.handle_task(task)`` again -
which, for the common single-candidate case, re-invoked the very
specialist that had already succeeded. Harmless today only because the
one registered specialist (the Email Agent) is read-only; a real risk the
moment a future specialist has a side effect.

This file proves the fix in ``src/manager/collaboration_entry.py``: a
specialist that already produced a genuine contribution during
collaboration is never re-executed merely because synthesis afterward
failed - its own, already-honest ``TaskResult`` is reused directly. A
fresh ``manager.handle_task`` still runs, exactly as before, whenever the
collaboration produced nothing usable to reuse.
"""

from __future__ import annotations

import inspect

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult
from src.manager.llm_brain import LLMManagerBrain
from scripts.exchange import answer_with_collaboration, collaboration_candidates


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class ScriptedProvider:
    """A real, local LLMProvider double - never a network call."""

    def __init__(self, text: str = "", *, fail: bool = False):
        self._text = text
        self._fail = fail

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        if self._fail:
            raise RuntimeError("provider exploded")
        return Completion(
            text=self._text, provider=self.name, model="scripted-1",
            input_tokens=len(prompt), output_tokens=len(self._text),
        )


def with_scripted_brain(manager, text: str = "", *, fail: bool = False):
    brain = LLMManagerBrain(ScriptedProvider(text, fail=fail))
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return brain


class CountingAgent(BaseAgent):
    """A specialist that counts its own executions - the direct, honest
    way to prove it ran once rather than inferring it from side effects
    that happen not to exist yet."""

    def __init__(self, name: str, capabilities: frozenset[str], reply: str):
        self._name = name
        self._capabilities = capabilities
        self._reply = reply
        self.calls = 0

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(name=self._name, description="counts its own executions", capabilities=self._capabilities)

    def execute(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult.success(task, self._name, self._reply)


class FailingAgent(BaseAgent):
    def __init__(self, name: str, capabilities: frozenset[str]):
        self._name = name
        self._capabilities = capabilities
        self.calls = 0

    @property
    def spec(self) -> AgentSpec:
        return AgentSpec(name=self._name, description="cannot reason", capabilities=self._capabilities)

    def execute(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult.failure(task, self._name, "no usable reasoning capability")


TASK = Task(description="map the territory please")


def manager_with_counting_agent(**brain_kwargs):
    manager = real_manager()
    agent = CountingAgent("Cartographer", frozenset({"map", "territory"}), "mapped the territory")
    manager.register_agent(agent)
    return manager, agent


# ---------------------------------------------------------------------------
# 1-2: not repeated, already-obtained result returned
# ---------------------------------------------------------------------------


class TestNoDoubleExecution:
    def test_a_successful_specialist_is_not_re_executed_when_synthesis_fails(self):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager)  # empty completion -> no_view

        answer_with_collaboration(manager, TASK)

        assert agent.calls == 1

    def test_the_already_obtained_task_result_is_returned_directly(self, monkeypatch):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager)

        captured: dict[str, TaskResult] = {}
        real_take_action = manager.take_action

        def spying_take_action(a, task):
            result = real_take_action(a, task)
            captured[a.name] = result
            return result

        monkeypatch.setattr(manager, "take_action", spying_take_action)

        result = answer_with_collaboration(manager, TASK)

        assert result is captured["Cartographer"]
        assert result.agent_name == "Cartographer"
        assert result.output == "mapped the territory"

    def test_manager_handle_task_is_not_called_when_a_single_result_is_reused(self, monkeypatch):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager)

        calls = []
        real_handle_task = manager.handle_task
        monkeypatch.setattr(
            manager, "handle_task",
            lambda *a, **k: (calls.append(1), real_handle_task(*a, **k))[1],
        )

        answer_with_collaboration(manager, TASK)

        assert calls == []
        assert agent.calls == 1


# ---------------------------------------------------------------------------
# 3: a fresh fallback still occurs when nothing usable was produced
# ---------------------------------------------------------------------------


class TestFreshFallbackWhenNothingUsable:
    def test_manager_handle_task_still_runs_when_collaboration_produced_nothing(self, monkeypatch):
        manager = real_manager()
        agent = FailingAgent("Broken", frozenset({"broken"}))
        manager.register_agent(agent)
        with_scripted_brain(manager)

        calls = []
        real_handle_task = manager.handle_task
        monkeypatch.setattr(
            manager, "handle_task",
            lambda *a, **k: (calls.append(1), real_handle_task(*a, **k))[1],
        )

        result = answer_with_collaboration(manager, Task(description="broken thing please"))

        assert calls == [1]
        # Nothing succeeded during collaboration, so there is nothing to
        # reuse - the fresh handle_task() call genuinely runs, and its own
        # ordinary diagnosis legitimately re-attempts the only registered
        # capability match. That second attempt is expected, honest
        # behaviour: the fix exists to stop a *successful* result being
        # thrown away and re-earned, never to stop the ordinary path from
        # trying a candidate that has not yet succeeded at all.
        assert agent.calls == 2
        assert not result.succeeded

    def test_a_failed_candidate_is_never_mistaken_for_a_reusable_success(self):
        """The distinction that matters: two failures are not a repeated
        success. `answer_with_collaboration` never returns a fabricated
        success out of a collaboration that produced only failures."""
        manager = real_manager()
        agent = FailingAgent("Broken", frozenset({"broken"}))
        manager.register_agent(agent)
        with_scripted_brain(manager)

        result = answer_with_collaboration(manager, Task(description="broken thing please"))

        assert not result.succeeded


# ---------------------------------------------------------------------------
# 4-5: Half Brain and provider-failure honesty preserved
# ---------------------------------------------------------------------------


class TestHalfBrainAndProviderFailureHonesty:
    def test_half_brain_default_nullbrain_reuses_the_real_result_honestly(self):
        manager, agent = manager_with_counting_agent()  # default NullBrain

        result = answer_with_collaboration(manager, TASK)

        assert result.succeeded
        assert result.agent_name == "Cartographer"
        assert agent.calls == 1

    def test_a_failing_provider_call_reuses_the_real_result_honestly(self):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager, fail=True)

        result = answer_with_collaboration(manager, TASK)

        assert result.succeeded
        assert result.agent_name == "Cartographer"
        assert agent.calls == 1

    def test_an_empty_completion_reuses_the_real_result_honestly(self):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager, text="")

        result = answer_with_collaboration(manager, TASK)

        assert result.succeeded
        assert result.agent_name == "Cartographer"
        assert agent.calls == 1


# ---------------------------------------------------------------------------
# 6: existing Stage 13 collaboration behaviour unchanged otherwise
# ---------------------------------------------------------------------------


class TestExistingBehaviourUnchangedOtherwise:
    def test_a_successful_synthesis_still_produces_alfreds_own_answer(self):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager, text="Based on the Cartographer, the territory is mapped.")

        result = answer_with_collaboration(manager, TASK)

        assert result.succeeded
        assert result.agent_name == "Alfred"
        assert "mapped" in result.output
        assert agent.calls == 1  # synthesis succeeding never needed a second run either

    def test_no_candidate_requests_are_completely_unaffected(self):
        manager = real_manager()
        result = answer_with_collaboration(manager, Task(description="status"))
        assert result.agent_name == "Alfred"
        assert "handles directly" in result.output


# ---------------------------------------------------------------------------
# 7-8: canonical instance, no second Brain/Memory
# ---------------------------------------------------------------------------


class TestCanonicalInstanceAndNoSecondBrainOrMemory:
    def test_the_canonical_registered_specialist_is_unchanged(self):
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager)
        real_agent = manager.registry.get("Cartographer")

        answer_with_collaboration(manager, TASK)

        assert manager.registry.get("Cartographer") is real_agent is agent

    def test_no_new_brain_or_memory_object_appears_on_the_manager(self):
        manager, agent = manager_with_counting_agent()
        brain = with_scripted_brain(manager)
        memory_before = manager.memory

        answer_with_collaboration(manager, TASK)

        assert manager.brain is brain
        assert manager.memory is memory_before

    def test_the_source_constructs_no_brain_or_memory(self):
        import src.manager.collaboration_entry as collaboration_entry

        source = inspect.getsource(collaboration_entry)
        for forbidden in ("Memory(", "Brain(", "PersistentMemory(", "SpecialistBrain(", "LLMManagerBrain("):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# 9-10: Stage 12, Stage 11.A / 11.B isolation
# ---------------------------------------------------------------------------


class TestStage12And11Isolation:
    def test_remember_that_still_retains_after_a_reused_fallback(self):
        from src.conversation.conversation import Conversation, Participant, ParticipantKind
        from src.channels.channel import TextChannel
        from scripts.exchange import exchange

        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager)
        channel = TextChannel(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))

        exchange(channel, conversation, TASK.description)  # triggers the reuse path

        before = len(manager.memory.recall(limit=50))
        exchange(channel, conversation, "remember that we prefer green")
        after = manager.memory.recall(limit=50)

        assert len(after) == before + 1
        assert any("green" in record.content for record in after)

    def test_the_fix_imports_nothing_from_extensions_or_evolution(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "src" / "manager" / "collaboration_entry.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = ", ".join(getattr(n, "name", "") for n in node.names)
                assert "extensions" not in module and "extensions" not in names
                assert "evolution" not in module and "evolution" not in names


# ---------------------------------------------------------------------------
# Mutation - the no-double-execution guarantee itself
# ---------------------------------------------------------------------------


class TestMutationNoDoubleExecution:
    def test_reuse_is_load_bearing_for_the_call_count(self):
        """Pins the behaviour a source-level mutation of the reuse branch
        (forcing the fresh-fallback path unconditionally) would break -
        see this session's own mutation-test report. A real, if
        currently harmless, second execution is exactly what this
        correction exists to prevent."""
        manager, agent = manager_with_counting_agent()
        with_scripted_brain(manager)

        answer_with_collaboration(manager, TASK)

        assert agent.calls == 1
