"""Final Pre-Phase Interaction Completion - clarification/conflict
resolution and general interactive guided learning, before the Master
Roadmap.

CHECK found no general mechanism for either behaviour: the only existing
uncertainty-resolution code was birthday-specific
(``_apply_relational_birthday``), and nothing anywhere asked a question,
accepted an answer, graded it, or tracked a score. Both are now real,
general capabilities layered additively onto ``open_conversation`` - never
a second Brain, a second Memory, or a per-subject/per-fact-type feature:

    ManagerBrain.discern_ambiguity          "does this state unresolved
                                             candidates for one thing?"
    ManagerBrain.discern_activity_start     "does this ask to begin a
                                             guided activity, on what?"
    ManagerBrain.compose_activity_question  "ask one genuine question
                                             about this arbitrary topic"
    ManagerBrain.evaluate_activity_answer   "was this answer correct, and
                                             does the person want to stop?"

Turn-scoped, in-progress state (a pending clarification; an active
activity's topic/question/score) lives in ``ActivitySessions`` -
deliberately *not* Memory (see that module's own docstring for why
``MemoryCategory``'s allow-list refuses exactly this kind of transient
bookkeeping) and *not* a new Brain. Only a genuinely resolved fact or a
session's final progress summary ever crosses into real, governed
``Memory``, through the identical ``retain()`` path every other durable
fact already uses.

Every test exercises the real production path
(``scripts/control_center.py::build()`` + ``scripts/exchange.py::
exchange()``) with a scripted Full-Brain provider standing in for the
funded model this environment does not have - never a bare unit call
standing in for "Alfred handled this correctly."
"""

from __future__ import annotations

import re

import pytest

import scripts.control_center as control_center_module
from src.config.settings import Settings
from src.manager.activity_session import ActivityBrainOps, ActivitySessions, GuidedActivityState, PendingClarification
from src.manager.brain import ActivityAnswerVerdict, BrainDecision
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange


@pytest.fixture(autouse=True)
def _no_owner_created_specialists(tmp_path, monkeypatch):
    """The ecosystem these tests build must be the shipped one, not whoever
    the owner has since created.

    ``memory="ephemeral"`` isolates memory, which is what this file needed
    when it was written. It does *not* isolate the governed config, and that
    file is where ``agent create`` persists a specialist - so ``build()``
    reads the owner's real roster. On 2026-09-29 the owner's installation
    gained ``agent_definition.marine_expert`` ("marine biology and
    oceanographic research"), and this file's own deliberately obscure quiz
    topic - "the biology of deep-sea anglerfish" - stopped being obscure:
    the router correctly delegated it to that specialist instead of Alfred
    starting a guided activity, and the test failed.

    That routing is the architecture working, not a defect: a marine-biology
    question belongs to the marine-biology specialist, and the fact that no
    source file anywhere names "Coral" is exactly the genericity this project
    holds itself to. What was wrong was this file's hidden assumption that
    nothing in the owner's installation would ever claim its topic. Isolated
    here rather than by choosing a more obscure topic, because any topic can
    become someone's specialism - the assumption is what had to go.
    """
    monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_SERVICES_PATH", tmp_path / "services.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_SITES_PATH", tmp_path / "sites.json")
    return tmp_path


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


def _remark_of(prompt: str) -> str:
    """Isolate the actual remark from a structured prompt - never the
    recent-conversation context also folded in, so a scripted provider's
    own routing does not accidentally trigger on history bleeding into a
    later turn's prompt (a real mistake caught and fixed while building
    this very test file)."""
    match = re.search(r"THE REMARK:\n(.*?)\n\n", prompt, re.DOTALL)
    return match.group(1) if match else prompt


