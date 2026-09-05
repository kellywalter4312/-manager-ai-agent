"""0.2 Final Surgical Pass - natural conversation is a reusable capability
of the canonical agent architecture, not something only the orchestrator
has.

The confirmed gap this closes: before this file's own code changes, a
specialist like Arthur (EmailAgent) had exactly two conversational shapes
(a greeting, "who are you" - see CognitiveAgent.conversational_reply's own
Stage 17.22 history) and nothing else. Anything else - "tell me about
Napoleon", a joke, an ordinary follow-up naming none of Arthur's own
domain vocabulary - fell straight into EmailAgent.execute()'s domain
default: fetching and summarising the mailbox. Confirmed empirically
before this fix: EmailAgent(sample_inbox()).execute(Task(description=
"tell me about Napoleon's rise to power")) genuinely fetched and
summarised the sample inbox, exactly failure condition 14's "treats every
conversation as a task."

The fix is additive and shared, never duplicated per specialist:
SpecialistBrain.converse (mirroring ManagerBrain.converse's own
default-degrading shape) and CognitiveAgent.general_conversation (mirroring
the orchestrator's own open_conversation capability) live exactly once, in
the base classes every specialist already inherits. EmailAgent's own
addition is the one thing only it can know: what its own domain vocabulary
looks like, so it can tell "this is not email work" apart from "this is."
"""

from __future__ import annotations

import pytest

from src.agents.brain import Insight, SpecialistBrain
from src.agents.email_agent import EmailAgent
from src.agents.llm_brain import LLMSpecialistBrain
from src.core.contracts import Task, TaskStatus
from src.providers.availability import AvailabilityState
from src.providers.base import Completion, LLMProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox
from src.services.email.sample_inbox import sample_inbox
from src.services.identity.personality import AgentPersonality
from src.config.settings import Settings
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**env_overrides):
    settings = Settings.from_env(env=env_overrides, memory="ephemeral")
    return build(settings)


class _CountingMailbox(Mailbox):
    """Proves the mailbox was, or was not, ever actually touched - a
    stronger check than inspecting the answer text."""

    def __init__(self, inbox):
        self._inbox = inbox
        self.fetch_calls = 0

    def fetch(self, limit=20, label=None):
        self.fetch_calls += 1
        return self._inbox.fetch(limit=limit, label=label)


class RecordingProvider(LLMProvider):
    def __init__(self, *, answer: str = "a genuine conversational answer"):
        self.calls: list[tuple[str, str]] = []
        self._answer = answer

    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        system = system or ""
        self.calls.append((system, prompt))
        if "having a direct conversation" in system:
            return Completion(text=self._answer, provider="anthropic")
        # A consideration-shaped call (domain analysis) - never exercised
        # by this file's own scenarios, but answered honestly if it were.
        return Completion(
            text='{"summary": "unused", "confidence": 0.0, "unknowns": []}',
            provider="anthropic",
        )


class RaisingProvider(LLMProvider):
    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        raise RuntimeError("the account has no credits")


class TestSpecialistBrainDefaultsToNoView:
    def test_the_base_class_gives_an_honest_no_view(self):
        class Bare(SpecialistBrain):
            @property
            def name(self):
                return "bare"

            def consider(self, task, notes=()):
                return Insight.none(reason="unused", source="bare")

        insight = Bare().converse(Task(description="tell me about Napoleon"))
        assert not insight.is_useful


