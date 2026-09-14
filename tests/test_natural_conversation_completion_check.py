"""Final Natural Conversation completion check, before Phase 1.3.

CHECK found a real, confirmed, widespread defect in the capability-routing
layer: several capabilities declared a `claims=` predicate that already
recognised the real request shape precisely (each one's own comment already
argued word-overlap scoring was unsafe for it), but never actually set
`scoreable=False` - so an ordinary English word appearing in unrelated,
casual conversation ("Remind me to call the dentist tomorrow morning.")
still won the word-overlap fallback and reached the wrong handler,
producing a genuinely broken reply ("I'm not sure what communication
request that is.") through the real production path. Confirmed for
`communication`, `record_this`, `forget`, `recall_memory`, `about_self`,
`temporal_derivation`, `milestone_recognition`, `personal_recurring_events`,
`connected_action` and `learning_context` - ten capabilities, each fixed
the same way `current_datetime`/`small_talk` already were.

Two capabilities that looked like the same bug were investigated and left
unchanged, because the evidence pointed the other way: `live_information`'s
scoreable=True is load-bearing for real, already-tested requests that score
only on "now" ("is Arsenal linked with any wingers right now?"), and
`participant_invitation`'s is load-bearing for `invite_participant`'s own
honest "no such specialist is registered" answer to an *unregistered* name
("invite Cartographer"), which its own `claims` predicate cannot recognise
by design (it requires an already-registered name). Both attempted fixes
were reverted after the real test suite caught the regression - recorded
here so neither is "fixed" again by accident.

Every test below exercises the real production path
(`scripts/control_center.py::build()` + `scripts/exchange.py::exchange()`)
with a scripted Full-Brain provider standing in for the funded model this
environment does not have - never a bare unit call to a routing function
standing in for "Alfred answered this well."
"""

from __future__ import annotations

from src.config.settings import Settings
from src.manager.capabilities import select_capability, MANAGER_CAPABILITIES
from src.manager.llm_brain import CONVERSE_SYSTEM_PROMPT, LLMManagerBrain
from src.core.contracts import Task
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange

_ROSTER = ("Alfred", "Arthur")


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


class RecordingProvider(LLMProvider):
    """Records every prompt/system pair and answers with a fixed,
    inspectable reply for genuine conversation, never JSON."""

    def __init__(self, answer: str = "a natural conversational answer"):
        self.calls: list[tuple[str, str]] = []
        self._answer = answer

    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        system = system or ""
        self.calls.append((system, prompt))
        if "should_retain" in system or "narrow question about a remark" in system:
            return Completion(text='{"should_retain": false, "content": null}', provider="anthropic")
        if "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider="anthropic")
        return Completion(text=self._answer, provider="anthropic")


def _wired(answer="a natural conversational answer", retention_json=None):
    runtime, manager, channel, conversation = real_system()
    provider = RecordingProvider(answer)
    if retention_json is not None:
        original_complete = provider.complete

        def complete(prompt, *, system=None):
            if system and ("should_retain" in system or "narrow question about a remark" in system):
                return Completion(text=retention_json, provider="anthropic")
            return original_complete(prompt, system=system)

        provider.complete = complete  # type: ignore[method-assign]
    brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return runtime, manager, channel, conversation, provider


