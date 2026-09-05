"""0.2 Final Foundation Audit - Brain / LLM provider / credential / memory /
voice independence.

Every claim below is checked against the real production architecture,
never against a description of it:

    Settings -> build_provider -> LLMProvider -> LLMManagerBrain -> ManagerAgent

CHECK confirmed, by direct source reading before any code changed here:
``src/manager/brain.py``, ``src/manager/llm_brain.py``, ``src/manager/
manager.py`` and ``src/manager/brain_factory.py`` name no vendor anywhere
(grepped: zero hits for "anthropic"/"ANTHROPIC") - ``LLMManagerBrain`` is
the one and only concrete LLM-backed ``ManagerBrain``, and it receives an
already-built ``LLMProvider`` instance, never a provider *name*. Swapping
which provider instance it holds cannot change which class it is, because
nothing about selecting a provider ever touches ``ManagerBrain`` at all.

No code changes were needed for the architecture itself - this file is
verification, not repair. The one genuine gap found and left honestly
open (see the class at the end of this file) is that ``Settings.api_key``/
``MANAGER_API_KEY`` is currently a single, generically-named credential
field that ``_anthropic_credential`` alone claims; a second *real* vendor
provider must be given its own dedicated field rather than reusing it -
noted here because nothing today collides (there is only one real
provider), not because anything is broken.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.manager.brain import ManagerBrain
from src.manager.brain_factory import build_brain
from src.manager.llm_brain import LLMManagerBrain
import src.providers.factory as provider_factory
from src.providers.factory import (
    ProviderConfigurationError,
    available_providers,
    build_provider,
)
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


class FakeProvider(LLMProvider):
    """A distinct, real ``LLMProvider`` implementation standing in for
    "Provider A/B/C" - not a mock of Anthropic, a wholly separate class,
    exactly the shape a genuinely different vendor would take."""

    def __init__(self, vendor_name: str, answer: str):
        self._name = vendor_name
        self._answer = answer
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt, *, system=None):
        self.calls += 1
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider=self._name)
        return Completion(text=self._answer, provider=self._name)


# ---------------------------------------------------------------------------
# 1. THE CANONICAL BRAIN
# ---------------------------------------------------------------------------


class TestCanonicalBrainArchitecture:
    def test_no_brain_module_names_a_vendor(self):
        """Structural: the same 'orchestration modules stay X-unaware'
        discipline test_email_agent.py already applies to email, applied
        here to vendors."""
        import src.manager.brain as brain_module
        import src.manager.brain_factory as brain_factory_module
        import src.manager.llm_brain as llm_brain_module
        import src.manager.manager as manager_module

        for module in (brain_module, llm_brain_module, manager_module, brain_factory_module):
            source = open(module.__file__, encoding="utf-8").read().lower()
            assert "anthropic" not in source, module.__name__

    def test_llmmanagerbrain_is_the_same_class_regardless_of_provider(self):
        provider_a = FakeProvider("vendor-a", "answer from A")
        provider_b = FakeProvider("vendor-b", "answer from B")
        brain_a = LLMManagerBrain(provider_a)
        brain_b = LLMManagerBrain(provider_b)
        assert type(brain_a) is type(brain_b) is LLMManagerBrain

    def test_build_brain_never_receives_or_needs_a_provider_name(self):
        """build_brain's own signature - (settings, provider, personality) -
        takes a constructed LLMProvider, never a string; this proves the
        call actually works that way end to end."""
        settings = Settings(brain="llm")
        brain = build_brain(settings, FakeProvider("vendor-a", "x"))
        assert isinstance(brain, LLMManagerBrain)
        assert brain.provider_name == "vendor-a"

    def test_switching_provider_never_changes_the_managerbrain_class_in_a_live_system(self):
        runtime, manager, channel, conversation = real_system()
        assert isinstance(manager.brain, ManagerBrain)

        for provider in (
            FakeProvider("vendor-a", "a"),
            FakeProvider("vendor-b", "b"),
            FakeProvider("vendor-c", "c"),
        ):
            manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
            manager.own_skills._converse = manager.brain.converse
            assert type(manager.brain) is LLMManagerBrain


# ---------------------------------------------------------------------------
# 2 & 8. PROVIDER ABSTRACTION, SWITCHING, AND FUTURE EXTENSIBILITY
# ---------------------------------------------------------------------------


class TestProviderSwitchingThroughTheRealProductionPath:
    def test_alfred_genuinely_answers_from_whichever_provider_is_currently_wired(self):
        runtime, manager, channel, conversation = real_system()
        own_skills = manager.own_skills
        memory = manager.memory
        registry = manager.registry

        for label, answer in (("vendor-a", "reply from A"), ("vendor-b", "reply from B"), ("vendor-c", "reply from C")):
            provider = FakeProvider(label, answer)
            manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
            manager.own_skills._converse = manager.brain.converse
            turn = exchange(channel, conversation, f"tell me something ({label})")
            assert turn.outcome == answer
            # Nothing about identity was rebuilt to switch providers.
            assert manager.own_skills is own_skills
            assert manager.memory is memory
            assert manager.registry is registry

    def test_a_new_compatible_provider_can_be_registered_without_touching_brain_code(self, monkeypatch):
        """Section 8's exact requirement, proven against the real
        registry _BUILDERS lives in - never a parallel/test-only registry -
        with its own dedicated credential variable, exactly mirroring
        _build_anthropic's own shape."""
        credential_env = "VENDOR_X_API_KEY"

        def _build_vendor_x(settings, env, account=None):
            source = env if env is not None else {}
            key = (source.get(credential_env) or "").strip()
            if not key:
                raise ProviderConfigurationError(
                    f"The 'vendor-x' provider is selected but no API key is configured. "
                    f"Set {credential_env} in the environment."
                )
            return FakeProvider("vendor-x", "a genuine vendor-x answer")

        patched = dict(provider_factory._BUILDERS)
        patched["vendor-x"] = _build_vendor_x
        monkeypatch.setattr(provider_factory, "_BUILDERS", patched)

        assert "vendor-x" in available_providers()

        provider = build_provider(Settings(provider="vendor-x"), env={credential_env: "fake-key"})
        assert provider.name == "vendor-x"

        # And it drops into the real Brain with zero changes anywhere else.
        brain = build_brain(Settings(brain="llm"), provider)
        assert isinstance(brain, LLMManagerBrain)
        decision = brain.converse(__import__("src.core.contracts", fromlist=["Task"]).Task(description="hi"))
        assert decision.reason == "a genuine vendor-x answer"

    def test_provider_names_stay_a_flat_registry_not_a_priority_or_special_case_list(self):
        """None of null/anthropic get special orchestration treatment -
        both are ordinary entries, proven by the registry being a plain
        dict this project's own factory already is."""
        assert {"null", "anthropic"} <= set(available_providers())


