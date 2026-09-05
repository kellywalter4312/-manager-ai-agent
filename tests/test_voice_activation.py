"""Stage 8A — how the system recognizes that a person is addressing an
agent by voice, before a single word of the request has been heard.

Mirrors tests/test_voice_identity.py's own shape deliberately: that file
proves a VoiceProfile is real, distinct, and derived from each agent's own
identity; this file proves the same three things about VoiceActivation,
plus the one guarantee unique to it - that changing one never changes the
other. No audio and no microphone anywhere in this file; a heard utterance
here is always a plain string, because that is exactly what a real speech
engine would have already turned it into by the time anything in this
project ever sees it (see src/channels/speech.py).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.agents.specialists import wiring_for
from src.services.identity.identity import parse_identity
from src.services.identity.specialist import parse_specialist_identity
from src.services.identity.voice import (
    VoiceActivation,
    activation_for,
    default_activation,
    parse_voice,
)
from src.services.memory.factory import EMAIL_AGENT

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The primitive itself
# ---------------------------------------------------------------------------


class TestVoiceActivationPrimitive:
    def test_an_exact_configured_phrase_matches(self):
        activation = VoiceActivation(key="an agent", phrases=["An Agent"])
        assert activation.matches("An Agent") is True

    def test_matching_is_case_and_whitespace_insensitive(self):
        activation = VoiceActivation(key="an agent", phrases=["Hey An Agent"])
        assert activation.matches("  hey   AN agent  ") is True

    def test_one_trailing_punctuation_mark_is_ignored(self):
        activation = VoiceActivation(key="an agent", phrases=["An Agent"])
        assert activation.matches("An Agent.") is True
        assert activation.matches("An Agent?") is True
        assert activation.matches("An Agent!") is True

    def test_unrelated_speech_does_not_match(self):
        activation = VoiceActivation(key="an agent", phrases=["An Agent", "Hey An Agent"])
        assert activation.matches("what's the weather like") is False

    def test_a_phrase_embedded_in_a_longer_sentence_does_not_match(self):
        """An exact, honest match - not a substring search. A sentence that
        merely mentions the agent's name is not somebody addressing it."""
        activation = VoiceActivation(key="an agent", phrases=["An Agent"])
        assert activation.matches("tell An Agent I said hello") is False

    def test_an_agent_with_no_configured_phrases_matches_nothing(self):
        activation = VoiceActivation(key="an agent", phrases=())
        assert activation.matches("an agent") is False
        assert activation.matches("") is False

    def test_duplicate_phrases_are_collapsed(self):
        activation = VoiceActivation(key="a", phrases=["Hi", "hi", "  HI  "])
        assert activation.phrases == ("hi",)

    def test_a_blank_key_is_rejected(self):
        with pytest.raises(ValueError):
            VoiceActivation(key="   ")

    def test_describe_names_the_phrases(self):
        activation = VoiceActivation(key="a", phrases=["Hi", "Hello"])
        assert "hi" in activation.describe()
        assert "hello" in activation.describe()

    def test_describe_is_honest_about_no_phrases(self):
        assert "no activation phrase" in VoiceActivation(key="a").describe()


class TestDefaultActivation:
    def test_derived_from_the_agents_own_name(self):
        activation = default_activation("Some Agent")
        assert activation.key == "some agent"
        assert activation.matches("Some Agent")
        assert activation.matches("Hey Some Agent")

    def test_two_agents_never_share_a_default(self):
        first = default_activation("Agent One")
        second = default_activation("Agent Two")
        assert first.phrases != second.phrases
        assert not first.matches("Agent Two")
        assert not second.matches("Agent One")

    def test_requires_a_real_name(self):
        with pytest.raises(ValueError):
            default_activation("")

    def test_activation_for_is_duck_typed(self):
        class HasActivation:
            activation = VoiceActivation(key="x", phrases=["x"])

        class HasNone:
            pass

        assert activation_for(HasActivation()) is not None
        assert activation_for(HasNone()) is None
        assert activation_for(object()) is None


# ---------------------------------------------------------------------------
# Configurable, from a document - not a hardcoded list in the engine
# ---------------------------------------------------------------------------


class TestDocumentParsing:
    def test_a_voice_activation_section_loads(self):
        identity = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice Activation\n- Somebody\n- Hey Somebody\n"
        )
        assert identity.activation is not None
        assert identity.activation.phrases == ("somebody", "hey somebody")

    def test_a_third_phrase_can_be_added_with_no_engine_change(self):
        """The extensibility the whole stage exists for: a document can add
        a phrase the code below has never seen named."""
        identity = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice Activation\n- Somebody\n- Hey Somebody\n- Okay Somebody\n"
        )
        assert identity.activation.matches("Okay Somebody") is True

    def test_explanatory_prose_before_the_list_is_not_mistaken_for_a_phrase(self):
        """Regression test: a first draft of the parser read every line of
        the section as a phrase, so a document's own explanation of what
        the section means became three bogus activation phrases. Only
        bulleted lines are phrases."""
        identity = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice Activation\n\n"
            "This section explains what activation means, in prose, before\n"
            "the actual list of phrases begins below.\n\n"
            "- Somebody\n- Hey Somebody\n"
        )
        assert identity.activation.phrases == ("somebody", "hey somebody")

    def test_no_section_means_no_declared_activation(self):
        identity = parse_identity("## Name\nSomebody\n## Capabilities\nroster\n")
        assert identity.activation is None

    def test_declaring_activation_grants_no_capability(self):
        with_activation = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice Activation\n- Somebody\n"
        )
        without = parse_identity("## Name\nSomebody\n## Capabilities\nroster\n")
        assert with_activation.capabilities == without.capabilities
        assert with_activation.to_spec().capabilities == without.to_spec().capabilities

    def test_a_specialist_identity_document_parses_the_same_section(self):
        identity = parse_specialist_identity(
            "## Name\nSomeSpecialist\n"
            "## Voice Activation\n- Some Specialist\n- Hey Some Specialist\n"
        )
        assert identity.activation.phrases == ("some specialist", "hey some specialist")

    def test_a_specialist_document_with_no_section_declares_none(self):
        identity = parse_specialist_identity("## Name\nSomeSpecialist\n")
        assert identity.activation is None