class TestConfirmedRoutingDefectsAreFixed:
    """The concrete, regression-pinned bugs CHECK found - each proven both
    at the routing layer and through a real, full production turn."""

    def test_task_intent_plus_content_in_one_utterance_no_longer_misroutes(self):
        """The smoking-gun case: a reminder request containing the bare
        word "call" used to be answered "I'm not sure what communication
        request that is." - a wrong, robotic reply to a task+content
        utterance, exactly the failure mode this check was commissioned
        to find. At the time this test was written, Tasks did not exist,
        so the honest fix available then was falling through to
        open_conversation - proven by asserting the *absence* of the old
        broken reply, never a claim that a Brain-composed "I can't set a
        real reminder yet" was the right answer.

        Phase 5.1 (Tasks) gave this exact utterance a genuinely better
        destination: create_task's own claims= predicate ("remind me
        to") now recognises it precisely, the identical
        scoreable=False/claims discipline this whole file is about,
        applied to a capability that did not exist yet when CHECK ran.
        The original defect - "call" winning word-overlap scoring for
        communication - is still the fact being pinned; what changed is
        only that a genuinely better handler now claims the request
        first, so there is no fallback for the old defect to reach.
        """
        cap = select_capability(
            Task(description="Remind me to call the dentist tomorrow morning."),
            MANAGER_CAPABILITIES, participants=list(_ROSTER),
        )
        assert cap.name == "create_task"

        _, manager, channel, conversation, provider = _wired(
            answer="Got it - I'll treat that as something to keep in mind, though I can't set a real reminder yet."
        )
        turn = exchange(channel, conversation, "Remind me to call the dentist tomorrow morning.")
        assert turn.succeeded
        assert "not sure what communication request" not in turn.outcome
        # The genuinely better outcome: a real task was created, not a
        # Brain-composed placeholder standing in for one.
        assert "call the dentist" in turn.outcome

    def test_ordinary_sentences_containing_capability_keywords_reach_conversation(self):
        """Ten confirmed collisions, each an ordinary remark that shares
        exactly one word with a capability's declared vocabulary and
        means nothing like it - all must reach open_conversation, never
        the unrelated handler."""
        probes = [
            "That was a new personal record for me at the gym today.",
            "I feel really connected to this project lately.",
            "I always forget where I put my keys, it drives me crazy.",
            "I have been learning to cook lately, it is fun.",
            "That joke is not really age appropriate for the team.",
            "I do not recall saying anything like that.",
            "My birthday is coming up and I have no plans yet.",
            "Reaching that milestone felt amazing honestly.",
            "I need to introduce this new feature to the client tomorrow.",
        ]
        for text in probes:
            cap = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=list(_ROSTER))
            assert cap.name == "open_conversation", text

    def test_genuine_requests_for_the_fixed_capabilities_still_route_correctly(self):
        """The fix must not have cost any real recall - each capability's
        own genuine, claims-recognised shape still reaches its own
        handler."""
        genuine = [
            ("record_this", "record this for later: pick up dry cleaning"),
            ("communication", "call Mom"),
            ("forget", "forget that we prefer blue"),
            ("personal_context", "I am studying for my chemistry exam next week."),
            ("temporal_derivation", "how old am I"),
            ("recall_memory", "what do you remember about the Q3 roadmap"),
            ("personal_recurring_events", "my mom's birthday is March 24"),
            ("milestone_recognition", "I got the internship!"),
            ("about_self", "who are you"),
        ]
        for expected, text in genuine:
            cap = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=list(_ROSTER))
            assert cap.name == expected, text

    def test_the_two_investigated_and_reverted_cases_still_work_as_designed(self):
        """Recorded so neither regressed fix is silently reintroduced:
        live_information genuinely needs "now" for already-tested
        requests, and participant_invitation genuinely needs scoring for
        an unregistered-name invitation's honest refusal."""
        cap = select_capability(
            Task(description="is Arsenal linked with any wingers right now"),
            MANAGER_CAPABILITIES, participants=list(_ROSTER),
        )
        assert cap.name == "live_information"

        cap = select_capability(
            Task(description="invite Cartographer"), MANAGER_CAPABILITIES, participants=list(_ROSTER),
        )
        assert cap.name == "participant_invitation"


class TestWholeUtteranceReachesTheBrainIntact:
    def test_a_multi_part_utterance_is_never_truncated_before_reaching_converse(self):
        """The exact 'multi-part utterances, not fragmented' probe: the
        full, unedited text - reaction, a question, and an aside - must
        be what the model actually receives."""
        full_text = (
            "That's hilarious, also can you tell me what you make of all this, "
            "and by the way we finished the project early!"
        )
        _, manager, channel, conversation, provider = _wired(answer="Ha! And congratulations on finishing early.")
        turn = exchange(channel, conversation, full_text)
        assert turn.succeeded
        assert turn.outcome == "Ha! And congratulations on finishing early."
        conversational_prompts = [p for s, p in provider.calls if "having a direct conversation" in s]
        assert any(full_text in p for p in conversational_prompts)

    def test_emotional_casual_content_is_answered_not_treated_as_an_instruction(self):
        text = "I can't believe my boss just yelled at me in front of everyone, this is so unfair."
        cap = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=list(_ROSTER))
        assert cap.name == "open_conversation"

        _, manager, channel, conversation, provider = _wired(
            answer="That sounds really unfair, I'm sorry you had to deal with that."
        )
        turn = exchange(channel, conversation, text)
        assert turn.outcome == "That sounds really unfair, I'm sorry you had to deal with that."


class TestMultiTurnContextAndFollowUps:
    def test_a_follow_up_genuinely_depends_on_what_was_said_earlier(self):
        """Unseen wording, two turns: the second turn names nothing on
        its own ("it", "that") and only makes sense with the first
        turn's own content - the shape the task names as 'follow-up
        statements that depend on previous turns.'"""
        _, manager, channel, conversation, provider = _wired(answer="acknowledged")
        exchange(channel, conversation, "We are calling the new internal tool Nightjar.")

        capturing = RecordingProvider(answer="Nightjar is the name you just gave it.")
        manager.brain = LLMManagerBrain(capturing)
        manager.own_skills._converse = manager.brain.converse

        turn = exchange(channel, conversation, "What did we just decide to call it?")
        assert turn.outcome == "Nightjar is the name you just gave it."
        conversational_prompts = [p for s, p in capturing.calls if "having a direct conversation" in s]
        assert any("Nightjar" in p for p in conversational_prompts)

    def test_three_turns_the_third_depends_on_the_first_not_the_second(self):
        """A stronger context test: turn 2 is unrelated small talk, and
        turn 3's follow-up still has to reach back past it to turn 1 -
        proving real conversation history, not just 'the last thing
        said', reaches the model."""
        _, manager, channel, conversation, provider = _wired(answer="acknowledged")
        exchange(channel, conversation, "The client we're pitching on Thursday is called Solvira.")
        exchange(channel, conversation, "haha anyway, how's your day going")

        capturing = RecordingProvider(answer="You're pitching Solvira on Thursday.")
        manager.brain = LLMManagerBrain(capturing)
        manager.own_skills._converse = manager.brain.converse

        turn = exchange(channel, conversation, "Remind me who we're pitching again and when.")
        assert turn.outcome == "You're pitching Solvira on Thursday."
        conversational_prompts = [p for s, p in capturing.calls if "having a direct conversation" in s]
        assert any("Solvira" in p and "Thursday" in p for p in conversational_prompts)


