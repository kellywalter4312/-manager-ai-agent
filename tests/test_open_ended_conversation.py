"""Stage 11 audit fix - Alfred's open-ended conversation/reasoning path.

Closes the single MUST-FIX finding from the Stage 11 architecture audit:
a genuinely open-ended question or remark ("teach me how a car works",
"bro Messi just retired") previously matched no declared capability word
and fell to a roster listing (or no agent at all), regardless of whether
a real reasoning Brain was configured. These tests prove the fix reaches
the Brain honestly - a real answer when one is available, a plain refusal
to fabricate one when it is not - without a single subject-specific word
anywhere in the mechanism.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.contracts import Task, TaskStatus
from src.manager.brain import BrainAction, BrainDecision, NullBrain
from src.manager.capabilities import (
    GENERAL_ASSISTANCE,
    KNOWLEDGE,
    MANAGER_CAPABILITIES,
    OPEN_CONVERSATION,
    PARTICIPANT_INVITATION,
    ROSTER_REPORT,
    select_capability,
)
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.registry.agent_registry import AgentRegistry
from src.services.identity.identity import load_identity

REPO_ROOT = Path(__file__).resolve().parent.parent

OPEN_ENDED_EXAMPLES = [
    "Teach me how a car engine works.",
    "Explain the trading market to me.",
    "Explain physics to me.",
    "Bro Messi just retired",
    "What happened between Iran and the USA?",
    "Help me understand this.",
]


class ScriptedProvider:
    """A minimal, real LLMProvider double - never a network call.

    Deliberately not tests/conftest.py's own ScriptedProvider: that one is
    shared session-wide fixture infrastructure; this file constructs its
    own small double so each test can inspect exactly what prompt/system
    text it received, which is the thing several tests below need to
    assert on directly.
    """

    def __init__(self, text: str, *, fail: bool = False):
        self._text = text
        self._fail = fail
        self.prompts: list[str] = []
        self.systems: list[str | None] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        if self._fail:
            raise RuntimeError("provider exploded")
        self.prompts.append(prompt)
        self.systems.append(system)
        return Completion(
            text=self._text, provider=self.name, model="scripted-1",
            input_tokens=len(prompt), output_tokens=len(self._text),
        )


def manager_with_brain(brain, *, identity_path="IDENTITY.md"):
    """A real Manager, hand-wired for precise control over which Brain
    reaches ManagerSkills - the same construction test_manager_capabilities
    .py's own by-hand tests already use, extended with converse=."""
    identity = load_identity(identity_path)
    registry = AgentRegistry()
    manager = ManagerAgent(registry=registry, brain=brain)
    manager.own_skills = ManagerSkills(
        identity, roster=registry.all_agents, converse=brain.converse
    )
    registry.register(manager.own_skills)
    return manager


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# 1 & 4 - a substantive open-ended question reaches the Brain and gets a
# real, Brain-generated answer when a usable Brain is available
# ---------------------------------------------------------------------------


class TestAUsableBrainComposesARealAnswer:
    def test_the_provider_is_actually_called(self):
        provider = ScriptedProvider("Good question - here is how it works...")
        brain = LLMManagerBrain(provider)
        manager = manager_with_brain(brain)

        manager.handle_task(Task(description="Teach me how a car engine works."))

        assert len(provider.prompts) == 1
        assert "car engine" in provider.prompts[0].lower()

    def test_the_composed_text_reaches_the_user(self):
        answer = "An engine converts fuel into motion through controlled combustion."
        brain = LLMManagerBrain(ScriptedProvider(answer))
        manager = manager_with_brain(brain)

        result = manager.handle_task(Task(description="Teach me how a car engine works."))

        assert result.succeeded
        assert result.output == answer

    def test_a_short_emotional_remark_also_reaches_the_brain(self):
        answer = "That's a huge moment - he shaped a whole era of the game."
        brain = LLMManagerBrain(ScriptedProvider(answer))
        manager = manager_with_brain(brain)

        result = manager.handle_task(Task(description="Bro Messi just retired"))

        assert result.succeeded
        assert result.output == answer

    @pytest.mark.parametrize("text", OPEN_ENDED_EXAMPLES)
    def test_every_open_ended_example_reaches_a_real_answer(self, text):
        answer = "Here's a genuine answer to that."
        brain = LLMManagerBrain(ScriptedProvider(answer))
        manager = manager_with_brain(brain)

        result = manager.handle_task(Task(description=text))

        assert result.succeeded
        assert result.output == answer
        assert result.agent_name == "Alfred"


