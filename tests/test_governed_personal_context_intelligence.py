"""Governed, generalized personal-context intelligence (2026-09-10).

**Motivating example, recorded as the audit itself requires.**

    EXAMPLE                    Apple Intelligence / "deeper personal
                                context" - a product feature named as an
                                illustration only.
    UNDERLYING REQUIREMENT     Generalized, governed personal-context
                                intelligence: accumulated, user-
                                authorized personal context genuinely
                                participates in Alfred's reasoning,
                                while preserving privacy, authorization,
                                agent independence, and Half/Full Brain
                                semantics.
    IMPLEMENTATION              Alfred's own, already-largely-real
                                architecture: ``personal_context.py``'s
                                generalized, topic-open ingestion/
                                storage (deterministic Tier 1 + Brain-
                                assisted Tier 2 - no per-fact-type
                                handler), and ``knowledge.py``'s
                                governed retrieval into the Brain's own
                                prompt (``gather_knowledge_evidence`` ->
                                ``open_conversation``/``knowledge_inquiry``).
    NOT IMPLEMENTATION          Nothing Apple-specific, iPhone-specific,
                                or tied to any named product exists
                                anywhere below or in the production
                                change this file verifies.

**The one real gap this checkpoint closes.** Every other link in the
chain (generalized ingestion, canonical single-store persistence,
governed per-agent retrieval, Half-Brain-safe extraction with an
honest Full-Brain-only reasoning boundary, Control Center visibility)
was already real - proven here as regression, not built here. What was
missing: ``gather_knowledge_evidence``'s own personal-context branch
recalled the ``PERSONAL_CONTEXT_RECALL_LIMIT`` most *recently touched*
topics, never filtered by relevance to the actual question - unlike
the ``LEARNING_CONTEXT`` branch immediately above it, which already
passes the query through for exactly this reason. A stored topic with
nothing to do with what was asked could still occupy one of the few
slots offered to the Brain, silently crowding out a genuinely relevant
one. ``src/manager/knowledge.py::_relevant_personal_context`` closes
that gap, reusing the identical literal-token-overlap relevance
``Memory.recall``'s own ``query`` parameter already provides elsewhere
in this same file - never a new relevance engine, never a per-topic
special case.

**Deliberately unfamiliar topics throughout.** Every taught fact below
names a subject this codebase has never mentioned anywhere else -
proof the mechanism generalizes to a topic invented for this test file
alone, never a fixed catalogue "if type == ...".
"""

from __future__ import annotations

import tempfile

import pytest

from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.services.memory.personal_context import all_current_personal_context
from scripts.control_center import build


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


def _current_remark(prompt: str) -> str:
    """Isolate the remark converse() was actually asked about from the
    Context section folded into the same prompt - the established
    isolation pattern this project's own objective-loop tests already
    use for the identical prompt shape."""
    return prompt.split("\n\nContext")[0]


class _CapturingProvider(LLMProvider):
    """A real LLMProvider double that answers honestly and remembers
    every prompt it was actually given - so a test can inspect exactly
    what evidence reached the Brain, not merely trust the final
    answer."""

    def __init__(self, answer: str = "a genuine ordinary answer"):
        self._answer = answer
        self.prompts: list[str] = []

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
        self.prompts.append(prompt)
        return Completion(text=self._answer, provider="test")


def _wired(**overrides):
    runtime, manager, channel, conversation = real_system(**overrides)
    provider = _CapturingProvider()
    brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return runtime, manager, channel, conversation, provider


class TestGenericIngestionAndCanonicalStorage:
    """Requirement 1/2: a never-before-seen kind of personal fact is
    ingested through the existing generalized mechanism (no new
    handler written for it) and stored canonically - real Memory,
    real provenance, no second store."""

    def test_a_wholly_novel_topic_is_stored_with_provenance_no_new_handler_needed(self):
        _, manager, _, _ = real_system()
        result = manager.handle_task(
            Task(description="I've learned advanced kombucha fermentation timing.")
        )
        assert result.succeeded

        stored = all_current_personal_context(recall=manager.memory.recall)
        assert len(stored) == 1
        item = stored[0]
        assert "kombucha fermentation timing" in item.topic
        assert item.status == "current"
        # Provenance: who noted it, and that it came from ordinary
        # conversation - never a second, undocumented store.
        record = manager.memory.recall(kind="personal_context", limit=1)[0]
        assert record.context["noted_by"] == manager.own_skills.name
        assert record.context["source"] == "conversation"


class TestPersistenceAcrossRestart:
    """Requirement 2: canonical storage survives a real restart - a
    fresh process reading the same persisted store sees the same
    current state, never re-taught."""

    def test_a_novel_topic_survives_a_real_restart(self):
        memory_dir = tempfile.mkdtemp()

        settings1 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager1, _, _ = build(settings1)
        manager1.handle_task(Task(description="I've learned mechanical keyboard switch tuning."))

        settings2 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager2, _, _ = build(settings2)
        stored = all_current_personal_context(recall=manager2.memory.recall)
        assert any("keyboard switch tuning" in item.topic for item in stored)


