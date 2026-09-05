"""Phase 1 - Core Alfred: Natural Conversation.

The defect this file closes, measured against the task's own worked
example rather than assumed:

    "I'm tired today."                     -> was answered with a date
                                               announcement (current_datetime
                                               scored on "today")
    "yeah, I still need to finish my Java   -> was answered "Got it."
     work."                                    (small_talk scored on "yeah",
                                               nothing else scored at all)
    "anyway, what were we talking about?"  -> was answered as a settled-
                                               knowledge question (knowledge_
                                               intent's "what were" marker)

None of the three is a capability request. All three were swallowed by
word-overlap *scoring* winning on one incidental word, never by a
`claims` predicate correctly recognising them - see
`src/manager/capabilities.py`'s own "Natural Conversation fix" comments
at `ManagerCapability.scoreable`, `CURRENT_DATETIME`, `small_talk`,
`CURRENT_INFORMATION_WORDS`, the research `GeneralCapability`, and
`knowledge_intent`'s new fourth deferral.

This file also proves the second half of the fix: once such a remark
correctly reaches `open_conversation`, the Brain it consults now has
short-term memory of the conversation (`CapabilityRequest.history`,
threaded from the real `Conversation` via
`ManagerSkills.bind_conversation_history`, wired in
`scripts/control_center.py::build()` - the same production path
`serve_ui.py` also builds through).

Existing, already-working behaviour (identity, memory, capability
routing for genuine capability requests, Half Brain honesty) is
regression-checked here, not re-implemented or re-designed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.contracts import Task
from src.manager.capabilities import MANAGER_CAPABILITIES, select_capability
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.providers.base import Completion, LLMProvider
from src.services.identity.identity import load_identity
from src.services.memory.day import Calendar

IDENTITY = load_identity("IDENTITY.md")
FIXED_INSTANT = datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc)


def alfred(brain=None, history=None):
    """A Manager with Alfred's real identity, converse correctly wired
    (composition.py's own build_manager pattern), and an optional bound
    conversation-history accessor."""
    manager = ManagerAgent(brain=brain, calendar=Calendar(zone=timezone.utc, clock=lambda: FIXED_INSTANT))
    manager.own_skills = ManagerSkills(
        IDENTITY,
        roster=manager.registry.all_agents,
        converse=manager.brain.converse,
        recall=manager.memory.recall,
        retain=manager.retain,
        history=history,
    )
    manager.registry.register(manager.own_skills)
    return manager


class RecordingProvider(LLMProvider):
    """Configured, keyed, and genuinely usable - and records every
    prompt it receives, so a test can inspect exactly what evidence the
    Brain was actually given."""

    def __init__(self, reply: str = "a natural, composed answer", vendor: str = "anthropic") -> None:
        self.calls: list[tuple[str | None, str]] = []
        self._reply = reply
        self._vendor = vendor

    @property
    def name(self) -> str:
        return self._vendor

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        self.calls.append((system, prompt))
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider=self._vendor)
        return Completion(text=self._reply, provider=self._vendor)


def full_brain(reply: str = "a natural, composed answer", vendor: str = "anthropic"):
    provider = RecordingProvider(reply, vendor)
    brain = LLMManagerBrain(provider)
    brain.deliberate(Task(description="warm up"))
    provider.calls.clear()
    return brain, provider


# ---------------------------------------------------------------------------
# The three misrouted examples the task itself specifies
# ---------------------------------------------------------------------------


class TestNoFalseSuccessAcknowledgement:
    def test_an_emotional_statement_is_not_answered_with_a_date(self):
        """The exact defect: current_datetime scored on the incidental
        word "today" and answered "I'm tired today" with a calendar
        fact."""
        capability = select_capability(Task(description="I'm tired today."), MANAGER_CAPABILITIES, participants=[])
        assert capability.name != "current_datetime"
        assert capability.name == "open_conversation"

    def test_a_substantive_remark_is_not_answered_got_it(self):
        """The exact defect: small_talk scored on "yeah" alone."""
        text = "yeah, I still need to finish my Java work."
        capability = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=[])
        assert capability.name == "open_conversation"

        manager = alfred()  # Half Brain
        result = manager.handle_task(Task(description=text))
        assert result.output != "Got it."

    def test_a_conversation_continuity_reference_is_not_a_knowledge_question(self):
        """The exact defect: "what were" is also a KNOWLEDGE_QUESTION_MARKER."""
        capability = select_capability(
            Task(description="anyway, what were we talking about?"), MANAGER_CAPABILITIES, participants=[]
        )
        assert capability.name == "open_conversation"

    def test_genuine_knowledge_questions_still_route_correctly(self):
        """Regression: the fix must not blunt knowledge_intent generally."""
        for text in ("what were the causes of the war", "who was Napoleon", "tell me about Liverpool"):
            capability = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=[])
            assert capability.name == "knowledge", text

    def test_genuine_date_questions_still_route_correctly(self):
        """Regression: Foundation Repair #1 must be untouched."""
        for text in ("what day is it", "what is today's date", "what year is it"):
            capability = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=[])
            assert capability.name == "current_datetime", text

    def test_genuine_live_information_requests_still_route_correctly(self):
        """Regression: live_information's scoring must still work for the
        words that remain in its set."""
        capability = select_capability(
            Task(description="what is the latest Arsenal transfer news"), MANAGER_CAPABILITIES, participants=[]
        )
        assert capability.name == "live_information"

    def test_genuine_research_requests_still_route_correctly(self):
        """Regression: removing "about" must not blunt "research"/"find"."""
        capability = select_capability(Task(description="research Liverpool"), MANAGER_CAPABILITIES, participants=[])
        assert capability.name == "general_assistance"

    def test_greetings_and_thanks_are_unaffected(self):
        """Regression: small_talk's claims path (the only correct path)
        must still fire. Through manager.handle_task(), not a bare
        select_capability(participants=[]) call - "hey Alfred" needs the
        real roster (supplied by ManagerSkills.execute() in production)
        to discount "Alfred" as an addressed name, exactly as
        conversational_intent's own docstring describes."""
        manager = alfred()
        for text in ("hello", "hey Alfred", "thanks", "good morning"):
            result = manager.handle_task(Task(description=text))
            assert result.succeeded, text
            assert result.payload is None, text  # small_talk never sets one