# ---------------------------------------------------------------------------
# 2 & 3 - these specific examples do not fall into roster_report
# ---------------------------------------------------------------------------


class TestOpenEndedExamplesDoNotBecomeARosterListing:
    def test_teach_me_a_car_does_not_select_roster_report(self):
        chosen = select_capability(
            Task(description="Teach me how a car engine works."), MANAGER_CAPABILITIES
        )
        assert chosen is OPEN_CONVERSATION
        assert chosen is not ROSTER_REPORT

    def test_messi_retired_does_not_select_roster_report(self):
        chosen = select_capability(
            Task(description="Bro Messi just retired"), MANAGER_CAPABILITIES
        )
        assert chosen is OPEN_CONVERSATION
        assert chosen is not ROSTER_REPORT

    def test_teach_me_a_car_reaches_alfred_not_no_agent(self):
        """The outer routing layer, not just the inner handler-selection
        one: previously this scored nothing anywhere and the task never
        reached Alfred at all."""
        manager = real_manager()
        result = manager.handle_task(Task(description="Teach me how a car engine works."))
        assert result.status is not TaskStatus.NO_AGENT_FOUND
        assert result.agent_name == "Alfred"

    def test_messi_retired_reaches_alfred_not_no_agent(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="Bro Messi just retired"))
        assert result.status is not TaskStatus.NO_AGENT_FOUND
        assert result.agent_name == "Alfred"

    def test_the_response_never_contains_the_roster_listing_shape(self):
        """A stronger, behavioural check than 'not ROSTER_REPORT': the
        actual text a person would see must not look like a status
        report either, with or without a configured Brain."""
        for brain in (NullBrain(), LLMManagerBrain(ScriptedProvider("A real answer."))):
            manager = manager_with_brain(brain)
            result = manager.handle_task(Task(description="Bro Messi just retired"))
            assert "specialists" not in (result.output or "").lower()
            assert "handles directly" not in (result.output or "").lower()


# ---------------------------------------------------------------------------
# 5 - no usable Brain: an honest fallback, never a fabricated answer
# ---------------------------------------------------------------------------


class TestNoUsableBrainRefusesRatherThanFabricates:
    def test_null_brain_gives_an_honest_failure(self):
        manager = manager_with_brain(NullBrain())
        result = manager.handle_task(Task(description="Explain physics to me."))
        assert result.status is TaskStatus.FAILED
        assert "reason about" in result.error
        assert "physics" not in result.error.lower()  # no fabricated physics content

    def test_a_manager_with_no_converse_wired_at_all_also_refuses_honestly(self):
        """The pre-this-stage shape: a ManagerSkills built without the new
        converse= parameter at all must behave exactly as before -
        honestly, never by crashing."""
        from src.services.identity.identity import AgentIdentity

        identity = AgentIdentity(name="Alfred", capabilities=frozenset({"roster"}))
        skills = ManagerSkills(identity)  # no converse= passed
        result = skills.execute(Task(description="Explain physics to me."))
        assert not result.succeeded
        assert "reason about" in result.error

    def test_a_failing_provider_also_refuses_honestly_not_a_crash(self):
        brain = LLMManagerBrain(ScriptedProvider("", fail=True))
        manager = manager_with_brain(brain)
        result = manager.handle_task(Task(description="Explain physics to me."))
        assert result.status is TaskStatus.FAILED
        assert "reason about" in result.error

    def test_an_empty_completion_also_refuses_honestly(self):
        brain = LLMManagerBrain(ScriptedProvider(""))
        manager = manager_with_brain(brain)
        result = manager.handle_task(Task(description="Explain physics to me."))
        assert result.status is TaskStatus.FAILED


