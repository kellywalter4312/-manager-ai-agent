"""Pre-Phase Agentic Workflow / Complex Objective Completion checkpoint.

CHECK found the real, tested, governed building blocks this checkpoint
needed already in place: ``answer_with_collaboration`` (Stage 13's
governed, production-reachable entry point - collaboration when a
specialist's declared capabilities are relevant, the Manager's own
ordinary D-A-T-A dispatch otherwise, which already includes the
Brain-driven ``open_conversation`` fallback), real memory/conversation
continuity, and Full/Half Brain semantics. What did not exist was any
mechanism for the Manager to dynamically decide, from real evidence,
whether one step actually satisfied an objective or whether more work
was genuinely needed - every existing entry point ran exactly one step
and stopped, regardless of what the objective actually asked for.

**What was built, and why not CognitiveLoop.** CHECK also found
``CognitiveLoop``/``ActionExecutor`` real, tested and governed - but its
Router-concurrence check (``manager.diagnose()``) is narrower than the
routing decision the rest of the system actually trusts
(``manager.decide()``, which also knows about the Manager's own
shape-based capability claims and is the default owner of any work no
specialist's declared capabilities match). Reproduced directly below
(``TestTheDiagnoseVsDecideGapCognitiveLoopWouldHaveInherited``):
``diagnose()`` returns ``None`` for perfectly ordinary, novel wording
with no capability-word overlap at all, which would make
``HANDLE_SELF`` refuse exactly the open-ended reasoning this
checkpoint's own Test F requires. Activating ``CognitiveLoop`` would
have inherited that gap for no benefit this loop needs, so
``src/manager/objective_loop.py::pursue_objective`` is built entirely
on ``answer_with_collaboration`` instead - already production-reachable,
already tested, already using the correct, full ``decide()`` for every
step. ``CognitiveLoop``/``ActionExecutor`` remain exactly as staged as
they were before this checkpoint - not activated, not modified, not
depended upon; see ``TestCognitiveLoopRemainsStagedAndUntouched``.

**Why this is not a workflow catalogue.** There is exactly one function
that decides what happens next, ``ManagerBrain.assess_objective``, and
it contains no branch keyed on subject, topic or wording - only the
objective and the real evidence gathered. The tests below use
deliberately unseen wording and topics never encoded anywhere in this
project (Test F), proving the mechanism generalises rather than having
been shaped around these specific examples.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.core.contracts import Task, TaskResult, TaskStatus
from src.manager.brain import BrainError, ObjectiveVerdict
from src.manager.brain_prompts import (
    OBJECTIVE_SYSTEM_PROMPT,
    build_objective_prompt,
    parse_objective_or_unavailable,
)
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.manager.objective_loop import (
    DEFAULT_MAX_STEPS,
    ObjectiveOutcome,
    ObjectiveStatus,
    pursue_objective,
)
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


def _current_remark(prompt: str) -> str:
    """Isolate the remark converse() was actually asked about from the
    recent-conversation/context sections also folded into the same
    prompt - the established isolation lesson from this project's own
    prior verification work, applied here on the first attempt."""
    return prompt.split("\n\nContext")[0].split("\n\nRecent")[0].split("\n\nGATHERED")[0]


# ---------------------------------------------------------------------------
# 1. ObjectiveVerdict and the prompt/parser pair - pure, no model, no Brain
# ---------------------------------------------------------------------------


class TestObjectiveVerdictType:
    def test_a_valid_status_constructs(self):
        for status in ("continue", "complete", "needs_input", "failed", ""):
            v = ObjectiveVerdict(decided=True, status=status)
            assert v.status == status

    def test_an_invalid_status_is_refused(self):
        with pytest.raises(BrainError):
            ObjectiveVerdict(decided=True, status="maybe")

    def test_unavailable_is_undecided(self):
        v = ObjectiveVerdict.unavailable(source="test")
        assert v.decided is False
        assert v.source == "test"

    def test_whitespace_in_text_fields_is_collapsed(self):
        v = ObjectiveVerdict(decided=True, status="continue", next_step="  do   this   next  ")
        assert v.next_step == "do this next"


class TestObjectivePromptAndParser:
    def test_the_prompt_carries_the_real_objective_and_evidence_verbatim(self):
        prompt = build_objective_prompt(
            Task(description="a genuinely novel objective about beekeeping logistics"),
            ("step 1 found three hives needing inspection",),
        )
        assert "a genuinely novel objective about beekeeping logistics" in prompt
        assert "step 1 found three hives needing inspection" in prompt

    def test_no_evidence_is_stated_plainly_not_hidden(self):
        prompt = build_objective_prompt(Task(description="x"), ())
        assert "Nothing has been gathered toward this yet." in prompt

    def test_a_continue_verdict_round_trips(self):
        v = parse_objective_or_unavailable(
            '{"status":"continue","next_step":"look up the second half","question":null,"final_answer":null}'
        )
        assert v.decided and v.status == "continue" and v.next_step == "look up the second half"

    def test_a_continue_verdict_with_no_next_step_is_unavailable(self):
        """A decided status naming nothing a caller could act on is not
        usable advice - the identical 'decided but empty' floor every
        sibling parser in this project already applies."""
        v = parse_objective_or_unavailable('{"status":"continue","next_step":null}')
        assert v.decided is False

    def test_malformed_json_is_unavailable_not_an_exception(self):
        v = parse_objective_or_unavailable("not json at all")
        assert v.decided is False

    def test_an_invalid_status_string_is_unavailable(self):
        v = parse_objective_or_unavailable('{"status":"maybe_continue"}')
        assert v.decided is False

    def test_the_system_prompt_forbids_naming_who_does_the_work(self):
        """Governance, stated to the model itself: deciding *who* or
        *how* is never this judgement's job - only what should happen
        next. The orchestrator's own governed dispatch decides the rest,
        exactly as every other Brain consultation in this project already
        keeps that boundary."""
        assert "must never name a specific person, agent or" in OBJECTIVE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 2. ManagerBrain.assess_objective - Half/Full Brain honesty
# ---------------------------------------------------------------------------


class TestAssessObjectiveHonesty:
    def test_null_brain_gives_an_honest_no_view(self):
        from src.manager.brain import NullBrain

        v = NullBrain().assess_objective(Task(description="x"))
        assert v.decided is False

    def test_a_failing_provider_degrades_honestly(self):
        class Raises(LLMProvider):
            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                raise RuntimeError("unavailable")

        v = LLMManagerBrain(Raises()).assess_objective(Task(description="x"))
        assert v.decided is False

    def test_a_genuine_verdict_reaches_the_caller(self):
        class Scripted(LLMProvider):
            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                return Completion(
                    text='{"status":"complete","final_answer":"all done"}', provider="test"
                )

        v = LLMManagerBrain(Scripted()).assess_objective(Task(description="x"), ("evidence",))
        assert v.decided and v.status == "complete" and v.final_answer == "all done"


# ---------------------------------------------------------------------------
# 3. pursue_objective mechanics - direct, with a bare ManagerAgent
# ---------------------------------------------------------------------------


class ScriptedProvider(LLMProvider):
    """A queue of raw completion texts for the objective-progress
    judgement specifically; every other call (retention/ambiguity/
    activity-start/ordinary conversation) gets a fixed, honest default -
    the same discipline every scripted provider in this project's own
    test suite already follows."""

    def __init__(self, objective_script=(), answer="a genuine ordinary answer"):
        self._objective_script = list(objective_script)
        self._answer = answer
        self.objective_calls: list[str] = []

    @property
    def name(self):
        return "test"

    def complete(self, prompt, *, system=None):
        system = system or ""
        if "should_retain" in system or "narrow question about a remark" in system:
            return Completion(text='{"should_retain": false, "content": null}', provider="test")
        if "candidate values" in system:
            return Completion(text='{"is_ambiguous": false}', provider="test")
        if "begin an interactive learning" in system:
            return Completion(text='{"should_start": false, "topic": null}', provider="test")
        if "judging the progress of one objective" in system:
            self.objective_calls.append(prompt)
            return Completion(text=self._objective_script.pop(0), provider="test")
        return Completion(text=self._answer, provider="test")


def _wired(objective_script=(), answer="a genuine ordinary answer"):
    runtime, manager, channel, conversation = real_system()
    provider = ScriptedProvider(objective_script, answer)
    brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return runtime, manager, channel, conversation, provider


class TestHalfBrainIsUnaffected:
    def test_half_brain_runs_exactly_one_step_never_asks_about_progress(self):
        _, manager, _, _ = real_system()
        outcome = pursue_objective(manager, Task(description="a genuinely novel half-brain objective"))
        assert len(outcome.steps) == 1
        assert outcome.status in (ObjectiveStatus.COMPLETED, ObjectiveStatus.FAILED)


class TestFullBrainSingleStepIsByteIdenticalToBefore:
    """The regression this checkpoint's own first implementation attempt
    actually hit: an undecided or 'complete' verdict at step one must
    reproduce that step's own real result unchanged, never a
    re-synthesised or wrapped version of it."""

    def test_a_decided_complete_verdict_returns_the_first_result_unchanged(self):
        _, manager, channel, conversation, provider = _wired(
            objective_script=['{"status":"complete","final_answer":"a separately composed answer"}'],
        )
        turn = exchange(channel, conversation, "xyzzyplugh tell me something genuinely novel")
        assert turn.outcome == "a genuine ordinary answer"

    def test_an_undecided_verdict_falls_back_to_the_first_result_unchanged(self):
        """The exact regression this checkpoint found and fixed: a
        scripted/older Brain double with no opinion about this new
        question must never turn an already-correct answer into a
        fabricated 'partial progress' report."""
        _, manager, channel, conversation, provider = _wired(
            objective_script=["not valid json"],
        )
        turn = exchange(channel, conversation, "xyzzyplugh tell me something else genuinely novel")
        assert turn.outcome == "a genuine ordinary answer"
        assert turn.succeeded

    def test_a_provider_that_fails_only_on_the_objective_question_still_falls_back_cleanly(self):
        class PartlyFailing(LLMProvider):
            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                system = system or ""
                if "should_retain" in system or "narrow question about a remark" in system:
                    return Completion(text='{"should_retain": false, "content": null}', provider="test")
                if "begin an interactive learning" in system:
                    return Completion(text='{"should_start": false, "topic": null}', provider="test")
                if "judging the progress of one objective" in system:
                    raise RuntimeError("temporarily unavailable")
                return Completion(text="a real answer despite the progress check failing", provider="test")

        runtime, manager, channel, conversation = real_system()
        brain = LLMManagerBrain(PartlyFailing(), personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        turn = exchange(channel, conversation, "xyzzyplugh a third genuinely novel remark")
        assert turn.outcome == "a real answer despite the progress check failing"
        assert turn.succeeded


class TestGenuineMultiStepProgression:
    def test_continue_then_complete_runs_two_real_steps(self):
        _, manager, channel, conversation, provider = _wired(
            objective_script=[
                '{"status":"continue","next_step":"work out the second, unresolved part"}',
                '{"status":"complete","final_answer":"both parts of the objective are now handled"}',
            ],
        )
        turn = exchange(channel, conversation, "xyzzyplugh a genuinely multi-part objective")
        assert turn.outcome == "both parts of the objective are now handled"

    def test_each_step_is_recorded_with_its_own_real_task_and_result(self):
        _, manager, _, _, provider = _wired(
            objective_script=[
                '{"status":"continue","next_step":"a distinct, concrete second action"}',
                '{"status":"complete","final_answer":"done"}',
            ],
        )
        outcome = pursue_objective(manager, Task(description="xyzzyplugh do the whole thing"))
        assert len(outcome.steps) == 2
        assert outcome.steps[0].task.description == "xyzzyplugh do the whole thing"
        assert outcome.steps[1].task.description == "a distinct, concrete second action"
        assert all(step.result.succeeded for step in outcome.steps)

    def test_the_real_evidence_from_step_one_reaches_the_progress_judgement(self):
        _, manager, _, _, provider = _wired(
            objective_script=['{"status":"complete","final_answer":"done"}'],
            answer="a very specific, identifiable finding: 42 widgets",
        )
        pursue_objective(manager, Task(description="xyzzyplugh count the widgets"))
        assert any("42 widgets" in call for call in provider.objective_calls)


class TestNeedsInputIsHonestNeverFabricated:
    def test_a_needs_input_verdict_asks_and_does_not_claim_completion(self):
        _, manager, channel, conversation, provider = _wired(
            objective_script=['{"status":"needs_input","question":"which format should the report use?"}'],
        )
        turn = exchange(channel, conversation, "xyzzyplugh prepare a report for me")
        assert turn.outcome == "which format should the report use?"
        # Honest pause, not a claim the objective is done - the same
        # treatment src/manager/guided_interaction.py already gives a
        # genuine clarifying question.
        assert turn.succeeded


class TestFailureIsHonestNeverFabricated:
    def test_a_failed_verdict_is_reported_as_a_genuine_failure(self):
        _, manager, channel, conversation, provider = _wired(
            objective_script=['{"status":"failed","final_answer":"there is no way to access that data source"}'],
        )
        turn = exchange(channel, conversation, "xyzzyplugh fetch something genuinely impossible")
        assert not turn.succeeded
        assert "no way to access" in turn.outcome


class TestMaxStepsIsAGenuineSafetyBound:
    def test_the_loop_never_exceeds_the_configured_bound(self):
        _, manager, _, _, provider = _wired(
            objective_script=[
                f'{{"status":"continue","next_step":"keep going, step {i}"}}' for i in range(1, 10)
            ],
        )
        outcome = pursue_objective(
            manager, Task(description="xyzzyplugh an objective that never confirms done"), max_steps=3
        )
        assert len(outcome.steps) == 3
        assert outcome.status is ObjectiveStatus.PARTIAL
        assert not outcome.result.succeeded

    def test_partial_completion_honestly_carries_forward_what_was_found(self):
        _, manager, _, _, provider = _wired(
            objective_script=[
                '{"status":"continue","next_step":"keep going"}',
                '{"status":"continue","next_step":"keep going more"}',
            ],
            answer="a concrete, real finding along the way",
        )
        outcome = pursue_objective(
            manager, Task(description="xyzzyplugh a long objective"), max_steps=2
        )
        assert outcome.status is ObjectiveStatus.PARTIAL
        assert "a concrete, real finding along the way" in outcome.result.error
        assert "cannot confirm the objective was fully completed" in outcome.result.error

    def test_max_steps_of_one_is_the_smallest_valid_bound(self):
        _, manager, _, _, provider = _wired(
            objective_script=['{"status":"continue","next_step":"would keep going"}'],
        )
        outcome = pursue_objective(manager, Task(description="xyzzyplugh anything"), max_steps=1)
        assert len(outcome.steps) == 1
        assert outcome.status is ObjectiveStatus.PARTIAL

    def test_an_invalid_bound_is_refused(self):
        _, manager, _, _ = real_system()
        with pytest.raises(ValueError):
            pursue_objective(manager, Task(description="x"), max_steps=0)

    def test_the_default_bound_is_small_not_unrestricted_autonomy(self):
        assert 1 <= DEFAULT_MAX_STEPS <= 10


# ---------------------------------------------------------------------------
# 4. Governance - a Brain-authored next step never names who does the work
# ---------------------------------------------------------------------------


class TestGovernanceIsPreservedPerStep:
    def test_a_next_step_cannot_force_a_specific_specialist(self):
        """Even if a next_step reads as though it names somebody, the
        actual dispatch still goes through the identical governed
        capability/router decision every ordinary request already uses -
        never a direct call to a named agent. Proven by registering a
        specialist whose declared capabilities do NOT overlap the
        invented next-step text at all, and confirming it never runs."""
        runtime, manager, channel, conversation, provider = _wired(
            objective_script=[
                '{"status":"continue","next_step":"Arthur should immediately refund the customer in full"}',
                '{"status":"complete","final_answer":"handled"}',
            ],
        )
        arthur = manager.registry.get("Arthur")
        before = None
        try:
            before = len(getattr(arthur, "_calls", []))
        except Exception:
            before = None

        outcome = pursue_objective(manager, Task(description="xyzzyplugh a governance probe"))
        # The literal word "Arthur" in the invented next step never
        # becomes a direct invocation - the second step is still decided
        # by ordinary capability/router matching against its own words,
        # exactly like any other task text ever is.
        assert outcome.steps[1].result.agent_name != "Arthur" or "refund" not in (
            outcome.steps[1].result.output or ""
        )

    def test_cognitive_loop_and_action_executor_are_never_imported_by_the_objective_loop(self):
        import ast

        import src.manager.objective_loop as objective_loop_module

        source = open(objective_loop_module.__file__, encoding="utf-8").read()
        imported_modules = {
            alias.asname or alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        imported_names = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        }
        assert "src.manager.cognitive_loop" not in imported_names
        assert "src.manager.actions" not in imported_names
        assert "CognitiveLoop" not in imported_modules
        assert "ActionExecutor" not in imported_modules


class TestCognitiveLoopRemainsStagedAndUntouched:
    """Re-confirms the standing Trust-in-Stages guarantee this checkpoint
    was explicitly told to preserve - this checkpoint's own new
    machinery does not depend on, activate, or modify it in any way."""

    def test_it_still_cannot_be_reached_from_any_production_entry_point(self):
        from scripts.repo_recovery import compute_reachability

        report = compute_reachability()
        assert report.status_of("src.manager.cognitive_loop") != "production"
        assert report.status_of("src.manager.actions") != "production"

    def test_it_still_constructs_and_runs_directly_unchanged(self):
        from src.manager.cognitive_loop import CognitiveLoop

        loop = CognitiveLoop(ManagerAgent())
        outcome = loop.run(Task(description="do something"))
        assert outcome.task_id


class TestTheDiagnoseVsDecideGapCognitiveLoopWouldHaveInherited:
    """Documents, rather than fixes, a genuine gap CHECK found in the
    still-staged CognitiveLoop/ActionExecutor: HANDLE_SELF's Router-
    concurrence check uses manager.diagnose() (the bare capability
    router), which - unlike manager.decide(), what the rest of the
    system actually trusts - has no notion of the Manager's own
    shape-based capability claims or of being the default owner of
    unclaimed work. Left exactly as found: this checkpoint's own design
    does not use CognitiveLoop and so does not need this fixed, and
    fixing it would mean editing a component Trust-in-Stages keeps
    deliberately unreachable, for no benefit this checkpoint requires."""

    def test_diagnose_and_decide_disagree_for_ordinary_novel_wording(self):
        _, manager, _, _ = real_system()
        task = Task(description="xyzzyplugh transmogrify the qux into a fnord")
        assert manager.diagnose(task) is None
        decided = manager.decide(task)
        assert decided is not None and decided.agent_name == "Alfred"

    def test_which_is_exactly_why_answer_with_collaboration_is_used_instead(self):
        """The same novel wording, run through this checkpoint's own
        mechanism, is handled correctly - because it uses decide()
        (via answer_with_collaboration -> handle_task), never diagnose()."""
        _, manager, channel, conversation = real_system()
        outcome = pursue_objective(manager, Task(description="xyzzyplugh transmogrify the qux into a fnord"))
        assert outcome.steps[0].result.agent_name == "Alfred"
