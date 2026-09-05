"""Phase 1.2 Natural Conversation - final completion pass.

Closes the five remaining 🟡 requirements: Personality, Friendly/
approachable behavior, Natural emoji use, Natural conversation flow,
Clarification when required.

**What "practically verified" means here, stated once.** No real API
call is permitted, so nothing here can prove a real model's subjective
compliance with a personality instruction - that claim is never made.
What every test below proves instead is the *behavioural contract*: that
the real production path (build() -> exchange() -> ManagerSkills ->
capability routing -> Brain) actually delivers personality/warmth/emoji
guidance and conversation history into the prompt a model would read,
and that whatever a model chooses to say back is relayed to the person
unaltered - never stripped, never filtered, never overridden by a
canned string. A scripted provider that conditions its own reply on
what it was actually sent is the strongest available proof of that
contract without spending real money.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange

TONE_MARKER = "warm, friendly and conversational"
WARMTH_MARKER = "Warmth: high"
EMOJI_MARKER = "Emoji:"


class ScriptedFullBrainProvider(LLMProvider):
    """Configured, keyed, genuinely usable - and its converse() replies
    are computed by a test-supplied function from the actual system
    prompt and message it receives, never a single hardcoded string.
    Records every call so a test can also inspect what was sent."""

    def __init__(self, respond=None, vendor: str = "anthropic") -> None:
        self.calls: list[tuple[str | None, str]] = []
        self._respond = respond or (lambda system, prompt: "a natural, composed answer")
        self._vendor = vendor

    @property
    def name(self) -> str:
        return self._vendor

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        self.calls.append((system, prompt))
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider=self._vendor)
        return Completion(text=self._respond(system, prompt), provider=self._vendor)

    @property
    def converse_calls(self) -> list[tuple[str | None, str]]:
        return [(s, p) for s, p in self.calls if not (s and "Reply with a single JSON object" in s)]


def half_brain_system():
    # memory="ephemeral" - this suite calls the real build() repeatedly
    # and must never touch data/memory, the real, already-populated
    # production store. Ephemeral still supports remember()/recall()
    # for the lifetime of one test, which is all a multi-turn exchange()
    # sequence within a single test needs.
    settings = Settings.from_env(env={}, memory="ephemeral")
    return build(settings)  # runtime, manager, channel, conversation


def full_brain_system(respond=None, vendor: str = "anthropic"):
    runtime, manager, channel, conversation = half_brain_system()
    provider = ScriptedFullBrainProvider(respond, vendor)
    # personality=runtime.identity.personality - the exact argument
    # composition.py's own build_brain(settings, provider,
    # identity.personality) always supplies in production. Omitting it
    # here would silently test a brain with no personality fragment at
    # all, which is not the system this task is verifying.
    brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    brain.deliberate(Task(description="warm up"))  # the call that proves usability
    provider.calls.clear()
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN
    return runtime, manager, channel, conversation, provider


# ---------------------------------------------------------------------------
# 1. Personality
# ---------------------------------------------------------------------------


class TestPersonalityContract:
    def test_personality_reaches_the_brain_through_the_real_production_path(self):
        def respond(system, prompt):
            return "PERSONALITY_PRESENT" if system and TONE_MARKER in system else "PERSONALITY_ABSENT"

        _, manager, channel, conversation, provider = full_brain_system(respond)
        turn = exchange(channel, conversation, "What do you think about remote work?")

        assert turn.succeeded
        assert turn.capability == "open_conversation"
        assert turn.outcome == "PERSONALITY_PRESENT"

    def test_personality_is_consistently_delivered_across_separate_turns(self):
        seen: list[bool] = []

        def respond(system, prompt):
            seen.append(bool(system and TONE_MARKER in system))
            return "ok"

        _, manager, channel, conversation, provider = full_brain_system(respond)
        exchange(channel, conversation, "What do you think about remote work?")
        exchange(channel, conversation, "What about hybrid schedules specifically?")

        assert seen == [True, True], "personality guidance must reach every turn, not just the first"

    def test_personality_is_provider_neutral(self):
        def respond(system, prompt):
            return "PERSONALITY_PRESENT" if system and TONE_MARKER in system else "PERSONALITY_ABSENT"

        _, manager, channel, conversation, provider = full_brain_system(respond, vendor="some-other-vendor")
        turn = exchange(channel, conversation, "What do you think about remote work?")

        assert turn.outcome == "PERSONALITY_PRESENT"
        assert provider.name == "some-other-vendor"

    def test_half_brain_is_honestly_personality_free_never_fabricated(self):
        """The other half of the contract: with no model at all, Alfred
        must not simulate a personality-flavoured answer deterministically."""
        _, manager, channel, conversation = half_brain_system()
        turn = exchange(channel, conversation, "What do you think about remote work?")

        assert not turn.succeeded
        assert "guess" in turn.outcome.lower()


# ---------------------------------------------------------------------------
# 2. Friendly / approachable behavior
# ---------------------------------------------------------------------------


class TestFriendlyApproachableBehavior:
    def test_an_ordinary_emotional_remark_reaches_the_brain_with_warmth_guidance(self):
        def respond(system, prompt):
            if system and WARMTH_MARKER in system:
                return "That sounds like a long day - want to talk about it?"
            return "NO_WARMTH_GUIDANCE_DELIVERED"

        _, manager, channel, conversation, provider = full_brain_system(respond)
        turn = exchange(channel, conversation, "I'm tired today.")

        assert turn.capability == "open_conversation"  # not misrouted
        assert turn.outcome == "That sounds like a long day - want to talk about it?"

    def test_it_does_not_falsely_claim_an_action_occurred(self):
        _, manager, channel, conversation, provider = full_brain_system()
        turn = exchange(channel, conversation, "I'm tired today.")

        assert turn.outcome != "Got it."
        assert turn.capability != "current_datetime"

    def test_half_brain_recognises_the_remark_as_conversation_and_refuses_honestly(self):
        _, manager, channel, conversation = half_brain_system()
        turn = exchange(channel, conversation, "I'm tired today.")

        assert turn.capability == "open_conversation"
        assert not turn.succeeded
        assert "guess" in turn.outcome.lower()


# ---------------------------------------------------------------------------
# 3. Natural emoji use
# ---------------------------------------------------------------------------


class TestNaturalEmojiUse:
    def test_emoji_guidance_reaches_the_brain(self):
        def respond(system, prompt):
            return "EMOJI_GUIDANCE_PRESENT" if system and EMOJI_MARKER in system else "ABSENT"

        _, manager, channel, conversation, provider = full_brain_system(respond)
        turn = exchange(channel, conversation, "I'm feeling really good about things today.")
        assert turn.outcome == "EMOJI_GUIDANCE_PRESENT"

    def test_a_response_containing_an_emoji_is_relayed_unaltered(self):
        """Nothing in the pipeline strips or mangles an emoji the model
        chose to use."""
        def respond(system, prompt):
            return "That's brilliant news 🎉 - well deserved."

        _, manager, channel, conversation, provider = full_brain_system(respond)
        turn = exchange(channel, conversation, "I'm feeling really good about things today.")

        assert turn.outcome == "That's brilliant news 🎉 - well deserved."
        assert "🎉" in turn.outcome

    def test_a_response_without_an_emoji_is_not_force_decorated(self):
        """Nothing in the pipeline inserts an emoji that was not there -
        emoji use must never be mechanical."""
        def respond(system, prompt):
            return "That's a solid plan for the evening."

        _, manager, channel, conversation, provider = full_brain_system(respond)
        turn = exchange(channel, conversation, "I'm going to cook dinner and read tonight.")

        assert turn.outcome == "That's a solid plan for the evening."
        assert not any(ch for ch in turn.outcome if ord(ch) > 0x2600)  # no emoji-range char introduced

    def test_half_brain_never_fabricates_emoji_flavoured_text(self):
        _, manager, channel, conversation = half_brain_system()
        turn = exchange(channel, conversation, "I'm feeling really good about things today.")
        assert not any(ch for ch in turn.outcome if ord(ch) > 0x2600)


# ---------------------------------------------------------------------------
# 4. Natural conversation flow
# ---------------------------------------------------------------------------


class TestNaturalConversationFlow:
    def test_the_four_turn_scenario_stays_in_one_conversation_without_canned_resets(self):
        prompts: list[str] = []

        def respond(system, prompt):
            prompts.append(prompt)
            if "java" in prompt.lower():
                return "Sounds like the Java work is still the thing on your plate."
            return "That's alright - I'm here."

        _, manager, channel, conversation, provider = full_brain_system(respond)

        t1 = exchange(channel, conversation, "Hey Alfred.")
        t2 = exchange(channel, conversation, "I'm tired today.")
        t3 = exchange(channel, conversation, "Yeah, I still need to finish my Java work.")
        t4 = exchange(channel, conversation, "Anyway, what were we talking about?")

        # Correct classification at every turn - never collapsed into an
        # unrelated deterministic capability.
        assert t1.capability == "small_talk"
        assert t2.capability == "open_conversation"
        assert t3.capability == "open_conversation"
        assert t4.capability == "open_conversation"

        # None of the conversational turns produced a false-success or
        # non-sequitur canned reply.
        for t in (t2, t3, t4):
            assert t.outcome != "Got it."

        # Turn 4's prompt genuinely carried turn 3's content forward -
        # the scripted provider could only produce the Java-aware
        # answer because "java" was actually present in what it read.
        assert t4.outcome == "Sounds like the Java work is still the thing on your plate."
        assert "java" in prompts[-1].lower()

        # The conversation object itself is one continuous room, not
        # four independent ones.
        assert len(conversation) >= 4

    def test_topic_continuation_where_the_second_statement_depends_on_the_first(self):
        """"The second one" is only resolvable if the first statement's
        content actually reached the Brain."""
        def respond(system, prompt):
            if "python project" in prompt.lower():
                return "Good call - the Python project it is."
            return "UNRESOLVED_REFERENCE"

        _, manager, channel, conversation, provider = full_brain_system(respond)

        exchange(channel, conversation, "I'm deciding between my Java assignment or my Python project tonight.")
        turn = exchange(channel, conversation, "Let's go with the second one.")

        assert turn.outcome == "Good call - the Python project it is."

    def test_statement_then_question_both_reach_the_same_conversational_path(self):
        _, manager, channel, conversation, provider = full_brain_system()
        statement = exchange(channel, conversation, "I've been thinking about switching careers.")
        question = exchange(channel, conversation, "What do you think I should consider?")

        assert statement.capability == "open_conversation"
        assert question.capability == "open_conversation"


# ---------------------------------------------------------------------------
# 5. Clarification when required
# ---------------------------------------------------------------------------


class TestClarificationWhenRequired:
    def test_a_missing_required_parameter_is_named_not_guessed(self):
        """Uses an existing capability (temporal_derivation) that
        genuinely cannot proceed without a stored fact - never an
        invented capability."""
        _, manager, channel, conversation = half_brain_system()
        turn = exchange(channel, conversation, "how old am I?")

        assert not turn.succeeded
        assert "date of birth" in turn.outcome.lower()
        assert "tell me" in turn.outcome.lower()  # asks, does not guess

    def test_identical_honest_clarification_in_full_brain_too(self):
        """temporal_derivation never touches the Brain - Full Brain
        availability changes nothing about this specific honest
        request for missing information."""
        _, manager, channel, conversation, provider = full_brain_system()
        turn = exchange(channel, conversation, "how old am I?")

        assert not turn.succeeded
        assert "date of birth" in turn.outcome.lower()
        assert provider.converse_calls == []  # never spent a call guessing

    def test_the_users_answer_in_a_later_turn_completes_the_original_task(self):
        """The conversation context requirement, proven behaviourally:
        after being asked, the person answers, and the *same*
        capability now succeeds - using the existing retention path
        (session.py / user_facts.py), not a new mechanism."""
        _, manager, channel, conversation = half_brain_system()

        first = exchange(channel, conversation, "how old am I?")
        assert not first.succeeded

        exchange(channel, conversation, "I was born September 27, 2006.")

        second = exchange(channel, conversation, "how old am I?")
        assert second.succeeded
        assert "years old" in second.outcome.lower()

    def test_full_brain_is_architecturally_free_to_ask_a_genuine_clarifying_question(self):
        """Not a hardcoded phrase - proof that nothing in the pipeline
        suppresses or reshapes a question-shaped answer a model chooses
        to give for a genuinely ambiguous, open-ended remark."""
        def respond(system, prompt):
            return "Do you mean the Java assignment, or something else on your mind?"

        _, manager, channel, conversation, provider = full_brain_system(respond)
        turn = exchange(channel, conversation, "I'm not sure what to do about it.")

        assert turn.outcome == "Do you mean the Java assignment, or something else on your mind?"
        assert turn.outcome.strip().endswith("?")

    def test_half_brain_does_not_fabricate_a_clarifying_question(self):
        """Half Brain must not pretend it can ask a natural clarifying
        question through a model that is not there."""
        _, manager, channel, conversation = half_brain_system()
        turn = exchange(channel, conversation, "I'm not sure what to do about it.")

        assert not turn.succeeded
        assert "guess" in turn.outcome.lower()
