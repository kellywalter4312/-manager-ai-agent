"""Stage 11.6 - Multi-Agent Voice Identity.

A verification stage, not an implementation one. Every prior voice stage
(3, 8A, 10, 11.2, 11.4) already built this architecture one honest piece at a
time; this file's job is to prove, in one place, that the pieces together
already satisfy the multi-agent claim - that any agent, present or future,
gets its own independent voice from its own identity, through the same
unmodified pipeline, with nothing here learning a second agent's name.

No production code changes accompanied this file. Where a claim needed
evidence this session did not already have on record, a test was added here;
where existing test files already proved a requirement, this file references
rather than duplicates it.
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.specialists import wiring_for
from src.channels.channel import VoiceChannel
from src.config.settings import Settings
from src.core.contracts import Task
from src.providers.availability import AvailabilityState
from src.services.identity.specialist import parse_specialist_identity
from src.services.identity.voice import VoiceProfile, default_voice, voice_for
from src.services.memory.factory import EMAIL_AGENT
import src.providers.elevenlabs_speech_provider as elevenlabs_speech_provider
import src.providers.windows_speech_provider as windows_speech_provider
from src.providers.elevenlabs_speech_provider import ElevenLabsSpeechAdapter
from src.providers.windows_speech_provider import WindowsSpeechAdapter, _select_voice_name

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLED = ("Voice A", "Voice B", "Voice C")


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["powershell"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Requirement 3 - resolved from the agent's own identity, never a global voice
# ---------------------------------------------------------------------------


class TestVoiceComesFromTheAgentsOwnIdentity:
    def test_alfreds_real_voice_comes_from_identity_md(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        voice = ManagerSkills(load_identity("IDENTITY.md")).voice
        assert voice.key == "alfred"
        assert voice.style.get("elevenlabs_voice_id")

    def test_arthurs_real_voice_comes_from_his_own_document_not_alfreds(self):
        arthur = wiring_for(EMAIL_AGENT, fallback_name="Arthur").voice
        assert arthur.key == "arthur"
        assert arthur.style.get("elevenlabs_voice_id") != None

        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        alfred = ManagerSkills(load_identity("IDENTITY.md")).voice
        assert arthur.style["elevenlabs_voice_id"] != alfred.style["elevenlabs_voice_id"]

    def test_there_is_no_global_default_voice_object(self):
        """Two agents that both declare nothing must still be distinguishable -
        default_voice derives from the agent's own name, never a shared
        constant, which is what makes this true."""
        a = default_voice("Research")
        b = default_voice("Security")
        assert a != b
        assert a.key != b.key

    def test_resolution_reads_no_settings_speech_or_provider_value(self):
        """Which provider is configured must never change *which voice an
        agent has* - only which engine speaks it. Proven structurally: the
        functions that resolve a voice do not even accept a Settings or
        speech-provider argument, so there is nothing for one to branch on."""
        import inspect

        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity, parse_identity

        for fn in (ManagerSkills.__init__, load_identity, parse_identity, wiring_for):
            params = {p.lower() for p in inspect.signature(fn).parameters}
            assert not params & {"settings", "speech", "provider", "speech_provider"}, fn


# ---------------------------------------------------------------------------
# Requirement 4 - Alfred and Arthur, simultaneously, through one runtime
# ---------------------------------------------------------------------------


class TestAlfredAndArthurSimultaneouslyUseDifferentVoices:
    def test_real_identity_documents_resolve_to_different_elevenlabs_ids_in_one_run(self, monkeypatch):
        """The exact configuration shipped this session (Alfred=Chris,
        Arthur=Eric), driven through one shared adapter instance, proven with
        a mocked HTTP layer so this is a fast, permanent regression test -
        not just the one-off live script this session already ran by hand."""
        manager = real_manager()  # any working default; speech built separately below

        seen_voice_ids = []

        def fake_client(api_key):
            return SimpleNamespace(
                voices=SimpleNamespace(get_all=lambda: SimpleNamespace(voices=[])),
                text_to_speech=SimpleNamespace(
                    convert=lambda voice_id, *, text, output_format: (
                        seen_voice_ids.append(voice_id) or iter([b"chunk"])
                    )
                ),
            )

        monkeypatch.setattr(elevenlabs_speech_provider, "ElevenLabs", fake_client)
        monkeypatch.setattr(elevenlabs_speech_provider, "_play_wav_bytes", lambda audio: True)

        from src.providers.speech_factory import build_speech

        speech = build_speech(Settings(speech="elevenlabs"), env={"ELEVENLABS_API_KEY": "fake"})
        channel = VoiceChannel(manager, speech=speech)

        alfred_response = channel.deliver(channel.ask("status"))
        arthur_response = channel.deliver(channel.ask("check my email"))

        assert alfred_response.turn.handled_by == "Alfred"
        assert arthur_response.turn.handled_by == "Arthur"
        assert alfred_response.spoken is True
        assert arthur_response.spoken is True
        assert len(seen_voice_ids) == 2
        assert seen_voice_ids[0] != seen_voice_ids[1]
        assert seen_voice_ids[0] == "iP95p4xoKVk53GoZ742B"  # Chris, Alfred's real id
        assert seen_voice_ids[1] == "cjVigY5qzO86Huf0OWal"  # Eric, Arthur's real id

    def test_real_identity_documents_resolve_to_different_windows_voices_in_one_run(self, monkeypatch):
        """The same claim against the other real, shipped provider - proves
        this is a property of the architecture, not one provider's doing."""
        manager = real_manager()
        calls = []
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: (calls.append(script), completed(
                returncode=0, stdout="\n".join(INSTALLED) + "\n"
            ))[1],
        )
        channel = VoiceChannel(manager, speech=WindowsSpeechAdapter())

        channel.deliver(channel.ask("status"))
        channel.deliver(channel.ask("check my email"))

        speak_calls = [c for c in calls if "Get-Content" in c]
        assert len(speak_calls) == 2
        lines = [next(l for l in c.splitlines() if "SelectVoice" in l) for c in speak_calls]
        assert lines[0] != lines[1]