class TestLLMSpecialistBrainConverse:
    def test_a_genuine_answer_is_returned_with_full_confidence(self):
        provider = RecordingProvider(answer="Napoleon rose to power after the French Revolution.")
        brain = LLMSpecialistBrain(provider, domain="email triage")
        insight = brain.converse(Task(description="tell me about Napoleon"))
        assert insight.is_useful
        assert insight.summary == "Napoleon rose to power after the French Revolution."

    def test_a_failing_provider_degrades_honestly(self):
        brain = LLMSpecialistBrain(RaisingProvider())
        insight = brain.converse(Task(description="tell me a joke"))
        assert not insight.is_useful
        assert brain.provider_availability.state is AvailabilityState.UNAVAILABLE

    def test_an_empty_completion_degrades_honestly(self):
        class EmptyProvider(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                return Completion(text="", provider="anthropic")

        insight = LLMSpecialistBrain(EmptyProvider()).converse(Task(description="x"))
        assert not insight.is_useful

    def test_personality_reaches_converse_but_never_consider(self):
        """Consideration is a judgement about the person's own work, read
        back as evidence - not a reply presented to them - so a
        personality's tone must never reach it, unlike converse()."""
        provider = RecordingProvider()
        personality = AgentPersonality(key="arthur", tone="brisk and dry", humor="dry, quick")
        brain = LLMSpecialistBrain(provider, domain="email triage", personality=personality)

        brain.converse(Task(description="tell me a joke"))
        brain.consider(Task(description="judge this message"))

        converse_system, consider_system = provider.calls[0][0], provider.calls[1][0]
        assert "brisk and dry" in converse_system
        assert "brisk and dry" not in consider_system


class TestCognitiveAgentGeneralConversation:
    def test_no_brain_configured_is_an_honest_failure(self):
        agent = EmailAgent(sample_inbox())
        result = agent.general_conversation(Task(description="tell me about Napoleon"))
        assert result.status is TaskStatus.FAILED
        assert "language model" in result.error.lower()

    def test_a_real_brain_answers_genuinely(self):
        provider = RecordingProvider(answer="Napoleon was a French military leader.")
        agent = EmailAgent(sample_inbox(), brain=LLMSpecialistBrain(provider))
        result = agent.general_conversation(Task(description="tell me about Napoleon"))
        assert result.status is TaskStatus.COMPLETED
        assert result.output == "Napoleon was a French military leader."


class TestEmailAgentRoutesNonDomainRemarksToConversationNotTheMailbox:
    def test_napoleon_never_touches_the_mailbox(self):
        """The exact confirmed defect: before this fix, this fetched and
        summarised the sample inbox."""
        mailbox = _CountingMailbox(sample_inbox())
        agent = EmailAgent(mailbox)
        agent.execute(Task(description="tell me about Napoleon's rise to power"))
        assert mailbox.fetch_calls == 0

    def test_napoleon_with_no_brain_is_an_honest_refusal_not_a_mailbox_dump(self):
        mailbox = _CountingMailbox(sample_inbox())
        agent = EmailAgent(mailbox)
        result = agent.execute(Task(description="tell me about Napoleon's rise to power"))
        assert result.status is TaskStatus.FAILED
        assert mailbox.fetch_calls == 0

    def test_napoleon_with_a_real_brain_gets_a_genuine_answer(self):
        provider = RecordingProvider(answer="Napoleon rose to power after the French Revolution.")
        mailbox = _CountingMailbox(sample_inbox())
        agent = EmailAgent(mailbox, brain=LLMSpecialistBrain(provider))
        result = agent.execute(Task(description="tell me about Napoleon's rise to power"))
        assert result.status is TaskStatus.COMPLETED
        assert result.output == "Napoleon rose to power after the French Revolution."
        assert mailbox.fetch_calls == 0

    def test_a_joke_is_handled_the_same_way(self):
        provider = RecordingProvider(answer="Ha! Good one.")
        mailbox = _CountingMailbox(sample_inbox())
        agent = EmailAgent(mailbox, brain=LLMSpecialistBrain(provider))
        result = agent.execute(Task(description="haha that's actually pretty funny"))
        assert result.status is TaskStatus.COMPLETED
        assert mailbox.fetch_calls == 0

    def test_genuine_email_requests_still_reach_the_mailbox_unaffected(self):
        """Preserve: this is the one thing that must not regress."""
        provider = RecordingProvider(answer="should never be used")
        mailbox = _CountingMailbox(sample_inbox())
        agent = EmailAgent(mailbox, brain=LLMSpecialistBrain(provider))
        result = agent.execute(Task(description="read my inbox"))
        assert result.status is TaskStatus.COMPLETED
        assert mailbox.fetch_calls == 1
        assert "should never be used" not in result.output

    def test_job_analysis_still_routes_to_its_own_analyzer_unaffected(self):
        provider = RecordingProvider(answer="should never be used")
        agent = EmailAgent(sample_inbox(), brain=LLMSpecialistBrain(provider))
        result = agent.execute(Task(description="find job opportunities in my inbox"))
        assert result.status is TaskStatus.COMPLETED
        assert "job_opportunity" in result.output

    def test_thanks_still_answers_without_a_brain(self):
        """Preserve: conversational_reply's own identity-independent
        shape (a bare "thanks" needs no name to answer) is unaffected."""
        agent = EmailAgent(sample_inbox())
        result = agent.execute(Task(description="thanks"))
        assert result.status is TaskStatus.COMPLETED
        assert result.output == "You're welcome!"


class TestRealProductionRoomParityForArthur:
    """The exact requirement this stage names explicitly: natural
    conversation must work for a specialist, through the real production
    path, not merely for the orchestrator."""

    def _wired(self, answer):
        runtime, manager, channel, conversation = real_system()
        provider = RecordingProvider(answer=answer)
        arthur = manager.registry.get("Arthur")
        arthur.brain = LLMSpecialistBrain(provider, personality=arthur.personality)
        return runtime, manager, channel, conversation, provider, arthur

    def test_arthur_genuinely_discusses_napoleon_through_exchange(self):
        _, manager, channel, conversation, _, _ = self._wired(
            "Napoleon rose to power after the French Revolution."
        )
        turn = exchange(channel, conversation, "tell me about Napoleon", addressed="Arthur")
        assert turn.handled_by == "Arthur"
        assert turn.outcome == "Napoleon rose to power after the French Revolution."

    def test_arthurs_own_personality_reaches_the_system_prompt_used(self):
        runtime, manager, channel, conversation, provider, arthur = self._wired("dry joke back")
        exchange(channel, conversation, "tell me a joke", addressed="Arthur")
        system_used = provider.calls[0][0]
        for trait in arthur.personality.traits:
            assert trait in system_used

    def test_alfred_and_arthur_use_independent_brains(self):
        """Section 15: an agent's own reasoning stays its own - swapping
        Arthur's brain must not touch Alfred's answers or vice versa."""
        runtime, manager, channel, conversation, arthur_provider, _ = self._wired("arthur's answer")

        alfred_provider = RecordingProvider(answer="alfred's answer")
        from src.manager.llm_brain import LLMManagerBrain

        manager.brain = LLMManagerBrain(alfred_provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        alfred_turn = exchange(channel, conversation, "tell me about Napoleon")
        arthur_turn = exchange(channel, conversation, "tell me a joke", addressed="Arthur")

        assert alfred_turn.outcome == "alfred's answer"
        assert arthur_turn.outcome == "arthur's answer"

    def test_the_conversation_room_route_reaches_the_same_arthur(self):
        """Section 7: the exact same production path, through
        scripts.serve_ui.route_message, must answer identically for a
        specialist too - never a second, simplified generator."""
        from scripts.serve_ui import build_live_system, route_message

        settings = Settings.from_env(env={}, memory="ephemeral")
        system = build_live_system(settings)
        provider = RecordingProvider(answer="Napoleon, via the Conversation Room.")
        arthur = system.manager.registry.get("Arthur")
        arthur.brain = LLMSpecialistBrain(provider, personality=arthur.personality)

        status, body, _ = route_message("tell me about Napoleon", system, to="Arthur")
        assert status == 200
        import json

        payload = json.loads(body)
        assert payload["handled_by"] == "Arthur"
        assert payload["outcome"] == "Napoleon, via the Conversation Room."
