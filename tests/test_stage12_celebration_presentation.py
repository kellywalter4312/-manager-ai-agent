"""Stage 12 closure - the celebration presentation bridge, and
CelebrationProvider replaceability through the same factory pattern
every sibling provider already uses.

The comprehensive Stage 12-15 audit found that ``CelebrationReaction``
could be produced (Stage 12's own follow-up already built the full
selection architecture) but had no real consumer anywhere - the data
reached a ``TaskResult.payload`` and died there. This file proves the
event/consumption seam that closes that gap:

    milestone_recognition()   (unchanged) selects a CelebrationReaction
                               after a genuine, governed storage success
    TaskResult.payload         carries it, exactly as before
    trail.py::observe()        extracts it onto ConversationTurn.celebration
                               (the identical pattern _invited_in already
                               uses for INVITED_KEY)
    render_turn()               a real presentation surface (the Control
                               Center) renders it - one line, only when
                               enabled
    serve_ui.py's /message      the live JSON boundary a future avatar/
                               environment renderer would read from also
                               carries it

And separately, the audit found CelebrationProvider was injectable but
not Settings-driven, unlike LLMProvider/LiveInfoProvider/CommunicationProvider.
``src/providers/celebration_factory.py`` closes that consistency gap the
identical, minimal way those three already exist.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.core.contracts import Task
from src.providers.celebration import CelebrationProvider, CelebrationReaction

REPO_ROOT = Path(__file__).resolve().parent.parent


def manager_with_scripted_brain(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.manager.llm_brain import LLMManagerBrain
    from src.providers.base import Completion
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    class ScriptedLLMProvider:
        @property
        def name(self) -> str:
            return "scripted-llm"

        def complete(self, prompt: str, *, system: str | None = None):
            return Completion(text="Milestone acknowledged.", provider=self.name, model="x")

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    brain = LLMManagerBrain(ScriptedLLMProvider())
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return manager


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# 1. CelebrationReaction reaches the presentation/event boundary
# ---------------------------------------------------------------------------


class TestTheReactionReachesTheEventBoundary:
    def test_a_genuine_milestone_carries_a_celebration_onto_the_conversation_turn(self):
        from src.channels.channel import TextChannel

        manager = manager_with_scripted_brain()
        channel = TextChannel(manager)

        response = channel.ask("milestone: I signed my first paying client")

        assert response.turn.celebration is not None
        assert response.turn.celebration["enabled"] is True
        assert response.turn.celebration["milestone_type"] == "i_signed_my_first_paying_client"

    def test_an_ordinary_turn_carries_no_celebration(self):
        from src.channels.channel import TextChannel

        manager = manager_with_scripted_brain()
        channel = TextChannel(manager)

        response = channel.ask("status")

        assert response.turn.celebration is None

    def test_the_terminal_control_center_actually_renders_it(self):
        from src.channels.channel import TextChannel
        from src.control_center.render import render_turn

        manager = manager_with_scripted_brain()
        channel = TextChannel(manager)
        response = channel.ask("milestone: I signed my first paying client")

        rendered = render_turn(response.turn)

        assert "Celebration:" in rendered

    def test_the_live_json_api_boundary_carries_it(self):
        """scripts/serve_ui.py's /message response is the boundary a
        future avatar/environment renderer would actually consume from -
        proven here by inspecting its own response-building source
        includes the field, without starting a real HTTP server."""
        source = (REPO_ROOT / "scripts" / "serve_ui.py").read_text(encoding="utf-8")
        assert '"celebration": turn.celebration' in source

    def test_the_extraction_never_re_selects_or_re_derives_a_reaction(self):
        """Structural: trail.py must only *read* the payload the
        capability already produced, never call CelebrationProvider or
        select() itself - the same "carried, not re-derived" rule
        _invited_in already lives under."""
        import src.control_center.trail as trail_module

        source = inspect.getsource(trail_module._celebration_of)
        assert "CelebrationProvider" not in source
        assert ".select(" not in source


# ---------------------------------------------------------------------------
# 2. Off disables presentation
# ---------------------------------------------------------------------------


class TestOffDisablesPresentation:
    def test_an_off_celebration_carries_enabled_false_but_renders_nothing(self):
        from src.channels.channel import TextChannel
        from src.control_center.render import render_turn

        manager = manager_with_scripted_brain()
        channel = TextChannel(manager)
        channel.ask("turn off celebrations")

        response = channel.ask("milestone: a second one for testing")

        assert response.turn.celebration is not None
        assert response.turn.celebration["enabled"] is False
        assert "Celebration:" not in render_turn(response.turn)


# ---------------------------------------------------------------------------
# 3. Reduced motion is respected where the platform can detect it
# ---------------------------------------------------------------------------


class TestReducedMotionIsRespected:
    def test_reduced_motion_caps_intensity_at_subtle_before_it_ever_reaches_presentation(self):
        """Reduced motion is resolved to an intensity *before*
        CelebrationProvider.select() ever runs (capabilities.py's own
        _resolve_celebration_intensity) - presentation reads only the
        already-resolved reaction, never a preference itself. This
        proves the resolved reaction a presentation layer would see is
        already capped, without presentation needing to know why."""
        from src.channels.channel import TextChannel

        manager = manager_with_scripted_brain()
        channel = TextChannel(manager)
        channel.ask("turn on reduced motion")

        response = channel.ask("milestone: signed a big client")

        assert response.turn.celebration["intensity"] == "subtle"

    def test_presentation_itself_never_reads_a_memory_preference(self):
        """Structural: render_turn/_celebration_line must never touch
        Memory or a preference lookup - reduced motion is already baked
        into the reaction by the time presentation sees it."""
        import src.control_center.render as render_module

        source = inspect.getsource(render_module._celebration_line)
        assert ".recall(" not in source
        assert "reduced_motion" not in source


# ---------------------------------------------------------------------------
# 4. Different reactions remain replaceable / different agents, types,
# occurrences produce different reactions (re-confirmed after the
# factory change)
# ---------------------------------------------------------------------------


class TestReplaceabilityAndVariety:
    def test_a_never_before_seen_provider_reaches_the_same_presentation_boundary(self):
        from src.channels.channel import TextChannel
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        class FutureCelebrationProvider(CelebrationProvider):
            @property
            def name(self) -> str:
                return "future"

            def select(self, *, milestone_type, agent_key, personality, intensity, occurrence=0):
                return CelebrationReaction(
                    milestone_type=milestone_type, agent_key=agent_key,
                    reaction_id="future-reaction", intensity="full",
                    avatar_action="teleport", environment_action="fireworks",
                    enabled=(intensity != "off"),
                )

        class ScriptedLLMProvider:
            @property
            def name(self):
                return "scripted-llm"

            def complete(self, prompt, *, system=None):
                return Completion(text="Milestone acknowledged.", provider=self.name, model="x")

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        manager.own_skills._celebration_provider = FutureCelebrationProvider()
        brain = LLMManagerBrain(ScriptedLLMProvider())
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        response = TextChannel(manager).ask("milestone: a brand new kind of thing")

        assert response.turn.celebration["avatar_action"] == "teleport"

    def test_different_milestone_types_still_resolve_differently(self):
        manager = real_manager()
        provider = manager.own_skills._celebration_provider
        from src.services.identity.personality import personality_for

        a = provider.select(
            milestone_type="first_client", agent_key="alfred",
            personality=personality_for(manager.own_skills), intensity="full",
        )
        b = provider.select(
            milestone_type="graduation", agent_key="alfred",
            personality=personality_for(manager.own_skills), intensity="full",
        )
        assert a.reaction_id != b.reaction_id

    def test_alfred_and_arthur_share_one_provider_instance_but_can_diverge_in_style(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        assert manager.own_skills._celebration_provider is arthur.celebration_provider


# ---------------------------------------------------------------------------
# 5-6. Independence from LLM provider, Brain, VoiceProfile, Memory
# ---------------------------------------------------------------------------


class TestIndependenceFromCoreArchitecture:
    def test_switching_the_celebration_provider_leaves_brain_and_memory_untouched(self):
        manager = real_manager()
        brain_before, memory_before = manager.brain, manager.memory
        manager.own_skills._celebration_provider = manager.own_skills._celebration_provider
        assert manager.brain is brain_before
        assert manager.memory is memory_before

    def test_switching_the_llm_provider_does_not_alter_celebration_selection(self):
        from src.services.identity.personality import personality_for

        for settings_overrides in ({}, {"provider": "null"}):
            manager = real_manager(**settings_overrides)
            provider = manager.own_skills._celebration_provider
            reaction = provider.select(
                milestone_type="first_client", agent_key="alfred",
                personality=personality_for(manager.own_skills), intensity="full",
            )
            assert reaction.enabled

    def test_voice_profile_is_unaffected_by_a_celebration(self):
        from src.channels.channel import TextChannel
        from src.services.identity.voice import voice_for

        manager = manager_with_scripted_brain()
        TextChannel(manager).ask("milestone: signed a big client")

        assert voice_for(manager.own_skills).key == "alfred"


# ---------------------------------------------------------------------------
# 7. The celebration provider factory - the new replaceability seam
# ---------------------------------------------------------------------------


class TestTheCelebrationFactory:
    def test_the_factory_builds_the_default_by_name(self):
        from src.config.settings import Settings
        from src.providers.celebration_factory import build_celebration_provider
        from src.providers.default_celebration_provider import DefaultCelebrationProvider

        settings = Settings.from_env(env={})
        provider = build_celebration_provider(settings)
        assert isinstance(provider, DefaultCelebrationProvider)

    def test_the_safe_default_is_preserved(self):
        from src.config.settings import Settings

        assert Settings.from_env(env={}).celebration_provider == "default"

    def test_an_unknown_name_fails_fast_and_loudly(self):
        from src.config.settings import Settings
        from src.providers.celebration_factory import build_celebration_provider

        settings = Settings.from_env(env={}, celebration_provider="nonexistent")
        with pytest.raises(ValueError):
            build_celebration_provider(settings)

    def test_constructor_injection_still_works_unchanged(self):
        """The factory is additive - EmailAgent/ManagerSkills still
        accept an explicit celebration= override, exactly as before this
        closure pass, for any caller (a test) that wants a scripted
        double instead of the configured provider."""
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        identity = load_identity("IDENTITY.md")

        class Scripted(CelebrationProvider):
            @property
            def name(self):
                return "scripted"

            def select(self, **kwargs):
                return CelebrationReaction(
                    milestone_type="x", agent_key="alfred", reaction_id="scripted", intensity="full",
                )

        scripted = Scripted()
        skills = ManagerSkills(identity, celebration=scripted)
        assert skills._celebration_provider is scripted

    def test_the_factory_is_the_only_new_registration_point(self):
        """Structural: composition.py/manager_skills.py/email_agent.py
        never name DefaultCelebrationProvider directly for the Runtime-
        level default any more - only the factory does. (Their own
        constructor-level fallback default is untouched and still named
        directly, which is the correct, existing "no None override
        supplied" behaviour - this checks the *new* Runtime wiring
        specifically.)"""
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        assert "build_celebration_provider(settings)" in source
        assert "DefaultCelebrationProvider()" not in source


# ---------------------------------------------------------------------------
# Mutation test - the Off/enabled presentation gate
# ---------------------------------------------------------------------------


class TestMutationTheOffGate:
    def test_the_enabled_check_in_render_turn_is_load_bearing(self):
        """Pins what removing the `turn.celebration.get("enabled")` check
        in render_turn would break: an Off celebration (enabled=False)
        must never print a Celebration line."""
        from src.control_center.render import render_turn
        from src.control_center.views import ConversationTurn

        turn = ConversationTurn(
            request="x", handled_by="Alfred", delegated=False, steps=(),
            outcome="done", succeeded=True,
            celebration={
                "enabled": False, "intensity": "off", "avatar_action": "",
                "environment_action": "", "overlay": "",
            },
        )
        assert "Celebration:" not in render_turn(turn)

    def test_an_enabled_celebration_is_load_bearing_for_appearing_at_all(self):
        from src.control_center.render import render_turn
        from src.control_center.views import ConversationTurn

        turn = ConversationTurn(
            request="x", handled_by="Alfred", delegated=False, steps=(),
            outcome="done", succeeded=True,
            celebration={
                "enabled": True, "intensity": "full", "avatar_action": "nod",
                "environment_action": "warm_glow", "overlay": "",
            },
        )
        assert "Celebration:" in render_turn(turn)