class ScriptedActivityProvider(LLMProvider):
    """A real, distinct ``LLMProvider`` that genuinely inspects each
    remark's own text (never a mock of the parsing) to decide how each
    of the four narrow Brain questions should answer - configurable per
    test via simple keyword rules, exercising the real JSON contracts
    each real method actually parses."""

    def __init__(
        self,
        *,
        ambiguity_trigger: str | None = None,
        ambiguity_candidates: tuple[str, str] = ("Tuesday", "Wednesday"),
        activity_trigger: str | None = None,
        activity_topic: str = "",
        questions: tuple[str, ...] = (),
        answer_key: dict[str, str] | None = None,
        stop_trigger: str = "stop",
        converse_answer: str = "(a natural reply)",
        retention_json: str = '{"should_retain": false, "content": null}',
    ):
        self.calls: list[tuple[str, str]] = []
        self._ambiguity_trigger = ambiguity_trigger
        self._ambiguity_candidates = ambiguity_candidates
        self._activity_trigger = activity_trigger
        self._activity_topic = activity_topic
        self._questions = list(questions)
        self._answer_key = answer_key or {}
        self._stop_trigger = stop_trigger
        self._converse_answer = converse_answer
        self._retention_json = retention_json
        self._question_index = 0

    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        system = system or ""
        self.calls.append((system, prompt))
        remark = _remark_of(prompt)

        if "candidate values" in system:
            if self._ambiguity_trigger and self._ambiguity_trigger in remark:
                a, b = self._ambiguity_candidates
                return Completion(
                    text=f'{{"is_ambiguous": true, "candidates": ["{a}", "{b}"], '
                    f'"question": "Which one is correct - {a} or {b}?"}}',
                    provider="anthropic",
                )
            return Completion(text='{"is_ambiguous": false}', provider="anthropic")

        if "begin an interactive learning" in system:
            if self._activity_trigger and self._activity_trigger in remark.lower():
                return Completion(
                    text=f'{{"should_start": true, "topic": "{self._activity_topic}"}}',
                    provider="anthropic",
                )
            return Completion(text='{"should_start": false, "topic": null}', provider="anthropic")

        if "grading one answer" in system:
            person_answer_match = re.search(r"THE PERSON'S ANSWER: (.*)", prompt)
            given = person_answer_match.group(1).strip() if person_answer_match else ""
            if self._stop_trigger and self._stop_trigger in given.lower():
                return Completion(text='{"wants_to_stop": true, "correct": "no", "feedback": ""}', provider="anthropic")
            verdict = self._answer_key.get(given, "no")
            feedback = "Correct!" if verdict == "yes" else ("Close!" if verdict == "partial" else "Not quite.")
            return Completion(
                text=f'{{"wants_to_stop": false, "correct": "{verdict}", "feedback": "{feedback}"}}',
                provider="anthropic",
            )

        if "running an interactive" in system:
            question = self._questions[self._question_index] if self._question_index < len(self._questions) else "One more question?"
            self._question_index += 1
            return Completion(text=question, provider="anthropic")

        if "should_retain" in system or "narrow question about a remark" in system:
            return Completion(text=self._retention_json, provider="anthropic")
        if "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider="anthropic")
        return Completion(text=self._converse_answer, provider="anthropic")


def _wired(provider: ScriptedActivityProvider):
    runtime, manager, channel, conversation = real_system()
    brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    manager.own_skills._activity_brain = ActivityBrainOps.from_brain(brain)
    return runtime, manager, channel, conversation


# ---------------------------------------------------------------------------
# Clarification / conflict resolution
# ---------------------------------------------------------------------------