# ---------------------------------------------------------------------------
# 6 & 7 - existing deterministic capabilities and specialist routing
# ---------------------------------------------------------------------------


class TestExistingBehaviorIsUnchanged:
    def test_status_still_reports_the_roster(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="status"))
        assert result.succeeded
        assert "handles directly" in result.output

    def test_thanks_still_gets_the_small_talk_reply(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="thanks"))
        assert result.succeeded
        assert result.output == "You're welcome!"

    def test_research_still_reaches_general_assistance(self):
        chosen = select_capability(Task(description="research this topic"), MANAGER_CAPABILITIES)
        assert chosen is GENERAL_ASSISTANCE

    def test_invitations_still_work(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="invite Arthur"))
        assert result.succeeded
        assert (result.payload or {}).get("invited") == "Arthur"

    def test_arthur_still_handles_email(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"

    def test_a_capability_word_still_wins_over_the_open_ended_fallback(self):
        """Existing, more specific capabilities are still tried first -
        open_conversation only ever wins when nothing else scored."""
        chosen = select_capability(Task(description="status"), MANAGER_CAPABILITIES)
        assert chosen is ROSTER_REPORT

    def test_tell_me_about_x_now_reaches_the_knowledge_capability(self):
        """The one behaviour Stage 18 deliberately moved, and why.

        This test previously pinned a nuance its own docstring called
        pre-existing: 'about' is a research-signal word GENERAL_CAPABILITIES
        declared, so 'tell me about X' was claimed by general_assistance and
        refused as "the 'web_search' capability is not installed". That
        refusal was honest but wrong about *what was missing*: nothing about
        a settled question requires looking at the present moment, so naming
        a live search tool as the obstacle described a gap that was not the
        gap. Stage 18 gives that question shape its own capability, which
        reasons over the user's own documents, their learning context and
        the Brain's own knowledge.

        What has NOT changed is the honesty. With no Brain and no knowledge
        source - this project's own default - the answer is still a refusal
        that invents nothing about the subject, which is what the assertions
        below actually check. 'research this topic' still reaches
        general_assistance, unchanged, one test above."""
        chosen = select_capability(
            Task(description="Tell me about the trading market."), MANAGER_CAPABILITIES
        )
        assert chosen is KNOWLEDGE
        assert chosen is not GENERAL_ASSISTANCE

        manager = real_manager()
        result = manager.handle_task(Task(description="Tell me about the trading market."))
        assert result.status is TaskStatus.FAILED
        # Honest about both missing halves, and inventing neither.
        assert "no language model" in result.error
        assert "knowledge_lookup" in result.error
        for invented in ("stock", "bond", "equities", "bull", "bear"):
            assert invented not in result.error.lower()


# ---------------------------------------------------------------------------
# 8 - Brain remains per-agent / canonical
# ---------------------------------------------------------------------------


class TestBrainRemainsCanonicalAndPerAgent:
    def test_manageragent_and_manager_skills_share_the_one_brain_instance(self):
        """No second Brain is created to wire this up - the same object
        reaches both collaborators."""
        brain = LLMManagerBrain(ScriptedProvider("answer"))
        manager = manager_with_brain(brain)
        assert manager.brain is brain

        manager.handle_task(Task(description="Teach me how a car works."))
        # own_skills' converse is a *bound method* of the exact same brain -
        # provable by observing the call landed on this brain's own provider.
        assert manager.own_skills._converse.__self__ is brain

    def test_alfreds_brain_is_not_arthurs(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        assert manager.brain is not arthur.brain

    def test_a_real_runtime_never_constructs_a_second_manager_brain(self):
        from src.composition import build_runtime
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        # runtime.manager_brain is the one instance; composition.py must
        # thread this exact object into both ManagerAgent and ManagerSkills,
        # never construct a fresh brain for either.
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        assert source.count("build_brain(") == 1


# ---------------------------------------------------------------------------
# 9 - VoiceProfile remains unchanged and independent
# ---------------------------------------------------------------------------


class TestVoiceProfileIsUnaffected:
    def test_alfreds_voice_is_unchanged_before_and_after_an_open_ended_turn(self):
        manager = real_manager()
        before = manager.own_skills.voice

        brain = LLMManagerBrain(ScriptedProvider("answer"))
        conversational_manager = manager_with_brain(brain)
        conversational_manager.handle_task(Task(description="Teach me how a car works."))

        after = manager.own_skills.voice
        assert before == after
        assert after.style.get("elevenlabs_voice_id") == "iP95p4xoKVk53GoZ742B"

    def test_voiceprofile_type_is_untouched_by_this_stage(self):
        import dataclasses

        from src.services.identity.voice import VoiceProfile

        names = {f.name for f in dataclasses.fields(VoiceProfile)}
        assert names == {"key", "accent", "pace", "pitch", "warmth", "preferred_voice", "style"}


# ---------------------------------------------------------------------------
# 10 - the LLM provider remains replaceable
# ---------------------------------------------------------------------------


class TestLlmProviderRemainsReplaceable:
    def test_swapping_the_brain_changes_only_whether_an_answer_is_composed(self):
        """The capability layer does not care which ManagerBrain
        implementation it was given - only whether converse() returns a
        decided view."""
        null_manager = manager_with_brain(NullBrain())
        null_result = null_manager.handle_task(Task(description="Explain physics to me."))
        assert null_result.status is TaskStatus.FAILED

        llm_manager = manager_with_brain(LLMManagerBrain(ScriptedProvider("A real explanation.")))
        llm_result = llm_manager.handle_task(Task(description="Explain physics to me."))
        assert llm_result.status is TaskStatus.COMPLETED
        assert llm_result.output == "A real explanation."

    def test_llmmanagerbrain_never_imports_a_vendor_sdk(self):
        source = (REPO_ROOT / "src" / "manager" / "llm_brain.py").read_text(encoding="utf-8")
        for vendor in ("anthropic", "openai", "elevenlabs"):
            assert vendor not in source.lower()


# ---------------------------------------------------------------------------
# 11 - no subject-specific keyword capability was introduced
# ---------------------------------------------------------------------------


class TestNoSubjectSpecificCapabilityWasIntroduced:
    def test_open_conversations_own_words_are_generic_not_subjects(self):
        forbidden_subjects = {
            "car", "cars", "messi", "physics", "trading", "iran", "usa",
            "football", "engine", "market",
        }
        assert OPEN_CONVERSATION.capabilities.isdisjoint(forbidden_subjects)

    def test_exactly_one_new_capability_was_registered(self):
        """As of this stage's own scope: open_conversation. Stage 12 later
        adds forget/recall_memory to this same registry - see
        test_manager_capabilities.py's own exact-list assertion for the
        current, complete total."""
        assert "open_conversation" in MANAGER_CAPABILITIES
        assert {
            "general_assistance", "open_conversation", "participant_invitation",
            "roster_report", "small_talk",
        } <= set(MANAGER_CAPABILITIES)

    def test_no_subject_word_appears_anywhere_in_the_capabilities_module(self):
        source = (REPO_ROOT / "src" / "manager" / "capabilities.py").read_text(encoding="utf-8")
        for subject in ("messi", "iran", "trading", "physics"):
            assert subject not in source.lower()

    def test_select_capability_reaches_open_conversation_by_elimination_not_a_word_list(self):
        """Structural: OPEN_CONVERSATION is never named as a literal string
        comparison target inside select_capability's own body - it is
        reached purely because scoring found nothing, for any input."""
        import inspect

        source = inspect.getsource(select_capability)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for side in (node.left, *node.comparators):
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        assert side.value.lower() not in (
                            "car", "messi", "physics", "trading", "iran", "usa",
                        )
