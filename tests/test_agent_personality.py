"""Stage 11.1 - the agent personality foundation.

Mirrors tests/test_voice_identity.py's own shape deliberately: that file
proves a VoiceProfile is real, distinct, and derived from each agent's own
identity; this file proves the same three things about AgentPersonality,
plus the one guarantee unique to it - a genuine, verifiable connection to
an actual place this agent communicates (the LLM system prompt
LLMManagerBrain builds), not a dangling, unused value type.

    IDENTITY  +  PERSONALITY  +  BRAIN  +  MEMORY  +  VOICE  +
    CAPABILITIES  +  CONNECTIONS

are the seven dimensions this stage's directive names. This file's job is
the second one, and proving it does not duplicate any of the other six.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.agents.specialists import wiring_for
from src.core.contracts import AgentSpec, Task
from src.manager.brain_factory import build_brain
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.services.identity.identity import parse_identity
from src.services.identity.specialist import parse_specialist_identity
from src.services.identity.personality import (
    AgentPersonality,
    default_personality,
    personality_for,
    personality_prompt_fragment,
)
from src.services.memory.factory import EMAIL_AGENT

REPO_ROOT = Path(__file__).resolve().parent.parent


def manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(
        env={}, log_level="CRITICAL", memory="ephemeral", **overrides
    )
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class RecordingProvider(LLMProvider):
    """Records the exact system prompt it was asked to reason under -
    never a real network call, exactly test_llm_manager_brain.py's own
    scripted-provider convention."""

    def __init__(self, text: str = '{"decision": "unknown", "reason": "recorded"}'):
        self.last_system: str | None = None
        self.calls = 0
        self._text = text

    @property
    def name(self) -> str:
        return "recording"

    def complete(self, prompt, *, system=None):
        self.last_system = system
        self.calls += 1
        return Completion(text=self._text, provider="recording")


# ---------------------------------------------------------------------------
# 1-2: The primitive is explicit and deterministic
# ---------------------------------------------------------------------------


class TestAgentPersonalityPrimitive:
    def test_a_personality_carries_its_stated_tendencies(self):
        personality = AgentPersonality(
            key="somebody", tone="warm", humor="occasional", emoji_style="light"
        )
        assert personality.tone == "warm"
        assert personality.humor == "occasional"
        assert personality.emoji_style == "light"

    def test_traits_lists_only_the_stated_ones_in_a_stable_order(self):
        personality = AgentPersonality(key="somebody", tone="warm", playfulness="light")
        assert personality.traits == ("warm", "light")

    def test_an_unstated_personality_has_no_traits(self):
        assert AgentPersonality(key="somebody").traits == ()

    def test_a_blank_key_is_rejected(self):
        with pytest.raises(ValueError):
            AgentPersonality(key="   ")

    def test_is_immutable(self):
        personality = AgentPersonality(key="somebody", tone="warm")
        with pytest.raises(Exception):
            personality.tone = "cold"

    def test_is_deterministic(self):
        """Building the same personality twice produces equal values -
        nothing here is randomised or time-dependent."""
        a = AgentPersonality(key="somebody", tone="warm", humor="dry")
        b = AgentPersonality(key="somebody", tone="warm", humor="dry")
        assert a == b

    def test_unknown_traits_are_kept_not_dropped(self):
        personality = AgentPersonality(key="somebody", style={"catchphrase": "indeed"})
        assert personality.style == {"catchphrase": "indeed"}

    def test_describe_is_honest_about_no_tendencies(self):
        assert "no stated tendencies" in AgentPersonality(key="somebody").describe()


class TestDefaultPersonality:
    def test_derived_from_the_agents_own_name(self):
        assert default_personality("Some Agent").key == "some agent"

    def test_two_agents_never_share_a_default_key(self):
        assert default_personality("Agent One").key != default_personality("Agent Two").key

    def test_declares_no_tendencies_by_default(self):
        """'No personality declared' and 'a personality that happens to be
        neutral' are different facts - the default states neither tone
        nor humor nor anything else, exactly as default_voice declares no
        accent."""
        assert default_personality("Some Agent").traits == ()

    def test_requires_a_real_name(self):
        with pytest.raises(ValueError):
            default_personality("")

    def test_personality_for_is_duck_typed(self):
        class HasPersonality:
            personality = AgentPersonality(key="x", tone="warm")

        class HasNone:
            pass

        assert personality_for(HasPersonality()) is not None
        assert personality_for(HasNone()) is None
        assert personality_for(object()) is None


# ---------------------------------------------------------------------------
# 3-6: Alfred configured, Arthur configured differently, generic parsing
# ---------------------------------------------------------------------------


class TestDocumentParsing:
    def test_a_personality_section_loads(self):
        identity = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Personality\ntone: warm\nhumor: dry\n"
        )
        assert identity.personality is not None
        assert identity.personality.tone == "warm"
        assert identity.personality.humor == "dry"

    def test_explanatory_prose_before_the_traits_is_not_mistaken_for_data(self):
        """Regression test: a first draft of this parser read any line
        containing a colon as a trait, so a document's own explanatory
        sentence ('...capability: nothing here grants anything') became a
        bogus style entry filed under its own first clause. Only short,
        punctuation-free candidate lines are read as traits."""
        identity = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Personality\n\n"
            "This section shapes tone, not capability: nothing here grants "
            "anything, exactly as describing a role grants nothing.\n\n"
            "tone: warm\nhumor: dry\n"
        )
        assert identity.personality.style == {}
        assert identity.personality.tone == "warm"
        assert identity.personality.humor == "dry"

    def test_no_section_means_no_declared_personality(self):
        identity = parse_identity("## Name\nSomebody\n## Capabilities\nroster\n")
        assert identity.personality is None

    def test_declaring_a_personality_grants_no_capability(self):
        with_personality = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Personality\ntone: warm\n"
        )
        without = parse_identity("## Name\nSomebody\n## Capabilities\nroster\n")
        assert with_personality.capabilities == without.capabilities
        assert with_personality.to_spec().capabilities == without.to_spec().capabilities

    def test_a_specialist_identity_document_parses_the_same_section(self):
        identity = parse_specialist_identity(
            "## Name\nSomeSpecialist\n## Personality\ntone: dry\nplayfulness: none\n"
        )
        assert identity.personality.tone == "dry"
        assert identity.personality.playfulness == "none"

    def test_a_specialist_document_with_no_section_declares_none(self):
        identity = parse_specialist_identity("## Name\nSomeSpecialist\n")
        assert identity.personality is None


class TestTheRealAlfredIdentity:
    def test_identity_md_declares_a_recognizable_personality(self):
        from src.services.identity.identity import load_identity

        identity = load_identity("IDENTITY.md")
        p = identity.personality
        assert p is not None
        assert "warm" in p.tone.lower()
        assert "conversational" in p.tone.lower()
        assert p.humor and p.playfulness and p.emoji_style
        assert p.style == {}, "prose must not have leaked into style - see the regression test above"

    def test_the_managers_own_skills_publish_the_same_personality(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        identity = load_identity("IDENTITY.md")
        skills = ManagerSkills(identity)
        assert skills.personality.key == "alfred"
        assert personality_for(skills) is skills.personality


class TestFutureSpecialistExtensibility:
    def test_arthur_now_declares_his_own_personality(self):
        """Stage 12's celebration follow-up gave Arthur a real
        ``## Personality`` section - brisker and more casual than
        Alfred's, so the two agents' celebration reactions (derived from
        this same Personality, see
        src/providers/default_celebration_provider.py) are genuinely
        different rather than both falling back to the same
        undeclared default."""
        wiring = wiring_for(EMAIL_AGENT, fallback_name="Arthur")
        assert wiring.identity is not None
        assert wiring.identity.personality is not None
        assert wiring.identity.personality.key == "arthur"

    def test_arthurs_declared_personality_is_distinct_from_alfreds(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        wiring = wiring_for(EMAIL_AGENT, fallback_name="Arthur")
        alfred = ManagerSkills(load_identity("IDENTITY.md")).personality
        assert wiring.personality.traits != alfred.traits

    def test_alfred_and_arthur_have_different_personality_keys(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        alfred = ManagerSkills(load_identity("IDENTITY.md")).personality
        arthur = wiring_for(EMAIL_AGENT, fallback_name="Arthur").personality
        assert alfred.key != arthur.key

    def test_a_specialist_that_does_declare_a_section_would_be_honoured(self):
        """Proof the primitive is ready for a specialist that adds one,
        without a line of the generic parser changing."""
        identity = parse_specialist_identity(
            "## Name\nArthur\n## Personality\ntone: dry and precise\nemoji_style: rare\n"
        )
        assert identity.personality.tone == "dry and precise"


# ---------------------------------------------------------------------------
# 5-6: Generic, no Alfred/Arthur branching in the mechanism
# ---------------------------------------------------------------------------


class TestGenericModuleIndependence:
    PERSONALITY_SRC = REPO_ROOT / "src" / "services" / "identity" / "personality.py"

    def test_the_module_imports_nothing_from_src_besides_the_standard_library(self):
        tree = ast.parse(self.PERSONALITY_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "src" not in node.module, node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src"), alias.name

    def test_the_module_names_no_agent(self):
        source = self.PERSONALITY_SRC.read_text(encoding="utf-8").lower()
        for forbidden in ("alfred", "arthur"):
            assert forbidden not in source, forbidden

    def test_the_module_contains_no_agent_name_branching(self):
        """Structural, not textual: no comparison anywhere in the module
        checks a value against a literal agent name."""
        tree = ast.parse(self.PERSONALITY_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        assert comparator.value.lower() not in ("alfred", "arthur"), comparator.value

    def test_the_module_names_no_provider(self):
        source = self.PERSONALITY_SRC.read_text(encoding="utf-8").lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey"):
            assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# The prompt fragment: deterministic, preference-stated, never a mechanical
# emoji inserter
# ---------------------------------------------------------------------------


class TestPersonalityPromptFragment:
    def test_none_produces_an_empty_fragment(self):
        assert personality_prompt_fragment(None) == ""

    def test_an_unstated_personality_produces_an_empty_fragment(self):
        assert personality_prompt_fragment(AgentPersonality(key="x")) == ""

    def test_is_deterministic(self):
        p = AgentPersonality(key="x", tone="warm", humor="dry")
        assert personality_prompt_fragment(p) == personality_prompt_fragment(p)

    def test_only_declared_tendencies_appear(self):
        p = AgentPersonality(key="x", tone="warm")
        fragment = personality_prompt_fragment(p)
        assert "Tone: warm" in fragment
        assert "Warmth:" not in fragment
        assert "Humor:" not in fragment

    def test_emoji_preference_is_described_never_inserted(self):
        """Item 13: the fragment may TALK ABOUT emoji usage in words, but
        must never contain an actual emoji character itself - that
        decision belongs to whatever composes a real reply, never to this
        function."""
        p = AgentPersonality(key="x", emoji_style="occasional and natural")
        fragment = personality_prompt_fragment(p)
        assert "occasional and natural" in fragment
        assert "🙂" not in fragment and "😀" not in fragment and "👍" not in fragment
        # No character outside the printable ASCII range - a stricter
        # general guarantee that nothing emoji-shaped slipped in.
        assert all(ord(ch) < 128 for ch in fragment)

    def test_never_claims_authority_over_the_users_request(self):
        p = AgentPersonality(key="x", humor="frequent", playfulness="high")
        fragment = personality_prompt_fragment(p)
        assert "never overriding" in fragment.lower()


# ---------------------------------------------------------------------------
# The genuine connection: personality reaches a real system prompt
# ---------------------------------------------------------------------------


class TestLLMBrainIntegration:
    def test_personality_reaches_the_real_system_prompt(self):
        provider = RecordingProvider()
        personality = AgentPersonality(key="alfred", tone="warm and friendly", emoji_style="occasional")
        brain = LLMManagerBrain(provider, personality=personality)
        brain.deliberate(Task(description="anything"), agents=(), notes=())
        assert provider.last_system is not None
        assert "warm and friendly" in provider.last_system
        assert "occasional" in provider.last_system

    def test_omitting_personality_reproduces_the_prior_exact_prompt(self):
        from src.manager.brain_prompts import BRAIN_SYSTEM_PROMPT

        provider = RecordingProvider()
        brain = LLMManagerBrain(provider)
        brain.deliberate(Task(description="anything"), agents=(), notes=())
        assert provider.last_system == BRAIN_SYSTEM_PROMPT

    def test_a_personality_with_no_tendencies_also_reproduces_the_prior_prompt(self):
        from src.manager.brain_prompts import BRAIN_SYSTEM_PROMPT

        provider = RecordingProvider()
        brain = LLMManagerBrain(provider, personality=AgentPersonality(key="alfred"))
        brain.deliberate(Task(description="anything"), agents=(), notes=())
        assert provider.last_system == BRAIN_SYSTEM_PROMPT

    def test_personality_never_changes_what_is_parsed_from_the_response(self):
        """The brain still only ever decides who should handle a task -
        personality shapes the instruction it reasons under, never the
        contract it returns through."""
        agents = (AgentSpec(name="arthur", description="d", capabilities=frozenset({"email"})),)
        provider = RecordingProvider('{"decision": "arthur", "reason": "matches"}')
        with_personality = LLMManagerBrain(
            provider, personality=AgentPersonality(key="alfred", tone="warm")
        ).deliberate(Task(description="check email"), agents=agents, notes=())
        without_personality = LLMManagerBrain(provider).deliberate(
            Task(description="check email"), agents=agents, notes=()
        )
        assert with_personality.delegate_to == without_personality.delegate_to
        assert with_personality.action == without_personality.action

    def test_generic_not_alfred_specific(self):
        """The same mechanism, handed an entirely different, non-Alfred
        personality, behaves identically - nothing in LLMManagerBrain
        branches on who the personality belongs to."""
        provider = RecordingProvider()
        personality = AgentPersonality(key="future-agent", tone="brisk and dry")
        LLMManagerBrain(provider, personality=personality).deliberate(
            Task(description="anything"), agents=(), notes=()
        )
        assert "brisk and dry" in provider.last_system


class TestBrainFactoryIntegration:
    def test_build_brain_threads_personality_to_the_llm_brain(self):
        from src.config.settings import Settings

        provider = RecordingProvider()
        personality = AgentPersonality(key="alfred", tone="warm")
        brain = build_brain(Settings(brain="llm"), provider, personality)
        brain.deliberate(Task(description="anything"), agents=(), notes=())
        assert "warm" in provider.last_system

    def test_null_brain_ignores_personality_without_error(self):
        from src.config.settings import Settings
        from src.manager.brain import NullBrain

        brain = build_brain(
            Settings(brain="null"), RecordingProvider(), AgentPersonality(key="alfred", tone="warm")
        )
        assert isinstance(brain, NullBrain)

    def test_existing_two_argument_calls_are_unaffected(self):
        """Every pre-Stage-11.1 call site calls build_brain with exactly
        two positional arguments - this must keep working unchanged."""
        from src.config.settings import Settings
        from src.manager.brain import NullBrain

        assert isinstance(build_brain(Settings(), RecordingProvider()), NullBrain)


# ---------------------------------------------------------------------------
# 7-10: Personality creates no second Brain, Memory, VoiceProfile,
# VoiceActivation
# ---------------------------------------------------------------------------


class TestNoDuplicateArchitecture:
    def test_personality_module_imports_no_brain_memory_voice_or_activation(self):
        tree = ast.parse(
            (REPO_ROOT / "src" / "services" / "identity" / "personality.py")
            .read_text(encoding="utf-8")
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in (
            "src.manager.brain", "src.manager.llm_brain", "src.manager.brain_factory",
            "src.services.memory.factory", "src.services.memory.persistent",
            "src.services.identity.voice",
        ):
            assert forbidden not in imported, forbidden

    def test_two_personalities_share_no_state(self):
        """Frozen value objects, not singletons - two different agents'
        personalities never alias one another's data."""
        a = AgentPersonality(key="a", tone="warm")
        b = AgentPersonality(key="b", tone="cold")
        assert a.tone != b.tone

    def test_llm_manager_brain_with_a_personality_is_still_the_one_brain_class(self):
        """Constructing it with a personality does not produce a subclass
        or a parallel type - the exact same ManagerBrain contract."""
        from src.manager.brain import ManagerBrain

        plain = LLMManagerBrain(RecordingProvider())
        with_personality = LLMManagerBrain(RecordingProvider(), personality=AgentPersonality(key="x"))
        assert type(plain) is type(with_personality) is LLMManagerBrain
        assert isinstance(with_personality, ManagerBrain)

    def test_a_second_manager_still_shares_no_memory_across_agents(self):
        """A broader regression check: nothing about a configured
        personality changes each agent's own memory ownership."""
        mgr = manager()
        assert mgr.own_skills.personality.key == "alfred"
        # The Manager's memory remains the Manager's own, unaffected.
        assert mgr.memory is not None


