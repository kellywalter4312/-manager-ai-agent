"""Voice-autonomy completion checkpoint - natural requests and the
Brain-assisted Tier 2 fallback.

Two things this file proves, both through the real production path
(scripts.control_center.build() -> manager.handle_task() -> the real
select_voice capability -> the real Windows speech adapter's own
live-discovered voices):

1. Every natural-language example this checkpoint's own brief gives
   ("Alfred, find me an Italian voice with an Italian accent.", "Use a
   Spanish voice.", "Give me an English voice.", "Use a Nigerian English
   voice if one is available.", "Find a female voice that sounds
   appropriate for this.") is handled by Tier 1 - deterministic,
   Brain-free - matching the brief's own "do not make simple requests
   unnecessarily dependent on an LLM" requirement.
2. A request none of that deterministic parsing can classify still
   reaches a real, governed decision - through a real Brain, given only
   the real discovered options and validated against them, never
   trusted blindly, and never a fabricated option - or an honest refusal
   when no Brain is configured.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.services.identity.voice_preference import effective_voice_for
from scripts.control_center import build


def real_system(**env_overrides):
    env = {"MANAGER_SPEECH": "windows", **env_overrides}
    settings = Settings.from_env(env=env, memory="ephemeral")
    return build(settings)


class ScriptedProvider(LLMProvider):
    def __init__(self, answer: str):
        self._answer = answer
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt, *, system=None):
        self.calls.append(prompt)
        return Completion(text=self._answer, provider="scripted")


class TestEveryBriefExampleIsHandledDeterministicallyNoBrainConfigured:
    """The brief's own five examples, verbatim, each through the real
    capability with zero LLM configured - proof Tier 1 alone covers
    them, per "do not make simple requests unnecessarily dependent on
    an LLM"."""

    def test_italian_voice_with_italian_accent(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(
            Task(description="Alfred, find me an Italian voice with an Italian accent.")
        )
        # This machine's real installed voices are all English-only
        # (verified live this checkpoint) - a genuinely unsupported
        # language, refused honestly, never substituted.
        assert not result.succeeded
        assert "italian" in result.error.lower()
        assert result.payload is None

    def test_use_a_spanish_voice(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Use a Spanish voice."))
        assert not result.succeeded
        assert "spanish" in result.error.lower()

    def test_give_me_an_english_voice(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Give me an English voice."))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "select_voice"
        assert result.payload["language_satisfied"] is True
        assert result.payload["brain_assisted"] is False

    def test_use_a_nigerian_english_voice_if_one_is_available(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(
            Task(description="Use a Nigerian English voice if one is available.")
        )
        # Real installed voices are English but not tagged with any
        # accent (SAPI genuinely does not expose one) - language
        # satisfied, accent honestly not, never fabricated.
        assert result.succeeded, result.error
        assert result.payload["language_satisfied"] is True
        assert result.payload["accent_satisfied"] is False
        assert "accent" in result.output.lower()

    def test_find_a_female_voice_that_sounds_appropriate_for_this(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(
            Task(description="Find a female voice that sounds appropriate for this.")
        )
        # This machine's real Windows voices genuinely report gender
        # (VoiceInfo.Gender, live-confirmed: Hazel/Zira=Female) - a
        # real match, deterministic (gender was recognised in Tier 1,
        # never routed to the Brain), never a guess.
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "select_voice"
        assert result.payload["gender_satisfied"] is True
        assert result.payload["brain_assisted"] is False


class TestTier2ReachesARealBrainOnlyWhenTier1FindsNothing:
    def test_a_genuinely_unparseable_request_reaches_the_brain_and_a_real_option_is_chosen(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider(
            "I'd recommend the voice named 'Microsoft Hazel Desktop' because it sounds "
            "clear and professional."
        )
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(
            Task(description="Alfred, find me a voice that sounds professional for this presentation")
        )
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "select_voice"
        assert result.payload["brain_assisted"] is True
        assert result.payload["voice_id"] == "Microsoft Hazel Desktop"
        assert provider.calls, "the real Brain must actually have been asked"

        agent = manager.registry.get(runtime.identity.name) or manager.own_skills
        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile.preferred_voice == "Microsoft Hazel Desktop"

    def test_the_brain_is_only_given_real_discovered_options_never_asked_to_invent_one(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("Microsoft David Desktop sounds best.")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        manager.handle_task(Task(description="find me a voice that sounds professional"))
        assert provider.calls
        prompt = provider.calls[-1]
        for real_name in ("Microsoft David Desktop", "Microsoft Hazel Desktop", "Microsoft Zira Desktop"):
            assert real_name in prompt

    def test_an_ambiguous_brain_answer_is_refused_never_guessed(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("I am not sure which voice would be best here.")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="find me a voice that sounds professional"))
        assert not result.succeeded
        assert "unambiguously" in result.error

    def test_no_brain_configured_degrades_to_an_honest_refusal_never_a_crash(self):
        """With no LLM configured, request.converse is still bound (to
        NullBrain), so this is reached and refused honestly rather than
        via the separate `request.converse is None` guard - both paths
        end at the same never-a-crash, never-a-guess outcome."""
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="find me a voice that sounds professional"))
        assert not result.succeeded
        assert "no reasoning capability is configured" in result.error

    def test_reselect_after_a_tier_2_pick_excludes_it_without_a_second_brain_call(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("I'd recommend 'Microsoft Hazel Desktop'.")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        first = manager.handle_task(Task(description="find me a voice that sounds professional"))
        assert first.succeeded and first.payload["brain_assisted"] is True
        calls_after_first = len(provider.calls)

        second = manager.handle_task(Task(description="choose another"))
        assert second.succeeded
        assert second.payload["voice_id"] != first.payload["voice_id"]
        assert second.payload["brain_assisted"] is False
        assert len(provider.calls) == calls_after_first, "reselect must be deterministic, never a second Brain call"

    def test_an_explicit_override_still_wins_ahead_of_tier_2(self):
        runtime, manager, channel, conversation = real_system()
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        centre = ControlCenter(runtime, manager)
        run_command(centre, f"voice {runtime.identity.name} Microsoft Zira Desktop", 100, channel, conversation)

        provider = ScriptedProvider("Microsoft David Desktop is a good choice.")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="find me a voice that sounds professional"))
        assert result.succeeded
        assert result.payload.get("deferred_to_override") == "Microsoft Zira Desktop"
        assert not provider.calls, "the Brain must never be asked when an explicit override already applies"


class TestUnrelatedRemarksNeverReachSelectVoice:
    """The precision requirement select_voice_intent's own docstring
    states - a bare mention of 'voice' with neither a parseable
    constraint nor the imperative "pick me a voice" shape must be left
    to whatever else already handles it."""

    def test_a_remark_about_having_no_voice_in_a_decision_is_not_claimed(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="I have no voice in this decision"))
        assert result.payload is None or result.payload.get("handled_by") != "select_voice"

    def test_raise_your_voice_is_not_claimed(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="raise your voice"))
        assert result.payload is None or result.payload.get("handled_by") != "select_voice"