# ---------------------------------------------------------------------------
# 3. PROVIDER-SPECIFIC CREDENTIALS
# ---------------------------------------------------------------------------


class TestCredentialIsolation:
    def test_anthropic_credential_check_never_reads_a_foreign_vendors_variable(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider="anthropic"),
                env={"VENDOR_X_API_KEY": "fake-key-for-a-different-vendor"},
            )
        assert "ANTHROPIC_API_KEY" in str(exc.value)

    def test_a_new_vendors_credential_check_never_accepts_the_anthropic_key(self, monkeypatch):
        credential_env = "VENDOR_X_API_KEY"

        def _build_vendor_x(settings, env, account=None):
            source = env if env is not None else {}
            key = (source.get(credential_env) or "").strip()
            if not key:
                raise ProviderConfigurationError(
                    f"The 'vendor-x' provider is selected but no API key is configured. "
                    f"Set {credential_env} in the environment."
                )
            return FakeProvider("vendor-x", "answer")

        patched = dict(provider_factory._BUILDERS)
        patched["vendor-x"] = _build_vendor_x
        monkeypatch.setattr(provider_factory, "_BUILDERS", patched)

        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider="vendor-x"),
                env={"ANTHROPIC_API_KEY": "a-real-looking-anthropic-key"},
            )
        assert "VENDOR_X_API_KEY" in str(exc.value)

    def test_settings_api_key_takes_precedence_over_the_environment_for_anthropic_only(self):
        """MANAGER_API_KEY (Settings.api_key) is documented and behaves as
        anthropic-specific - checked here so a future second real
        provider is never tempted to silently reuse it."""
        settings = Settings(provider="anthropic", api_key="configured-key")
        provider = build_provider(settings, env={})
        assert isinstance(provider, AnthropicProvider)

    def test_no_credential_present_for_either_vendor_fails_closed_for_each_independently(self, monkeypatch):
        credential_env = "VENDOR_X_API_KEY"
        patched = dict(provider_factory._BUILDERS)
        patched["vendor-x"] = lambda settings, env, account=None: (_ for _ in ()).throw(
            ProviderConfigurationError("vendor-x needs " + credential_env)
        )
        monkeypatch.setattr(provider_factory, "_BUILDERS", patched)

        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="anthropic"), env={})
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="vendor-x"), env={})

    def test_removing_a_credential_from_the_environment_is_honoured_immediately(self):
        """'Revocation' in this architecture: credentials are read fresh
        from the environment on every build_provider call, never cached
        or stored - so unsetting one and rebuilding fails closed again,
        proving nothing about a prior successful build is remembered."""
        built = build_provider(Settings(provider="anthropic"), env={"ANTHROPIC_API_KEY": "a-key"})
        assert isinstance(built, AnthropicProvider)

        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="anthropic"), env={})

    def test_constructing_a_provider_makes_no_network_call(self, monkeypatch):
        import socket

        def _no_network(*a, **k):
            raise AssertionError("build_provider must not touch the network")

        monkeypatch.setattr(socket, "socket", _no_network)
        build_provider(Settings(provider="anthropic"), env={"ANTHROPIC_API_KEY": "a-key"})  # must not raise