class TestClarificationAndConflictResolution:
    def test_a_genuine_clarification_question_is_asked(self):
        # The candidates are the ones the remark states. This used to script
        # "Tuesday"/"Wednesday" for a remark about the 5th and the 12th - an
        # invented candidate, which Phase 11 closure now refuses to ask about.
        provider = ScriptedActivityProvider(ambiguity_trigger="either", ambiguity_candidates=("the 5th", "the 12th"))
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "The deadline is either the 5th or the 12th, I'm honestly not sure.")
        assert turn.outcome == "Which one is correct - the 5th or the 12th?"

    def test_selecting_an_option_resolves_it_and_confirms(self):
        provider = ScriptedActivityProvider(ambiguity_trigger="either")
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "It's either Tuesday or Wednesday, I can't remember which.")
        turn = exchange(channel, conversation, "Tuesday is correct.")
        assert turn.outcome == "Got it - I'll go with Tuesday from now on."

    def test_the_obsolete_candidate_is_never_retained_as_truth(self):
        """The exact requirement: both conflicting answers must not end
        up simultaneously treated as current truth."""
        provider = ScriptedActivityProvider(ambiguity_trigger="either")
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "It's either Tuesday or Wednesday, I can't remember which.")
        exchange(channel, conversation, "Tuesday is correct.")
        records = manager.memory.recall(limit=10)
        assert any("Tuesday" in r.content for r in records)
        assert not any(
            "Wednesday" in r.content and "unsure between" not in r.content for r in records
        )

    def test_an_existing_conflicting_record_is_superseded_on_resolution(self):
        """A stronger form: a record naming the *other* candidate already
        existed before the clarification was even raised - it must be
        removed, not left alongside the newly chosen value."""
        provider = ScriptedActivityProvider(ambiguity_trigger="either")
        _, manager, channel, conversation = _wired(provider)
        from src.manager.memory_policy import MemoryCategory
        from src.services.memory.memory import MemoryRecord

        manager.retain(
            MemoryRecord(content="The workshop is on Wednesday.", kind=MemoryCategory.USER_CONTEXT.value, tags={"x"})
        )
        exchange(channel, conversation, "Actually it's either Tuesday or Wednesday, I keep flip-flopping.")
        exchange(channel, conversation, "It's Tuesday.")
        records = manager.memory.recall(limit=10)
        assert not any(r.content == "The workshop is on Wednesday." for r in records)
        assert any("Tuesday" in r.content for r in records)

    def test_unseen_paraphrasing_of_ambiguity_still_asks(self):
        """Genuine understanding, not a fixed phrase - 'genuinely torn
        between' shares no trigger word with anything hardcoded."""
        provider = ScriptedActivityProvider(ambiguity_trigger="torn")
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "Honestly I'm genuinely torn between Tuesday and Wednesday for this.")
        assert "Which one is correct" in turn.outcome

    def test_ordinary_conversation_never_triggers_a_spurious_clarification(self):
        provider = ScriptedActivityProvider(ambiguity_trigger="either")
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "Honestly today has been a pretty good day overall.")
        assert turn.outcome == "(a natural reply)"

    def test_a_reply_that_does_not_match_any_candidate_leaves_it_pending(self):
        provider = ScriptedActivityProvider(ambiguity_trigger="either")
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "It's either Tuesday or Wednesday, not sure.")
        from src.manager.activity_session import ActivitySessions

        turn = exchange(channel, conversation, "haha anyway how are you doing")
        assert turn.outcome == "(a natural reply)"
        assert manager.own_skills._activity_sessions.pending_clarification("Alfred") is not None


# ---------------------------------------------------------------------------
# General interactive learning / assessment
# ---------------------------------------------------------------------------