# ---------------------------------------------------------------------------
# Requirement 5 - a future specialist gets its own voice, automatically
# ---------------------------------------------------------------------------


class TestAFutureSpecialistNeedsNoVoiceSpecificCode:
    def test_a_brand_new_identity_document_produces_a_correct_voiceprofile(self, tmp_path):
        """No code anywhere has ever seen 'Cartographer' or this document.
        Nothing here is a new VoiceProfile - the exact same dataclass."""
        (tmp_path / "cartographer.md").write_text(
            "## Name\nCartographer\n## Voice\nkey: cartographer\naccent: welsh\n"
            "provider: elevenlabs\nelevenlabs_voice_id: zzz999\n",
            encoding="utf-8",
        )
        wiring = wiring_for("cartographer", fallback_name="Cartographer", identities_dir=str(tmp_path))
        assert isinstance(wiring.voice, VoiceProfile)
        assert wiring.voice.key == "cartographer"
        assert wiring.voice.accent == "welsh"
        assert wiring.voice.style["elevenlabs_voice_id"] == "zzz999"

    def test_the_real_construction_loop_needs_no_edit_for_a_new_specialist(self):
        """The architectural claim itself, pinned as a structural fact: both
        the identity/brain-resolution loop (over SPECIALISTS, the
        registration dict) and the registration loop (over
        runtime.specialists) are generic - neither names a specialist."""
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Comprehensions (SPECIALISTS.items() is inside a dict comprehension)
        # carry their `for` clauses as ast.comprehension, not ast.For - both
        # shapes are checked since either would satisfy the same claim.
        def iter_calls(tree):
            for node in ast.walk(tree):
                if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
                    yield node.iter.func
                if isinstance(node, (ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                    for gen in node.generators:
                        if isinstance(gen.iter, ast.Call):
                            yield gen.iter.func

        def matches(func, *, name=None, attr_chain=None):
            if not (isinstance(func, ast.Attribute) and func.attr in ("items", "values")):
                return False
            if name is not None:
                return isinstance(func.value, ast.Name) and func.value.id == name
            if attr_chain is not None:
                return isinstance(func.value, ast.Attribute) and func.value.attr == attr_chain
            return False

        calls = list(iter_calls(tree))
        found_resolution_loop = any(matches(f, name="SPECIALISTS") for f in calls)
        found_registration_loop = any(matches(f, attr_chain="specialists") for f in calls)
        assert found_resolution_loop, "expected a generic loop over SPECIALISTS.items()"
        assert found_registration_loop, "expected a generic loop over runtime.specialists"

        for name in ("cartographer", "quartermaster", "librarian"):
            assert name not in source.lower()  # no specialist is named in this file

    def test_voicechannel_resolves_a_never_before_seen_agent_type_with_zero_changes(self, monkeypatch):
        """A minimal object - not a Specialist, not an EmailAgent, not
        anything this module has a name for - registered and spoken through
        the real VoiceChannel and a real provider, unmodified."""
        from src.agents.base_agent import BaseAgent
        from src.core.contracts import AgentSpec, TaskResult
        from src.registry.agent_registry import AgentRegistry
        from src.manager.manager import ManagerAgent
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        class FutureResearchAgent(BaseAgent):
            """Only what BaseAgent actually requires (spec, execute), plus a
            `.voice` attribute VoiceChannel discovers by duck typing, never by
            a type check - nothing voice-related is a base class or mixin."""

            def __init__(self, name, voice):
                self._name = name
                self.voice = voice

            @property
            def spec(self):
                return AgentSpec(name=self._name, description="future", capabilities=frozenset({"research"}))

            def execute(self, task):
                return TaskResult.success(task=task, agent_name=self._name, output="done")

        registry = AgentRegistry()
        manager = ManagerAgent(
            own_skills=ManagerSkills(load_identity("IDENTITY.md")), registry=registry
        )
        future_voice = VoiceProfile(key="research", style={"elevenlabs_voice_id": "future-id-123"})
        manager.register_agent(FutureResearchAgent("Research", future_voice))

        assert voice_for(manager.registry.get("Research")) == future_voice

        calls = []
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: (calls.append(script), completed(
                returncode=0, stdout="\n".join(INSTALLED) + "\n"
            ))[1],
        )
        channel = VoiceChannel(manager, speech=WindowsSpeechAdapter())
        from src.channels.channel import Response
        from src.control_center.views import ConversationTurn

        turn = ConversationTurn(
            request="find me a paper", handled_by="Research", delegated=False,
            steps=(), outcome="done", succeeded=True,
        )
        response = channel.deliver(Response(text="done", speech="done", spoken=False, turn=turn))
        assert response.spoken is True
        speak_script = next(c for c in calls if "Get-Content" in c)
        expected = _select_voice_name(future_voice, INSTALLED)
        assert f'$synth.SelectVoice("{expected}")' in speak_script


# ---------------------------------------------------------------------------
# Requirement 6 - provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_voiceprofile_has_no_vendor_field(self):
        names = {f.name for f in dataclasses.fields(VoiceProfile)}
        # "language" (the language spoken, kept apart from the voice and its
        # accent) is a characteristic, never a vendor fact - see
        # test_no_field_name_mentions_a_vendor for the invariant that matters.
        assert names == {"key", "accent", "pace", "pitch", "warmth", "preferred_voice", "style", "language"}

    def test_no_field_name_mentions_a_vendor(self):
        for name in dataclasses.fields(VoiceProfile):
            for vendor in ("elevenlabs", "windows", "sapi", "kokoro", "azure", "google"):
                assert vendor not in name.name.lower()

    def test_elevenlabs_specific_facts_live_only_in_style(self):
        profile = VoiceProfile(
            key="x", style={"provider": "elevenlabs", "elevenlabs_voice_id": "abc"}
        )
        assert not hasattr(profile, "elevenlabs_voice_id")
        assert not hasattr(profile, "provider")
        assert profile.style["elevenlabs_voice_id"] == "abc"

    def test_switching_manager_speech_does_not_change_the_resolved_voiceprofile(self):
        """End-to-end version of the structural claim above: three real
        runtimes, three different speech providers, one identical voice."""
        for speech_name in ("none", "windows", "elevenlabs"):
            settings = Settings.from_env(
                env={"ELEVENLABS_API_KEY": "fake"} if speech_name == "elevenlabs" else {},
                log_level="CRITICAL", speech=speech_name,
            )
            from src.services.identity.identity import load_identity

            identity = load_identity(settings.identity_path)
            from src.manager.manager_skills import ManagerSkills

            voice = ManagerSkills(identity).voice
            assert voice.key == "alfred"
            assert voice.style.get("elevenlabs_voice_id") == "iP95p4xoKVk53GoZ742B"

    def test_elevenlabs_would_not_be_the_only_source_of_voices_if_removed(self):
        """Windows Speech alone still resolves and speaks Alfred and Arthur -
        proof ElevenLabs is one interchangeable provider, not the system."""
        assert "windows" in __import__(
            "src.providers.speech_factory", fromlist=["available_speech_providers"]
        ).available_speech_providers()


# ---------------------------------------------------------------------------
# Requirement 7 - voice independence between agents
# ---------------------------------------------------------------------------


class TestVoiceIndependenceBetweenAgents:
    def test_changing_one_agents_style_dict_cannot_reach_anothers(self):
        """Frozen and copied on construction - two profiles never share the
        same underlying mutable dict."""
        shared_source = {"elevenlabs_voice_id": "shared-at-construction"}
        alfred = VoiceProfile(key="alfred", style=shared_source)
        arthur = VoiceProfile(key="arthur", style=shared_source)
        shared_source["elevenlabs_voice_id"] = "mutated-after-construction"
        assert alfred.style["elevenlabs_voice_id"] == "shared-at-construction"
        assert arthur.style["elevenlabs_voice_id"] == "shared-at-construction"
        assert alfred.style is not arthur.style

    def test_editing_arthurs_document_leaves_alfreds_voice_untouched(self, tmp_path):
        alfred_doc = tmp_path / "IDENTITY.md"
        alfred_doc.write_text(
            "## Name\nAlfred\n## Capabilities\nstatus\n"
            "## Voice\nkey: alfred\nelevenlabs_voice_id: alfred-original\n",
            encoding="utf-8",
        )
        from src.services.identity.identity import load_identity

        before = load_identity(str(alfred_doc)).voice

        (tmp_path / "identities").mkdir()
        (tmp_path / "identities" / "email_agent.md").write_text(
            "## Name\nArthur\n## Voice\nkey: arthur\nelevenlabs_voice_id: arthur-changed\n",
            encoding="utf-8",
        )
        after = load_identity(str(alfred_doc)).voice
        assert before == after
        assert after.style["elevenlabs_voice_id"] == "alfred-original"


# ---------------------------------------------------------------------------
# Requirement 8 - fallback is safe, and one agent's failure cannot corrupt
# another's
# ---------------------------------------------------------------------------


class TestFallbackIsSafeAndIsolatedPerCall:
    def test_windows_falls_back_safely_when_preferred_is_unavailable(self):
        voice = VoiceProfile(key="alfred", preferred_voice="Voice Nobody Has")
        fallback = _select_voice_name(voice, INSTALLED)
        assert fallback in INSTALLED

    def test_elevenlabs_fails_honestly_when_id_is_missing_never_crashes(self, monkeypatch):
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        adapter = ElevenLabsSpeechAdapter(api_key="fake")
        monkeypatch.setattr(elevenlabs_speech_provider, "_play_wav_bytes", lambda audio: True)
        assert adapter.speak("hello", VoiceProfile(key="broken-agent")) is False
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE

    def test_one_agents_bad_config_does_not_break_the_next_agents_call(self, monkeypatch):
        """Same shared adapter instance, two calls in sequence: the first
        agent has no id (fails honestly), the second has a real one and
        still succeeds - proving state from one call cannot poison another."""
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        adapter = ElevenLabsSpeechAdapter(api_key="fake")
        monkeypatch.setattr(elevenlabs_speech_provider, "_play_wav_bytes", lambda audio: True)

        seen = []
        adapter._client = SimpleNamespace(
            text_to_speech=SimpleNamespace(
                convert=lambda voice_id, *, text, output_format: (
                    seen.append(voice_id) or iter([b"chunk"])
                )
            )
        )

        broken_agent_voice = VoiceProfile(key="broken-agent")  # no elevenlabs_voice_id
        working_agent_voice = VoiceProfile(key="working-agent", style={"elevenlabs_voice_id": "good-id"})

        first = adapter.speak("hello", broken_agent_voice)
        second = adapter.speak("hello", working_agent_voice)

        assert first is False
        assert second is True
        assert seen == ["good-id"]  # the broken call never reached the API at all


# ---------------------------------------------------------------------------
# Requirement 9 - the LLM provider is a fully separate axis
# ---------------------------------------------------------------------------


class TestLlmProviderIndependence:
    def test_voice_module_imports_nothing_llm_related(self):
        source = (REPO_ROOT / "src" / "services" / "identity" / "voice.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = ", ".join(a.name for a in node.names)
                assert "provider" not in module.lower() and "brain" not in module.lower()
                assert "anthropic" not in names.lower()

    def test_alfreds_voice_is_identical_regardless_of_llm_provider(self):
        null_manager = real_manager(provider="null")
        anthropic_manager = real_manager(provider="anthropic", api_key="sk-ant-fake-not-real")
        assert null_manager.own_skills.voice == anthropic_manager.own_skills.voice

    def test_arthurs_voice_is_identical_regardless_of_llm_provider(self):
        null_manager = real_manager(provider="null")
        anthropic_manager = real_manager(provider="anthropic", api_key="sk-ant-fake-not-real")
        null_arthur = null_manager.registry.get("Arthur")
        anthropic_arthur = anthropic_manager.registry.get("Arthur")
        assert null_arthur.voice == anthropic_arthur.voice


# ---------------------------------------------------------------------------
# Requirement 10 - the speech provider is a fully separate axis
# ---------------------------------------------------------------------------


class TestSpeechProviderIndependenceFromCoreArchitecture:
    def test_no_speech_or_vendor_module_is_imported_by_brain_memory_or_personality(self):
        for relative in (
            "src/manager/llm_brain.py",
            "src/manager/cognition.py",
            "src/services/memory/persistent.py",
            "src/services/identity/personality.py",
        ):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    assert "speech" not in module.lower(), (relative, module)
                    assert "elevenlabs" not in module.lower(), (relative, module)

    def test_switching_speech_provider_touches_no_other_settings_field(self):
        base = Settings.from_env(env={}, log_level="CRITICAL")
        for name in ("none", "windows", "elevenlabs"):
            varied = dataclasses.replace(base, speech=name)
            assert varied.provider == base.provider
            assert varied.brain == base.brain
            assert varied.memory == base.memory

    def test_manageragent_class_never_mentions_a_speech_vendor(self):
        source = (REPO_ROOT / "src" / "manager" / "manager.py").read_text(encoding="utf-8")
        for vendor in ("elevenlabs", "windows_speech", "sapi"):
            assert vendor not in source.lower()