class TestRelevanceFilteringReachesIntelligence:
    """Requirement 3/4, the central proof and the actual gap this
    checkpoint closes: retrieved personal context genuinely reaches the
    Brain's own prompt, and only the topic actually relevant to the
    question does - a stored, unrelated topic never crowds it out."""

    def test_relevant_context_reaches_the_prompt_irrelevant_context_does_not(self):
        _, manager, _, _, provider = _wired()
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))
        manager.handle_task(Task(description="I've learned mechanical keyboard switch tuning."))

        result = manager.handle_task(
            Task(description="xyzzyplugh tell me about my kombucha fermentation timing")
        )
        assert result.succeeded
        assert len(provider.prompts) == 1
        prompt = provider.prompts[0]
        assert "kombucha fermentation timing" in prompt
        assert "keyboard switch tuning" not in prompt

    def test_the_reverse_topic_is_symmetric_not_a_coincidence(self):
        """The identical proof, the other direction - confirms this is
        genuine relevance scoring, not an accident of which topic was
        taught first or named more distinctively."""
        _, manager, _, _, provider = _wired()
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))
        manager.handle_task(Task(description="I've learned mechanical keyboard switch tuning."))

        result = manager.handle_task(
            Task(description="xyzzyplugh tell me about my mechanical keyboard switch tuning")
        )
        assert result.succeeded
        prompt = provider.prompts[0]
        assert "keyboard switch tuning" in prompt
        assert "kombucha fermentation timing" not in prompt

    def test_a_genuinely_broad_remark_still_falls_back_to_something_never_nothing(self):
        """Nothing in the question shares any real word with either
        stored topic - the honest fallback (existing, unchanged
        behaviour) still offers ambient context rather than silently
        contributing nothing, the identical default this mechanism
        already had before relevance scoring existed."""
        _, manager, _, _, provider = _wired()
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))

        result = manager.handle_task(Task(description="xyzzyplugh good morning"))
        assert result.succeeded
        assert "kombucha fermentation timing" in provider.prompts[0]


class TestMissingContextIsNeverFabricated:
    """Requirement 5: asking about something never taught produces no
    invented note - honest absence, not a guess."""

    def test_an_untaught_topic_contributes_no_fabricated_note(self):
        _, manager, _, _, provider = _wired()
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))

        result = manager.handle_task(
            Task(description="xyzzyplugh what is my favourite unicycle trick?")
        )
        assert result.succeeded
        prompt = provider.prompts[0]
        # The evidence section (everything after the user's own remark,
        # which of course still names "unicycle") must contain no
        # fabricated note about the untaught topic - only what is
        # genuinely stored (the kombucha fact, offered as the honest
        # fallback) ever appears there.
        evidence = prompt.split("\n\nContext")[1]
        assert "unicycle" not in evidence.lower()
        assert "Personal context: The user has learned advanced kombucha fermentation timing." in prompt


class TestAgentIndependence:
    """Requirement 6: personal context is the agent's own - a second,
    independently-built agent's memory shares none of it."""

    def test_a_second_independent_agent_sees_none_of_the_first_agents_context(self):
        _, manager1, _, _ = real_system()
        manager1.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))
        assert len(all_current_personal_context(recall=manager1.memory.recall)) == 1

        _, manager2, _, _ = real_system()
        assert all_current_personal_context(recall=manager2.memory.recall) == ()


class TestHalfBrainHonesty:
    """Requirement 7: Half Brain must not silently gain a false sense
    of reasoning merely because personal context happens to be on
    file - the existing honest "no reasoning provider" refusal must
    still hold, unchanged, deterministically, with no model consulted
    even to decide whether to refuse."""

    def test_half_brain_still_refuses_honestly_even_with_relevant_context_stored(self):
        _, manager, _, _ = real_system()  # no Brain wired - genuine Half Brain
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))

        result = manager.handle_task(
            Task(description="xyzzyplugh tell me about my kombucha fermentation timing")
        )
        assert not result.succeeded
        assert "no language model" in result.error.lower() or "reason" in result.error.lower()

    def test_deterministic_ingestion_itself_needs_no_brain_at_all(self):
        """Tier 1 ingestion (this test's own teaching sentence) is
        Half-Brain-safe by construction - proven by it having already
        succeeded, with no converse wired, in every test above. Stated
        here as its own explicit assertion."""
        _, manager, _, _ = real_system()
        assert not manager.can_reason
        result = manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))
        assert result.succeeded


class TestControlCenterIntegration:
    """Requirement 8: the real, existing Control Center surface
    (``services personal``, unchanged by this checkpoint) reads the
    identical canonical state execution reads - never a second,
    disconnected view."""

    def test_the_existing_personal_command_shows_the_real_novel_topic(self):
        from src.control_center.observer import ControlCenter

        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))

        centre = ControlCenter(runtime, manager)
        topics = centre.personal_context()
        assert any("kombucha fermentation timing" in t.topic for t in topics)
        assert topics[0].noted_by == manager.own_skills.name

    def test_the_terminal_personal_verb_renders_the_real_state(self):
        from scripts.control_center import run_command
        from src.control_center.observer import ControlCenter

        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I've learned advanced kombucha fermentation timing."))

        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "personal", 100, channel, conversation)
        assert "kombucha fermentation timing" in output


class TestNoHardcoding:
    """Requirement 10: the fix and the mechanism it completes read no
    fixed catalogue of topics, categories, or products - proven by
    this test file's own deliberately invented topic names being
    genuinely absent from the production source, and by grepping for
    the motivating example's own name."""

    def test_this_files_own_novel_topics_are_absent_from_production_source(self):
        from pathlib import Path

        source = (
            Path("src/manager/knowledge.py").read_text(encoding="utf-8")
            + Path("src/services/memory/personal_context.py").read_text(encoding="utf-8")
        )
        for forbidden in ("kombucha", "fermentation", "mechanical keyboard", "unicycle"):
            assert forbidden not in source.lower()

    def test_no_apple_or_product_specific_dependency_exists(self):
        from pathlib import Path

        source = (
            Path("src/manager/knowledge.py").read_text(encoding="utf-8")
            + Path("src/services/memory/personal_context.py").read_text(encoding="utf-8")
        )
        for forbidden in ("apple", "iphone", "ios", "siri"):
            assert forbidden not in source.lower()