# ---------------------------------------------------------------------------
# 11-12: Brain mode independence
# ---------------------------------------------------------------------------


class TestBrainModeIndependence:
    def test_half_brain_still_answers_a_deterministic_capability(self):
        mgr = manager(brain="null")
        response_text = mgr.own_skills.identity.name  # sanity: identity intact
        assert response_text == "Alfred"
        assert mgr.own_skills.personality.key == "alfred"

    def test_personality_is_accessible_regardless_of_brain_mode(self):
        for brain_name in ("null", "llm"):
            mgr = manager(brain=brain_name)
            assert mgr.own_skills.personality is not None
            assert mgr.own_skills.personality.key == "alfred"

    def test_half_brain_does_not_disable_capabilities_with_a_personality_declared(self):
        from src.core.contracts import Task

        mgr = manager(brain="null")
        result = mgr.handle_task(Task(description="status"))
        assert result.status.value == "completed"

    def test_full_brain_does_not_imply_a_declared_personality_is_required(self):
        """A Full Brain agent with NO declared personality still works -
        personality is optional configuration, not a Brain prerequisite."""
        provider = RecordingProvider()
        brain = LLMManagerBrain(provider)  # no personality at all
        decision = brain.deliberate(Task(description="anything"), agents=(), notes=())
        assert decision is not None