class TestDictatedContentAfterUnseenPhrasing:
    """The exact probe: content dictated after 'take notes', 'take this
    down', 'record this', etc. Only 'record this' is a fixed phrase this
    project already recognised deterministically; the other two, and any
    wording nobody wrote down, must still work through genuine Brain-
    judged understanding (discern_retention) - proven here with wording
    that shares no trigger word with any existing regex."""

    def test_take_notes_colon_content_is_genuinely_retained(self):
        _, manager, channel, conversation, _ = _wired(
            retention_json=(
                '{"should_retain": true, "content": '
                '"finalize the budget by Friday and loop in accounting"}'
            )
        )
        exchange(
            channel, conversation,
            "Take notes: we need to finalize the budget by Friday, and also loop in accounting.",
        )
        records = manager.memory.recall(limit=10)
        assert any("finalize the budget by Friday" in r.content for r in records)

    def test_take_this_down_dash_content_is_genuinely_retained(self):
        _, manager, channel, conversation, _ = _wired(
            retention_json='{"should_retain": true, "content": "the client wants the blue variant, not the red one"}'
        )
        exchange(channel, conversation, "Take this down - the client wants the blue variant, not the red one.")
        records = manager.memory.recall(limit=10)
        assert any("blue variant" in r.content for r in records)

    def test_jot_this_down_is_recognised_despite_sharing_no_word_with_any_existing_trigger(self):
        """'jot' appears in no regex, no marker list and no capability
        word anywhere in this project - genuine understanding is the
        only way this could ever be recognised."""
        _, manager, channel, conversation, _ = _wired(
            retention_json='{"should_retain": true, "content": "the wifi password is on the fridge"}'
        )
        exchange(channel, conversation, "jot this down for me: the wifi password is on the fridge")
        records = manager.memory.recall(limit=10)
        assert any("wifi password" in r.content for r in records)


class TestAmbiguityInstructionReachesTheRealPrompt:
    def test_the_converse_system_prompt_instructs_clarification_over_guessing(self):
        assert "ask a short clarifying question" in CONVERSE_SYSTEM_PROMPT
        assert "instead of picking a guess" in CONVERSE_SYSTEM_PROMPT

    def test_an_ambiguous_follow_up_reaches_the_real_brain_with_that_instruction_present(self):
        """Whether the model actually asks for clarification is a model
        judgement call this environment cannot fund a real answer for -
        what this project's own code controls, and what this test
        proves, is that the instruction to do so genuinely reaches the
        real system prompt used for the real call."""
        _, manager, channel, conversation, provider = _wired(
            answer="Which thing did you mean - the roadmap doc or the pricing sheet?"
        )
        turn = exchange(channel, conversation, "Can you help me with that thing we discussed?")
        assert turn.outcome == "Which thing did you mean - the roadmap doc or the pricing sheet?"
        conversational_systems = [s for s, p in provider.calls if "having a direct conversation" in s]
        assert conversational_systems
        assert all("ask a short clarifying question" in s for s in conversational_systems)


class TestCasualConversationStillFeelsLikeConversation:
    def test_a_casual_greeting_style_remark_gets_a_real_reply_not_a_capability_dump(self):
        text = "so anyway, what were we talking about earlier?"
        cap = select_capability(Task(description=text), MANAGER_CAPABILITIES, participants=list(_ROSTER))
        assert cap.name == "open_conversation"

        _, manager, channel, conversation, provider = _wired(answer="We hadn't started yet - what's on your mind?")
        turn = exchange(channel, conversation, text)
        assert turn.outcome == "We hadn't started yet - what's on your mind?"

    def test_provider_independence_is_unaffected_by_any_of_this_stages_changes(self):
        """Regression guard: none of this stage's capability-routing
        fixes touch Brain/provider independence - re-proven with a
        second, distinct provider mid-conversation."""
        _, manager, channel, conversation, provider_a = _wired(answer="answer from provider A")
        exchange(channel, conversation, "remember that the project codename is Halcyon")

        provider_b = RecordingProvider(answer="answer from provider B")
        manager.brain = LLMManagerBrain(provider_b, personality=manager.own_skills.personality)
        manager.own_skills._converse = manager.brain.converse
        turn = exchange(channel, conversation, "tell me something else")
        assert turn.outcome == "answer from provider B"
        assert any("Halcyon" in r.content for r in manager.memory.recall(limit=10))