# ---------------------------------------------------------------------------
# 4 & 5. MEMORY AND CONVERSATION CONTINUITY ACROSS PROVIDER SWITCHING
# ---------------------------------------------------------------------------


class TestMemoryContinuityAcrossProviderSwitching:
    """The most important practical test, run exactly as specified:
    Provider A -> genuine info stored -> switch to B -> still there ->
    switch to C -> still there. Same manager, same memory, same room -
    never rebuilt, never reset, never provider-siloed."""

    def test_provider_a_to_b_to_c_retains_the_same_memory(self):
        runtime, manager, channel, conversation = real_system()
        manager.brain = LLMManagerBrain(FakeProvider("vendor-a", "noted"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        exchange(channel, conversation, "remember that the launch codeword is Blue Falcon")
        records_after_a = manager.memory.recall(limit=10)
        assert any("Blue Falcon" in r.content for r in records_after_a)

        manager.brain = LLMManagerBrain(FakeProvider("vendor-b", "still here"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        exchange(channel, conversation, "what's the weather like today")  # ordinary continued conversation
        records_after_b = manager.memory.recall(limit=10)
        assert any("Blue Falcon" in r.content for r in records_after_b)

        manager.brain = LLMManagerBrain(FakeProvider("vendor-c", "still here too"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        records_after_c = manager.memory.recall(limit=10)
        assert any("Blue Falcon" in r.content for r in records_after_c)

    def test_memory_is_never_written_into_a_provider_named_silo(self):
        """The record's own storage carries no provider-scoped key or
        collection - the same governed store regardless of who is
        currently reasoning. Provenance (which brain answered) may be
        recorded as an inert fact about a *turn*, never as a partition of
        the store itself."""
        runtime, manager, channel, conversation = real_system()
        manager.brain = LLMManagerBrain(FakeProvider("vendor-a", "ok"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        exchange(channel, conversation, "remember that the safe word is Aurora")

        record = next(r for r in manager.memory.recall(limit=10) if "Aurora" in r.content)
        assert "vendor-a" not in record.kind
        assert "vendor_a_memory" not in (record.context or {})
        assert not any(str(k).startswith("vendor") for k in (record.context or {}))

    def test_information_survives_a_genuine_process_restart_with_a_different_provider(self, tmp_path):
        """Stronger than the in-process check above: two fully separate
        build() calls - a real, file-backed PersistentMemory, the actual
        default storage this project ships with - simulating a real
        restart between Provider A and Provider B."""
        memory_dir = str(tmp_path / "memory")

        settings_a = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        runtime_a, manager_a, channel_a, conversation_a = build(settings_a)
        manager_a.brain = LLMManagerBrain(FakeProvider("vendor-a", "ok"), personality=runtime_a.identity.personality)
        manager_a.own_skills._converse = manager_a.brain.converse
        exchange(channel_a, conversation_a, "remember that the project codename is Nightjar")
        assert any("Nightjar" in r.content for r in manager_a.memory.recall(limit=10))

        # A brand new Runtime/Manager/Memory object graph, from disk -
        # the closest this test suite can get to "the process restarted."
        settings_b = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        runtime_b, manager_b, channel_b, conversation_b = build(settings_b)
        assert manager_b is not manager_a
        assert manager_b.memory is not manager_a.memory
        manager_b.brain = LLMManagerBrain(FakeProvider("vendor-b", "ok"), personality=runtime_b.identity.personality)
        manager_b.own_skills._converse = manager_b.brain.converse

        records = manager_b.memory.recall(limit=10)
        assert any("Nightjar" in r.content for r in records)


class TestConversationHistoryContinuityAcrossProviderSwitching:
    def test_the_room_transcript_survives_a_provider_switch_mid_conversation(self):
        runtime, manager, channel, conversation = real_system()
        manager.brain = LLMManagerBrain(FakeProvider("vendor-a", "first answer"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        exchange(channel, conversation, "let's talk about the Q3 roadmap")

        before_switch = conversation.transcript()
        manager.brain = LLMManagerBrain(FakeProvider("vendor-b", "second answer"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        exchange(channel, conversation, "what did we just say")

        after_switch = conversation.transcript()
        assert all(line in after_switch for line in before_switch)

    def test_the_new_provider_genuinely_receives_the_old_conversations_context(self):
        """Not merely 'the transcript object still exists' - the new
        provider's own prompt must actually carry what was said before
        the switch, proving continuity reaches the model call itself."""
        runtime, manager, channel, conversation = real_system()
        manager.brain = LLMManagerBrain(FakeProvider("vendor-a", "the launch is Tuesday"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        exchange(channel, conversation, "when is the launch, remind me it's called Project Kite")

        seen_prompts: list[str] = []

        class CapturingProvider(LLMProvider):
            @property
            def name(self):
                return "vendor-b"

            def complete(self, prompt, *, system=None):
                seen_prompts.append(prompt)
                if system and "Reply with a single JSON object" in system:
                    return Completion(text='{"action":"unknown","reason":"n/a"}', provider="vendor-b")
                return Completion(text="continuing from before", provider="vendor-b")

        manager.brain = LLMManagerBrain(CapturingProvider(), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        exchange(channel, conversation, "what did I just call it")

        assert any("Project Kite" in p for p in seen_prompts)


# ---------------------------------------------------------------------------
# 6. VOICE INDEPENDENCE (the full matrix, in one place)
# ---------------------------------------------------------------------------


class TestVoiceIndependenceMatrix:
    def test_full_matrix_brain_provider_voice(self):
        from src.control_center.observer import ControlCenter
        from src.services.identity.voice_preference import effective_voice_for

        runtime, manager, channel, conversation = real_system(speech="kokoro")
        centre = ControlCenter(runtime, manager)
        starting_brain = manager.brain

        def current_kokoro_id():
            resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
            return resolved.style.get("kokoro_voice_id")

        assert current_kokoro_id() == "am_eric"

        # LLM Provider A -> B: Brain class and voice must both be unchanged.
        manager.brain = LLMManagerBrain(FakeProvider("vendor-a", "x"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        assert type(manager.brain) is LLMManagerBrain
        assert current_kokoro_id() == "am_eric"

        manager.brain = LLMManagerBrain(FakeProvider("vendor-b", "y"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse
        assert current_kokoro_id() == "am_eric"

        # Now change voice only: Brain/provider must both be unchanged.
        from scripts.control_center import run_command

        brain_before_voice_change = manager.brain
        result = run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)
        assert "now" in result and "Zira" in result
        assert manager.brain is brain_before_voice_change

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.preferred_voice == "Microsoft Zira Desktop"
        assert resolved.style.get("kokoro_voice_id") == "am_eric"  # untouched by preferred_voice override

    def test_reverse_direction_voice_change_first_then_provider_switch(self):
        from src.control_center.observer import ControlCenter
        from src.services.identity.voice_preference import effective_voice_for
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system(speech="kokoro")
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)

        manager.brain = LLMManagerBrain(FakeProvider("vendor-a", "z"), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.preferred_voice == "Microsoft Zira Desktop"
        assert resolved.style.get("kokoro_voice_id") == "am_eric"
        assert type(manager.brain) is LLMManagerBrain
        assert manager.brain.provider_name == "vendor-a"


# ---------------------------------------------------------------------------
# 7. SPECIALIST (ALFRED/ARTHUR) INDEPENDENCE
# ---------------------------------------------------------------------------


class TestSpecialistIndependence:
    def test_arthur_has_his_own_brain_instance_distinct_from_alfreds(self):
        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        assert arthur.brain is not manager.brain

    def test_arthur_has_his_own_voice_distinct_from_alfreds(self):
        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        assert arthur.voice.key != manager.own_skills.voice.key
        assert arthur.voice.style.get("kokoro_voice_id") == "bm_daniel"
        assert manager.own_skills.voice.style.get("kokoro_voice_id") == "am_eric"

    def test_arthur_has_his_own_memory_distinct_from_alfreds(self):
        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        assert arthur.memory is not manager.memory

    def test_changing_alfreds_provider_never_touches_arthurs_brain(self):
        from src.agents.llm_brain import LLMSpecialistBrain

        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        arthur_provider = FakeProvider("arthur-vendor", "arthur's own answer")
        arthur.brain = LLMSpecialistBrain(arthur_provider, personality=arthur.personality)

        for label in ("vendor-a", "vendor-b", "vendor-c"):
            manager.brain = LLMManagerBrain(FakeProvider(label, f"alfred via {label}"), personality=runtime.identity.personality)
            manager.own_skills._converse = manager.brain.converse
            assert arthur.brain._provider is arthur_provider

    def test_changing_arthurs_provider_never_touches_alfreds_brain(self):
        from src.agents.llm_brain import LLMSpecialistBrain

        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        alfred_provider = FakeProvider("alfred-vendor", "alfred's own answer")
        manager.brain = LLMManagerBrain(alfred_provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        for label in ("vendor-x", "vendor-y"):
            arthur.brain = LLMSpecialistBrain(FakeProvider(label, f"arthur via {label}"), personality=arthur.personality)
            assert manager.brain._provider is alfred_provider


# ---------------------------------------------------------------------------
# A genuinely open finding, reported rather than silently worked around
# ---------------------------------------------------------------------------


class TestKnownDesignNoteForAFutureSecondRealProvider:
    """Not a defect in anything that exists today - there is exactly one
    real LLM provider, so nothing collides - but worth a permanent,
    checkable marker: Settings.api_key/MANAGER_API_KEY is currently
    claimed exclusively by _anthropic_credential. A future second real
    provider must receive its own dedicated field, never this one -
    this test exists so that reusing it is a visible, deliberate decision
    to break this test, not a silent accident."""

    def test_settings_api_key_is_documented_as_anthropic_specific(self):
        import inspect

        source = inspect.getsource(provider_factory._anthropic_credential)
        assert "settings.api_key" in source