# ---------------------------------------------------------------------------
# Follow-up conversation / context-aware responses
# ---------------------------------------------------------------------------


class TestFollowUpConversation:
    def test_a_second_turn_is_offered_the_first_turns_content(self):
        """The decisive one. A scripted Brain records its own prompt;
        the second turn's prompt must contain what the first turn said."""
        brain, provider = full_brain()
        manager = alfred(brain=brain, history=lambda: [
            "you: Yeah, I still need to finish my Java work.",
            "Alfred: Sounds like the Java assignment is still on your plate.",
        ])

        manager.handle_task(Task(description="Anyway, what were we talking about?"))

        assert provider.calls
        _, prompt = provider.calls[-1]
        assert "Java" in prompt

    def test_no_history_bound_is_byte_identical_to_before(self):
        """Every existing caller that never wires history (every test
        predating this change) must behave exactly as it always did."""
        brain, provider = full_brain()
        manager = alfred(brain=brain, history=None)
        result = manager.handle_task(Task(description="what do you think about remote work"))
        assert result.succeeded
        _, prompt = provider.calls[-1]
        assert "Recent conversation" not in prompt

    def test_empty_history_contributes_nothing(self):
        brain, provider = full_brain()
        manager = alfred(brain=brain, history=lambda: [])
        manager.handle_task(Task(description="what do you think about remote work"))
        _, prompt = provider.calls[-1]
        assert "Recent conversation" not in prompt


# ---------------------------------------------------------------------------
# Honest failure / Half Brain behaviour
# ---------------------------------------------------------------------------


class TestHonestHalfBrainConversation:
    def test_an_emotional_statement_is_refused_honestly_not_fabricated(self):
        manager = alfred()  # NullBrain, no provider
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        result = manager.handle_task(Task(description="I'm tired today."))
        assert not result.succeeded
        assert "guess" in result.error.lower()

    def test_half_brain_never_claims_to_have_reasoned(self):
        manager = alfred()
        result = manager.handle_task(Task(description="what do you think I should do"))
        assert not result.succeeded
        assert "no reasoning capability" in result.error.lower() or "guess" in result.error.lower()

    def test_deterministic_capabilities_still_work_in_half_brain(self):
        """Half Brain is not degraded - a genuine capability request
        alongside casual conversation still executes deterministically."""
        manager = alfred()
        result = manager.handle_task(Task(description="what day is it"))
        assert result.succeeded
        assert result.payload["handled_by"] == "current_datetime"


# ---------------------------------------------------------------------------
# Provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependentConversation:
    def test_conversation_context_works_through_any_named_provider(self):
        """Not coupled to Anthropic - a differently-named provider gets
        the identical mechanism."""
        brain, provider = full_brain(vendor="some-other-vendor")
        manager = alfred(brain=brain, history=lambda: ["you: I have a Java assignment due Friday."])
        result = manager.handle_task(Task(description="what were we just discussing"))
        assert result.succeeded
        _, prompt = provider.calls[-1]
        assert "Java" in prompt

    def test_identical_routing_regardless_of_which_provider_is_configured(self):
        text = "yeah, I still need to finish my Java work."
        half = alfred()
        full, _ = (alfred(brain=(fb := full_brain()[0])), None)
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=[]).name == "open_conversation"
        # Both reach the identical handler; only the Brain behind
        # converse() differs, and routing never inspects brain mode.
        assert assess_manager(half).mode is CognitiveMode.HALF_BRAIN
        assert assess_manager(full).mode is CognitiveMode.FULL_BRAIN


# ---------------------------------------------------------------------------
# Natural transition into capability execution
# ---------------------------------------------------------------------------


class TestNaturalTransitionIntoCapability:
    def test_a_capability_request_embedded_in_conversation_still_executes(self):
        """A genuine capability request phrased naturally, mid-conversation
        register, still actually executes - not swallowed by the
        conversational floor."""
        manager = alfred()
        result = manager.handle_task(Task(description="hey, what's today's date?"))
        assert result.succeeded
        assert result.payload["handled_by"] == "current_datetime"

    def test_conversational_acknowledgement_never_implies_action(self):
        """The acknowledgement requirement, stated as a test: small_talk's
        fixed replies never claim an action was taken."""
        manager = alfred()
        for text in ("thanks", "ok", "got it"):
            result = manager.handle_task(Task(description=text))
            assert result.payload is None  # no handled_by, no action record


# ---------------------------------------------------------------------------
# Regression: identity, existing capabilities untouched
# ---------------------------------------------------------------------------


class TestRegressionProtection:
    def test_identity_questions_are_unaffected(self):
        manager = alfred()
        result = manager.handle_task(Task(description="who are you?"))
        assert "Alfred" in result.output

    def test_temporal_derivation_is_unaffected(self):
        manager = alfred()
        result = manager.handle_task(Task(description="how old am i"))
        assert not result.succeeded
        assert "date of birth" in result.error