class TestInteractiveLearningAndAssessment:
    def test_starting_a_session_asks_a_genuine_question_on_an_arbitrary_topic(self):
        """Topic is deliberately obscure and unrelated to any example in
        this project's own code or docs - proving no per-subject feature."""
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="the biology of deep-sea anglerfish",
            questions=("How do anglerfish attract prey in the dark?",),
        )
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "quiz me on the biology of deep-sea anglerfish")
        assert turn.outcome == "How do anglerfish attract prey in the dark?"

    def test_question_then_answer_then_evaluation(self):
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="basic arithmetic",
            questions=("What is 7 plus 5?", "What is 9 times 3?"),
            answer_key={"12": "yes", "27": "yes"},
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "quiz me on basic arithmetic")
        turn = exchange(channel, conversation, "12")
        assert "Correct!" in turn.outcome
        assert "What is 9 times 3?" in turn.outcome

    def test_an_incorrect_answer_is_graded_honestly_not_pretended_correct(self):
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="basic arithmetic",
            questions=("What is 7 plus 5?", "One more?"),
            answer_key={"11": "no"},
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "quiz me on basic arithmetic")
        turn = exchange(channel, conversation, "11")
        assert "Not quite" in turn.outcome

    def test_scoring_is_exact_across_multiple_questions(self):
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="basic arithmetic",
            questions=("Q1?", "Q2?", "Q3?"),
            answer_key={"right": "yes", "wrong": "no", "sorta": "partial"},
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "quiz me on basic arithmetic")
        exchange(channel, conversation, "right")
        exchange(channel, conversation, "wrong")
        turn = exchange(channel, conversation, "sorta")
        session = manager.own_skills._activity_sessions.activity("Alfred")
        assert session.correct_count == 1
        assert session.incorrect_count == 1
        assert session.partial_count == 1
        assert "1 correct" in turn.outcome or "1 correct" in session.score_line

    def test_adaptation_the_next_question_receives_the_sessions_own_history(self):
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="basic arithmetic",
            questions=("Q1?", "Q2?"),
            answer_key={"right": "yes"},
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "quiz me on basic arithmetic")
        exchange(channel, conversation, "right")
        question_prompts = [p for s, p in provider.calls if "running an interactive" in s]
        assert len(question_prompts) == 2
        assert "graded yes" in question_prompts[1]

    def test_the_session_finishes_naturally_and_reports_the_final_score(self):
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="basic arithmetic",
            questions=("Q1?", "Q2?"),
            answer_key={"right": "yes"},
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "quiz me on basic arithmetic")
        exchange(channel, conversation, "right")
        turn = exchange(channel, conversation, "let's stop here")
        assert "stop" in turn.outcome.lower()
        assert "1 correct" in turn.outcome
        assert manager.own_skills._activity_sessions.activity("Alfred") is None

    def test_progress_is_preserved_in_real_governed_memory_when_the_session_ends(self):
        provider = ScriptedActivityProvider(
            activity_trigger="quiz",
            activity_topic="basic arithmetic",
            questions=("Q1?", "Q2?"),
            answer_key={"right": "yes"},
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "quiz me on basic arithmetic")
        exchange(channel, conversation, "right")
        exchange(channel, conversation, "stop")
        records = manager.memory.recall(limit=10)
        assert any("basic arithmetic" in r.content and "1 correct" in r.content for r in records)

    def test_a_different_subject_works_with_no_new_hardcoded_feature(self):
        """The same mechanism, a wholly different domain - proving there
        is no per-topic branch anywhere in the implementation."""
        provider = ScriptedActivityProvider(
            activity_trigger="practice",
            activity_topic="conversational Japanese greetings",
            questions=("How do you say 'good morning' in Japanese?",),
            answer_key={"ohayo": "yes"},
        )
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "let's practice conversational Japanese greetings")
        assert "Japanese" in turn.outcome
        turn2 = exchange(channel, conversation, "ohayo")
        assert "Correct!" in turn2.outcome

    def test_an_unevaluatable_answer_falls_through_honestly_never_fabricated(self):
        """Half Brain / a provider that cannot grade - the session must
        not invent a verdict."""
        provider = ScriptedActivityProvider(activity_trigger="quiz", activity_topic="x", questions=("Q1?",))

        class UngradeableProvider(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                if "grading one answer" in (system or ""):
                    return Completion(text="not json at all", provider="anthropic")
                return provider.complete(prompt, system=system)

        _, manager, channel, conversation = _wired(UngradeableProvider())
        exchange(channel, conversation, "quiz me on x")
        turn = exchange(channel, conversation, "an answer")
        assert turn.outcome == "(a natural reply)"
        assert manager.own_skills._activity_sessions.activity("Alfred") is not None

    def test_ordinary_conversation_never_starts_a_spurious_activity(self):
        provider = ScriptedActivityProvider(activity_trigger="quiz", activity_topic="x")
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "I'm just chilling today, nothing much going on.")
        assert turn.outcome == "(a natural reply)"
        assert manager.own_skills._activity_sessions.activity("Alfred") is None

    def test_mentioning_that_one_is_studying_does_not_itself_start_a_session(self):
        """Distinguishes existing learning_context ('I am studying X')
        from a genuine request to be actively quizzed - the task's own
        explicit distinction."""
        provider = ScriptedActivityProvider(activity_trigger="quiz me", activity_topic="x")
        _, manager, channel, conversation = _wired(provider)
        turn = exchange(channel, conversation, "I am studying for my chemistry exam next week.")
        assert manager.own_skills._activity_sessions.activity("Alfred") is None