# ---------------------------------------------------------------------------
# VoiceProfile and VoiceActivation are separate responsibilities
# ---------------------------------------------------------------------------


class TestActivationIsSeparateFromVoiceProfile:
    def test_changing_the_voice_section_does_not_change_activation(self):
        before = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice\nkey: somebody\naccent: plain\n"
            "## Voice Activation\n- Somebody\n"
        )
        after = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice\nkey: somebody\naccent: completely different\n"
            "## Voice Activation\n- Somebody\n"
        )
        assert before.activation.phrases == after.activation.phrases

    def test_changing_activation_phrases_does_not_change_the_voice(self):
        before = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice\nkey: somebody\naccent: plain\n"
            "## Voice Activation\n- Somebody\n"
        )
        after = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice\nkey: somebody\naccent: plain\n"
            "## Voice Activation\n- Somebody\n- Hey Somebody\n- Okay Somebody\n"
        )
        assert before.voice == after.voice

    def test_the_two_types_share_no_fields_beyond_the_common_key(self):
        """Both are keyed identity concepts, so ``key`` is expected on both -
        everything else must be exclusive to one or the other, or the two
        responsibilities have started to blur into one type."""
        from src.services.identity.voice import VoiceProfile

        profile_only = set(VoiceProfile.__dataclass_fields__) - {"key"}
        activation_only = set(VoiceActivation.__dataclass_fields__) - {"key"}
        assert profile_only.isdisjoint(activation_only)
        assert "phrases" not in VoiceProfile.__dataclass_fields__
        assert "accent" not in VoiceActivation.__dataclass_fields__


# ---------------------------------------------------------------------------
# The real, shipped Alfred identity
# ---------------------------------------------------------------------------


class TestTheRealAlfredIdentity:
    def test_identity_md_declares_alfred_and_hey_alfred(self):
        from src.services.identity.identity import load_identity

        identity = load_identity("IDENTITY.md")
        assert identity.activation is not None
        assert identity.activation.phrases == ("alfred", "hey alfred")

    def test_the_managers_own_skills_publish_the_same_activation(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        identity = load_identity("IDENTITY.md")
        skills = ManagerSkills(identity)
        assert skills.activation.matches("Alfred")
        assert skills.activation.matches("Hey Alfred")
        assert not skills.activation.matches("Arthur")
        assert activation_for(skills) is skills.activation


# ---------------------------------------------------------------------------
# Future specialists - Arthur is the first example, not wired this stage
# ---------------------------------------------------------------------------


class TestFutureSpecialistExtensibility:
    def test_arthur_has_no_declared_voice_activation_section_yet(self):
        """True today, and the point: Arthur's actual activation phrase is
        explicitly out of scope for this stage - see IDENTITY.md's sibling
        directive. What matters is that the primitive already works for
        him without anything here having been written for him specifically."""
        wiring = wiring_for(EMAIL_AGENT, fallback_name="Arthur")
        assert wiring.identity is not None
        assert wiring.identity.activation is None

    def test_arthur_still_gets_a_distinct_default_activation(self):
        wiring = wiring_for(EMAIL_AGENT, fallback_name="Arthur")
        assert wiring.activation.key == "arthur"
        assert wiring.activation.matches("Arthur")
        assert wiring.activation.matches("Hey Arthur")

    def test_arthur_and_alfred_have_different_activation_identities(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        alfred = ManagerSkills(load_identity("IDENTITY.md")).activation
        arthur = wiring_for(EMAIL_AGENT, fallback_name="Arthur").activation
        assert alfred.key != arthur.key
        assert not alfred.matches("Arthur")
        assert not arthur.matches("Alfred")

    def test_a_specialist_that_does_declare_a_section_would_be_honoured(self):
        """Proof the primitive is ready for a specialist that adds one,
        without a line of the generic parser changing - a synthetic
        document is enough to show it; nothing here writes Arthur's."""
        identity = parse_specialist_identity(
            "## Name\nArthur\n## Voice Activation\n- Arthur\n- Hey Arthur\n- Okay Arthur\n"
        )
        assert identity.activation.matches("Okay Arthur")


# ---------------------------------------------------------------------------
# The generic primitive names no agent and no provider
# ---------------------------------------------------------------------------


class TestGenericModuleIndependence:
    def test_voice_module_imports_nothing_from_src_besides_the_standard_library(self):
        source = (REPO_ROOT / "src" / "services" / "identity" / "voice.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "src" not in node.module, node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src"), alias.name

    def test_the_module_names_no_agent(self):
        source = (REPO_ROOT / "src" / "services" / "identity" / "voice.py").read_text(
            encoding="utf-8"
        ).lower()
        for forbidden in ("alfred", "arthur"):
            assert forbidden not in source, forbidden

    def test_the_module_names_no_provider(self):
        source = (REPO_ROOT / "src" / "services" / "identity" / "voice.py").read_text(
            encoding="utf-8"
        ).lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey"):
            assert forbidden not in source, forbidden

    def test_parse_voice_still_returns_a_voice_profile_unaffected(self):
        """This stage touched the module VoiceProfile already lived in;
        confirms the pre-existing parser was not disturbed."""
        profile = parse_voice(["key: somebody", "accent: plain"], key="somebody")
        assert profile is not None
        assert profile.key == "somebody"
