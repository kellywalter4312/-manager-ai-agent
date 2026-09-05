"""Every agent sounds like itself.

The requirement, stated so it can be tested: in a conversation with several
agents, a person should be able to tell who is speaking without looking at the
screen. That makes a voice part of an agent's *identity* - alongside its name,
its brain domain and its memory store - and not a setting on the machinery
producing audio.

    AGENT IDENTITY  ->  VoiceProfile  ->  Speech interface  ->  a provider

Two groups of tests. One proves voices are real and distinct and derived from
each agent's own identity. The other proves what this layer must *not* do:
name a vendor, name an agent, let one agent inherit another's voice, or let a
voice reach a speaker without going through the Manager and the Conversation.

**No audio is produced anywhere in this file.** No speech engine is installed,
and the double here records which voice it was asked for rather than speaking.
That is the whole point: the architectural claim is provable without pretending
a sound was made.
"""

import ast
from pathlib import Path

import pytest

from src.agents.specialists import wiring_for
from src.services.identity.identity import parse_identity
from src.services.identity.specialist import parse_specialist_identity
from src.services.identity.voice import (
    VoiceProfile,
    default_voice,
    parse_voice,
    voice_for,
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


# ---------------------------------------------------------------------------
# A voice belongs to an identity
# ---------------------------------------------------------------------------


class TestAVoiceComesFromTheAgentsOwnIdentity:
    def test_the_manager_document_declares_a_voice_and_it_loads(self):
        """Loaded, not decorative. A `## Voice` section that parsed into
        nothing would be the heading-that-behaves-like-a-comment defect this
        project has already fixed twice."""
        identity = parse_identity(
            (REPO_ROOT / "IDENTITY.md").read_text(encoding="utf-8")
        )
        assert identity.voice is not None
        assert identity.voice.traits

    def test_the_specialist_document_declares_its_own(self):
        identity = parse_specialist_identity(
            (REPO_ROOT / "identities" / "email_agent.md").read_text(encoding="utf-8")
        )
        assert identity.voice is not None
        assert identity.voice.traits

    def test_the_two_shipped_agents_do_not_sound_alike(self):
        """The requirement in one assertion."""
        alfred = parse_identity(
            (REPO_ROOT / "IDENTITY.md").read_text(encoding="utf-8")
        ).voice
        arthur = parse_specialist_identity(
            (REPO_ROOT / "identities" / "email_agent.md").read_text(encoding="utf-8")
        ).voice

        assert alfred.key != arthur.key
        assert alfred.traits != arthur.traits

    def test_a_voice_is_the_fourth_thing_one_identity_decides(self):
        """Name, memory owner and brain domain already come from one identity
        so they cannot disagree. A voice configured elsewhere could drift from
        the agent it belongs to.

        Asserted on the *traits*, not the key. A mutation check found that
        asserting the key alone proved nothing here: the fallback derives its
        key from the agent's name, so "Arthur" produces the key "arthur"
        whether the document was read or ignored. Only the characteristics can
        have come from the document.
        """
        wiring = wiring_for(EMAIL_AGENT, fallback_name=EMAIL_AGENT)
        assert wiring.name == "Arthur"
        assert wiring.voice.key == "arthur"
        assert wiring.voice.traits, "the declared characteristics must be loaded"
        assert wiring.voice != default_voice(wiring.name)

    def test_a_specialist_that_declares_nothing_still_sounds_like_itself(self):
        """Derived from its own name, never a shared default object - two
        agents that both declared nothing must not be given one voice."""
        first = default_voice("Some Specialist")
        second = default_voice("Another Specialist")
        assert first.key != second.key

    def test_a_document_may_describe_a_trait_this_version_does_not_model(self):
        """Carried into style rather than dropped. Silently discarding it would
        make the document a place where writing has no effect."""
        profile = parse_voice(["cadence: lilting"], key="somebody")
        assert profile.style["cadence"] == "lilting"

    def test_a_document_that_says_nothing_about_voice_declares_none(self):
        assert parse_voice([], key="somebody") is None


# ---------------------------------------------------------------------------
# The agents themselves publish it
# ---------------------------------------------------------------------------


class TestAgentsPublishTheirOwnVoice:
    def test_each_registered_agent_has_a_voice(self):
        alfred = manager()
        for agent in alfred.registry.all_agents():
            assert voice_for(agent) is not None, agent.name

    def test_no_two_registered_agents_share_a_voice_key(self):
        """The scaling claim. Adding a specialist must not risk a collision,
        because the fallback is keyed on the agent's own name."""
        alfred = manager()
        keys = [voice_for(agent).key for agent in alfred.registry.all_agents()]
        assert len(keys) == len(set(keys))

    def test_an_agent_cannot_inherit_another_agents_voice(self):
        alfred = manager()
        manager_voice = voice_for(alfred.registry.get("Alfred"))
        specialist_voice = voice_for(alfred.registry.get("Arthur"))
        assert manager_voice.key != specialist_voice.key

    def test_voice_lookup_is_duck_typed_not_a_registry_of_names(self):
        """An object with no voice returns None rather than being handed
        somebody else's."""
        assert voice_for(object()) is None


# ---------------------------------------------------------------------------
# Speaking asks for the right voice, without any engine
# ---------------------------------------------------------------------------


class TestSpeakingRequestsTheSpeakersOwnVoice:
    def _channel(self, alfred):
        from src.channels.channel import VoiceChannel
        from tests.test_channels_events_improvement import FakeSpeech

        speech = FakeSpeech()
        return VoiceChannel(alfred, speech), speech

    def test_an_answer_is_spoken_in_the_voice_of_whoever_answered(self):
        alfred = manager()
        channel, speech = self._channel(alfred)

        delivered = channel.deliver(channel.ask("roster"))

        assert delivered.turn.handled_by == "Alfred"
        assert speech.voices[0] is not None
        assert speech.voices[0].key == "alfred"

    def test_a_delegated_answer_is_spoken_in_the_specialists_voice(self):
        """The decisive one. Two agents, two voices, chosen by who answered -
        and no engine involved."""
        from src.services.email.sample_inbox import sample_inbox
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.profile.loader import parse_profile

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        alfred = build_manager(
            runtime,
            mailbox=sample_inbox(),
            profile=parse_profile("## Skills\n### Strong\npython\n"),
        )
        channel, speech = self._channel(alfred)

        delivered = channel.deliver(channel.ask("scan my inbox for opportunities"))

        assert delivered.turn.handled_by == "Arthur"
        assert speech.voices[0].key == "arthur"

    def test_the_channel_looks_a_voice_up_rather_than_choosing_one(self):
        """A channel that picked a voice would be assigning identity from the
        presentation layer. It holds no mapping and names no agent."""
        source = (REPO_ROOT / "src" / "channels" / "channel.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr):
                    if isinstance(body[0].value, ast.Constant):
                        docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                for name in ("alfred", "arthur"):
                    assert name not in node.value.lower(), node.value[:40]

    def test_speech_still_reports_honestly_when_there_is_no_engine(self):
        """A voice being requested must not make a mute system look like a
        talking one."""
        from src.channels.channel import VoiceChannel
        from src.channels.speech import NoSpeech

        alfred = manager()
        channel = VoiceChannel(alfred, NoSpeech())

        delivered = channel.deliver(channel.ask("roster"))

        assert delivered.spoken is False
        assert delivered.channel_note == "written only"

    def test_the_shipped_adapter_accepts_a_voice_and_says_nothing_in_it(self):
        from src.channels.speech import NoSpeech

        assert NoSpeech().speak("hello", VoiceProfile(key="anyone")) is False


# ---------------------------------------------------------------------------
# What this layer must never become
# ---------------------------------------------------------------------------


class TestVoiceIdentityNamesNoVendorAndNoAgent:
    def test_a_profile_has_nowhere_to_put_a_vendor(self):
        """Absent, not ignored. A document that could name an endpoint would be
        choosing where a request goes."""
        profile = VoiceProfile(key="somebody")
        for forbidden in ("provider", "vendor", "model", "endpoint", "api_key", "url"):
            assert not hasattr(profile, forbidden), forbidden

    def test_the_voice_module_names_no_agent(self):
        source = (REPO_ROOT / "src" / "services" / "identity" / "voice.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr):
                    if isinstance(body[0].value, ast.Constant):
                        docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                for name in ("alfred", "arthur", "jarvis"):
                    assert name not in node.value.lower(), node.value[:40]

    def test_the_voice_module_names_no_speech_vendor(self):
        source = (
            (REPO_ROOT / "src" / "services" / "identity" / "voice.py")
            .read_text(encoding="utf-8")
            .lower()
        )
        for vendor in ("elevenlabs", "azure", "polly", "openai", "google", "piper"):
            assert vendor not in source, vendor

    def test_the_voice_module_reaches_no_engine_brain_or_memory(self):
        source = (REPO_ROOT / "src" / "services" / "identity" / "voice.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.startswith("__") or "src" not in node.module, (
                    node.module
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src"), alias.name

    def test_declaring_a_voice_grants_nothing(self):
        """Describes, never grants - the same rule identity documents already
        live under. A voice changes what an agent sounds like and nothing about
        what it may do."""
        with_voice = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice\nkey: somebody\naccent: whatever\n"
        )
        without = parse_identity("## Name\nSomebody\n## Capabilities\nroster\n")
        assert with_voice.capabilities == without.capabilities
        assert with_voice.to_spec().capabilities == without.to_spec().capabilities


# ---------------------------------------------------------------------------
# Hearing and speaking are separate responsibilities
# ---------------------------------------------------------------------------


class TestListeningAndSpeakingAreSeparableConcerns:
    def test_a_listener_and_a_speaker_are_separate_contracts(self):
        """So a local recogniser and a hosted synthesiser can be different
        products, each reporting its own availability."""
        from src.channels.speech import Listener, Speaker

        assert "listen" in Listener.__abstractmethods__
        assert "speak" not in Listener.__abstractmethods__
        assert "speak" in Speaker.__abstractmethods__
        assert "listen" not in Speaker.__abstractmethods__

    def test_each_half_answers_availability_for_itself(self):
        from src.channels.speech import Listener, Speaker

        for half in (Listener, Speaker):
            assert "availability" in half.__abstractmethods__
            assert "available" not in half.__abstractmethods__

    def test_the_shipped_adapter_is_still_both(self):
        """Splitting the contract must not have broken the one implementation
        that exists."""
        from src.channels.speech import Listener, NoSpeech, Speaker

        speech = NoSpeech()
        assert isinstance(speech, Listener)
        assert isinstance(speech, Speaker)

    def test_a_speaker_can_be_implemented_without_a_microphone(self):
        """The point of the split: text-to-speech with no listening at all."""
        from src.channels.speech import Speaker
        from src.providers.availability import Availability

        class SpeakOnly(Speaker):
            def __init__(self):
                self.asked_for = []

            @property
            def name(self):
                return "speak-only"

            @property
            def availability(self):
                return Availability()

            def speak(self, text, voice=None):
                self.asked_for.append(voice)
                return False

        engine = SpeakOnly()
        assert engine.speak("hello", VoiceProfile(key="somebody")) is False
        assert engine.asked_for[0].key == "somebody"
        assert engine.available is False  # UNKNOWN is not usable