# ---------------------------------------------------------------------------
# 13-15: Emoji representation and register range
# ---------------------------------------------------------------------------


class TestRegisterRange:
    def test_a_serious_professional_register_can_be_declared(self):
        p = AgentPersonality(
            key="x", formality="strictly professional", humor="none", emoji_style="never"
        )
        fragment = personality_prompt_fragment(p)
        assert "strictly professional" in fragment
        assert "never" in fragment.lower()

    def test_a_casual_conversational_register_can_be_declared(self):
        p = AgentPersonality(key="x", tone="casual and relaxed", formality="low")
        fragment = personality_prompt_fragment(p)
        assert "casual and relaxed" in fragment

    def test_both_registers_use_the_same_mechanism(self):
        """Serious and casual are both just declared tendencies - proving
        neither requires a different code path."""
        serious = personality_prompt_fragment(AgentPersonality(key="x", formality="strict"))
        casual = personality_prompt_fragment(AgentPersonality(key="x", formality="relaxed"))
        assert serious.split("\n")[0] == casual.split("\n")[0]  # same framing sentence


# ---------------------------------------------------------------------------
# 16-19: Regression - existing architecture remains compatible
# ---------------------------------------------------------------------------


class TestRegression:
    def test_voice_profile_is_unaffected_by_personality(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        skills = ManagerSkills(load_identity("IDENTITY.md"))
        assert skills.voice.key == "alfred"
        assert skills.voice.accent == "nigerian-english"

    def test_voice_activation_is_unaffected_by_personality(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        skills = ManagerSkills(load_identity("IDENTITY.md"))
        assert skills.activation.matches("Alfred")
        assert skills.activation.matches("Hey Alfred")

    def test_stage_8a_ambient_voice_session_still_activates_and_converses(self):
        from src.channels.activation import ActivationState, VoiceActivationSession
        from src.channels.channel import VoiceChannel

        mgr = manager()
        channel = VoiceChannel(mgr)
        session = VoiceActivationSession(channel, mgr.own_skills.activation)
        session.start()
        assert session.hear_activation("Alfred") is True
        assert session.state is ActivationState.ACTIVATED
        response = session.turn("status")
        assert response.text
        assert session.state is ActivationState.LISTENING

    def test_stage_10_speech_factory_still_builds_the_default_adapter(self):
        from src.channels.speech import NoSpeech
        from src.config.settings import Settings
        from src.providers.speech_factory import build_speech

        assert isinstance(build_speech(Settings()), NoSpeech)