# ---------------------------------------------------------------------------
# Regression: existing behaviour untouched
# ---------------------------------------------------------------------------


class TestExistingBehaviourIsUnaffected:
    def test_natural_retention_still_works_independently(self):
        provider = ScriptedActivityProvider(
            retention_json='{"should_retain": true, "content": "the launch is Friday"}'
        )
        _, manager, channel, conversation = _wired(provider)
        exchange(channel, conversation, "Don't let me lose this - the launch is Friday.")
        records = manager.memory.recall(limit=10)
        assert any("the launch is Friday" in r.content for r in records)

    def test_with_no_activity_brain_configured_open_conversation_is_unchanged(self):
        """The honest degradation every Brain consultation in this
        project already has: no activity_brain means guided_interaction_
        response does nothing, and ordinary conversation is untouched."""
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedActivityProvider(ambiguity_trigger="either")
        brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse
        manager.own_skills._activity_brain = None
        turn = exchange(channel, conversation, "it's either Tuesday or Wednesday, not sure")
        assert turn.outcome == "(a natural reply)"

    def test_specialist_independence_arthur_is_unaffected(self):
        """This turn's work wires Alfred's own ManagerSkills only -
        Arthur's own conversational path (built in the previous Natural
        Conversation pass) must keep working exactly as before."""
        provider = ScriptedActivityProvider(converse_answer="Napoleon rose after the French Revolution.")
        _, manager, channel, conversation = _wired(provider)
        from src.agents.llm_brain import LLMSpecialistBrain

        arthur_provider = ScriptedActivityProvider(converse_answer="arthur's own answer")
        arthur = manager.registry.get("Arthur")
        arthur.brain = LLMSpecialistBrain(arthur_provider, personality=arthur.personality)
        turn = exchange(channel, conversation, "tell me about Napoleon", addressed="Arthur")
        assert turn.outcome == "arthur's own answer"

    def test_provider_independence_still_holds_alongside_this_feature(self):
        provider_a = ScriptedActivityProvider(activity_trigger="quiz", activity_topic="x", questions=("Q?",))
        runtime, manager, channel, conversation = real_system()
        brain_a = LLMManagerBrain(provider_a, personality=runtime.identity.personality)
        manager.brain = brain_a
        manager.own_skills._converse = brain_a.converse
        manager.own_skills._activity_brain = ActivityBrainOps.from_brain(brain_a)
        exchange(channel, conversation, "quiz me on x")

        provider_b = ScriptedActivityProvider(converse_answer="answer from provider B")
        brain_b = LLMManagerBrain(provider_b, personality=runtime.identity.personality)
        manager.brain = brain_b
        manager.own_skills._converse = brain_b.converse
        manager.own_skills._activity_brain = ActivityBrainOps.from_brain(brain_b)
        turn = exchange(channel, conversation, "an answer")
        # The activity itself is tracked outside the Brain (ActivitySessions),
        # so it survives the provider swap - graded by whichever Brain
        # currently answers, exactly as every other independence guarantee
        # in this project already requires.
        assert manager.own_skills._activity_sessions.activity("Alfred") is not None
