"""Stage 17.1 - Agent Presence + the first visible avatar/presence slice.

    Agent
    +-- Brain
    +-- Memory
    +-- Identity
    +-- Personality
    +-- VoiceProfile
    +-- Capabilities
    +-- Connections
    +-- AgentPresence
          +-- AvatarProfile

This file proves the guarantees the Stage 17.1 brief requires before this
phase can be called done: choosing/changing an agent or its avatar never
constructs a second Agent/Brain/Memory; an avatar is independent of Brain,
Memory, Identity, Personality and VoiceProfile; AvatarProvider is genuinely
replaceable; and nothing here assumes a particular device.

Nothing in this file exercises rendering, animation or a real renderer -
Stage 17.1 is presence *data*, not a Digital Human.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.avatar import AvatarProfile, AvatarProvider
from src.providers.avatar_factory import available_avatar_providers, build_avatar_provider
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import (
    AgentPresence,
    PresenceState,
    avatar_preference_for,
    presence_for,
    set_avatar_preference,
)
from src.services.identity.voice import voice_for
from scripts.exchange import exchange, memory_for

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


def system(**overrides):
    """A real, composed system - the same recipe test_live_ui.py's own
    ``live_system()`` uses - with Arthur discovered the same way every
    other entry point discovers him: through composition, never listed
    here."""
    runtime = build_runtime(settings(**overrides))
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    channel = build_channel(runtime, manager)
    conversation = build_conversation(manager)
    centre = ControlCenter(runtime, manager)
    return runtime, manager, channel, conversation, centre


# ---------------------------------------------------------------------------
# 1. Agent selection: Alfred/Arthur resolve to the canonical instance -
#    never a new one - and an unknown name is not silently invented.
# ---------------------------------------------------------------------------


class TestAgentSelectionNeverConstructsANewAgent:
    def test_addressing_arthur_resolves_to_the_one_registered_instance(self):
        _, manager, channel, conversation, _ = system()
        before = manager.registry.get("Arthur")
        assert before is not None

        turn = exchange(channel, conversation, "what can you do?", addressed="Arthur")

        after = manager.registry.get("Arthur")
        assert after is before, "addressing Arthur must never construct a second Arthur"
        assert len(manager.registry.all_agents()) == 2
        assert turn.handled_by == "Arthur"

    def test_addressing_alfred_by_name_resolves_to_own_skills_not_a_new_agent(self):
        _, manager, channel, conversation, _ = system()
        before = manager.own_skills

        exchange(channel, conversation, "what can you do?", addressed=manager.own_skills.name)

        assert manager.own_skills is before
        assert len(manager.registry.all_agents()) == 2

    def test_an_unregistered_address_is_not_invented_and_does_not_crash(self):
        _, manager, channel, conversation, _ = system()
        turn = exchange(channel, conversation, "hello there", addressed="Nobody")
        assert manager.registry.get("Nobody") is None
        assert len(manager.registry.all_agents()) == 2
        # Falls back to ordinary routing rather than raising.
        assert turn is not None
        # An unrecognised override must never reach the transcript or the
        # room as if it had been resolved - only a real, registered name
        # may ever appear as `addressed` or gain a joined Participant.
        assert conversation.utterances[0].addressed != "Nobody"
        assert not conversation.has("Nobody")

    def test_addressing_still_records_who_was_actually_spoken_to(self):
        _, manager, channel, conversation, _ = system()
        exchange(channel, conversation, "what can you do?", addressed="Arthur")
        said = conversation.utterances[0]
        assert said.addressed == "Arthur"


# ---------------------------------------------------------------------------
# 2. AvatarProfile: construction, invariants, serialization.
# ---------------------------------------------------------------------------


class TestAvatarProfile:
    def test_a_minimal_profile_constructs_with_honest_defaults(self):
        p = AvatarProfile(avatar_id="x", label="X")
        assert p.avatar_id == "x"
        assert p.label == "X"
        assert p.body_type == ""
        assert p.accent_color == ""
        assert p.style == {}

    def test_an_empty_avatar_id_is_rejected(self):
        with pytest.raises(ValueError):
            AvatarProfile(avatar_id="   ", label="X")

    def test_an_empty_label_is_rejected(self):
        with pytest.raises(ValueError):
            AvatarProfile(avatar_id="x", label="  ")

    def test_avatar_id_is_normalised_to_lowercase(self):
        p = AvatarProfile(avatar_id="  WARM_Formal  ", label="Warm")
        assert p.avatar_id == "warm_formal"

    def test_whitespace_is_collapsed_not_just_trimmed(self):
        p = AvatarProfile(avatar_id="a", label="Warm   &    Formal")
        assert p.label == "Warm & Formal"

    def test_is_frozen(self):
        p = AvatarProfile(avatar_id="a", label="A")
        with pytest.raises(Exception):
            p.avatar_id = "b"  # type: ignore[misc]

    def test_style_is_never_interpreted_only_carried(self):
        p = AvatarProfile(avatar_id="a", label="A", style={"model": "future-renderer-param"})
        assert p.to_dict()["style"] == {"model": "future-renderer-param"}

    def test_to_dict_is_a_plain_json_shaped_payload(self):
        p = AvatarProfile(
            avatar_id="a", label="A", body_type="humanoid", accent_color="teal", style={"k": 1}
        )
        assert p.to_dict() == {
            "avatar_id": "a",
            "label": "A",
            "body_type": "humanoid",
            "accent_color": "teal",
            "style": {"k": 1},
        }

    def test_describe_names_both_label_and_id(self):
        p = AvatarProfile(avatar_id="a", label="A")
        assert p.describe() == "A (a)"


# ---------------------------------------------------------------------------
# 3. Per-agent avatar identity: independent choices, independent storage.
# ---------------------------------------------------------------------------


class TestPerAgentAvatarIndependence:
    def test_alfred_and_arthur_get_different_deterministic_defaults(self):
        provider = DefaultAvatarProvider()
        assert provider.default_for("Alfred") != provider.default_for("Arthur")

    def test_choosing_alfreds_avatar_does_not_change_arthurs(self):
        _, manager, _, _, _ = system()
        provider = DefaultAvatarProvider()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")

        alfred_default = provider.default_for(manager.own_skills.name)
        arthur_default = provider.default_for("Arthur")
        chosen_for_alfred = next(
            p.avatar_id for p in provider.catalog() if p.avatar_id != alfred_default.avatar_id
        )

        ok = set_avatar_preference(
            manager.own_skills.name,
            chosen_for_alfred,
            provider=provider,
            recall=alfred_memory.recall,
            remember=alfred_memory.remember,
            forget=alfred_memory.forget,
        )
        assert ok

        assert (
            avatar_preference_for(manager.own_skills.name, recall=alfred_memory.recall)
            == chosen_for_alfred
        )
        # Arthur's own memory never received Alfred's preference write.
        assert avatar_preference_for("Arthur", recall=arthur_memory.recall) is None
        assert presence_for("Arthur", provider=provider, recall=arthur_memory.recall).avatar == (
            arthur_default
        )

    def test_an_unknown_avatar_id_is_rejected_and_writes_nothing(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        before = len(memory.recall(limit=50))

        ok = set_avatar_preference(
            manager.own_skills.name,
            "not-a-real-avatar",
            provider=DefaultAvatarProvider(),
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

        assert ok is False
        assert len(memory.recall(limit=50)) == before

    def test_choosing_again_replaces_rather_than_accumulates(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        provider = DefaultAvatarProvider()
        ids = [p.avatar_id for p in provider.catalog()]

        for avatar_id in ids:
            set_avatar_preference(
                manager.own_skills.name,
                avatar_id,
                provider=provider,
                recall=memory.recall,
                remember=memory.remember,
                forget=memory.forget,
            )

        tagged = [
            r
            for r in memory.recall(limit=50)
            if (r.context or {}).get("fact_type") == "avatar_preference"
        ]
        assert len(tagged) == 1
        assert tagged[0].context["avatar_id"] == ids[-1]


# ---------------------------------------------------------------------------
# 4. Changing an avatar must not touch Brain/Memory/Identity/Personality/
#    VoiceProfile - the same guarantee this project already gives every
#    other pair of independent identity facets, extended one layer.
# ---------------------------------------------------------------------------


class TestAvatarChangeIsIsolated:
    def test_choosing_an_avatar_does_not_replace_brain_or_memory_objects(self):
        _, manager, _, _, _ = system()
        brain_before = manager.brain
        memory_before = manager.memory
        skills_before = manager.own_skills

        provider = DefaultAvatarProvider()
        memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            manager.own_skills.name,
            "calm_composed",
            provider=provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

        assert manager.brain is brain_before
        assert manager.memory is memory_before
        assert manager.own_skills is skills_before

    def test_choosing_an_avatar_does_not_change_voice_or_personality(self):
        _, manager, _, _, _ = system()
        skills = manager.own_skills
        voice_before = voice_for(skills)
        personality_before = personality_for(skills)

        provider = DefaultAvatarProvider()
        memory = memory_for(manager, skills.name)
        set_avatar_preference(
            skills.name,
            "bright_approachable",
            provider=provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

        assert voice_for(skills) == voice_before
        assert personality_for(skills) == personality_before

    def test_avatarprofile_shares_no_field_with_voiceprofile(self):
        """A structural guarantee, not just a behavioural one: nothing
        auditory ever lands on AvatarProfile, so a future edit cannot
        accidentally duplicate voice onto it."""
        voice_only = {"key", "accent", "pace", "pitch", "warmth", "preferred_voice"}
        assert not (set(AvatarProfile.__dataclass_fields__) & voice_only)


# ---------------------------------------------------------------------------
# 5. LLM independence: swapping the brain/provider must not invalidate a
#    stored avatar choice or the provider that resolves it.
# ---------------------------------------------------------------------------


class TestLLMIndependence:
    def test_swapping_the_manager_brain_does_not_change_the_resolved_avatar(self):
        _, manager, _, _, _ = system()
        provider = DefaultAvatarProvider()
        memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            manager.own_skills.name,
            "warm_formal",
            provider=provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )
        before = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)
        assert after.avatar == before.avatar == AvatarProfile(
            avatar_id="warm_formal",
            label="Warm & Formal",
            body_type="humanoid",
            accent_color="amber",
            style={"direction": "composed, measured presence"},
        )

    def test_avatar_module_imports_nothing_from_the_brain_or_llm_layer(self):
        import ast

        tree = ast.parse((REPO_ROOT / "src" / "providers" / "avatar.py").read_text("utf-8"))
        imported = {
            n.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for n in [node]
        }
        assert not any(m and ("brain" in m or "llm" in m) for m in imported)


# ---------------------------------------------------------------------------
# 6. Voice independence: an avatar resolves the same way regardless of
#    what VoiceProfile a caller supplies or omits.
# ---------------------------------------------------------------------------


class TestVoiceIndependence:
    def test_presence_for_needs_no_voice_collaborator_at_all(self):
        """presence_for()'s own signature never accepts a voice profile,
        an activation, or an agent object it could reach one through -
        only agent_key/provider/recall/state/live (Stage 17.2's live
        presence signal - itself not a voice collaborator; see
        tests/test_stage17_2_digital_human.py for its own guarantees).
        That is the whole guarantee, made impossible to violate by
        accident."""
        import inspect

        params = set(inspect.signature(presence_for).parameters)
        assert params == {"agent_key", "provider", "recall", "state", "live"}

    def test_two_agents_with_different_declared_voices_still_get_independent_avatars(self):
        _, manager, _, _, _ = system()
        provider = DefaultAvatarProvider()
        alfred_voice = voice_for(manager.own_skills)
        arthur = manager.registry.get("Arthur")
        arthur_voice = voice_for(arthur)
        assert alfred_voice != arthur_voice  # sanity: they are genuinely different

        alfred_presence = presence_for(
            manager.own_skills.name,
            provider=provider,
            recall=memory_for(manager, manager.own_skills.name).recall,
        )
        arthur_presence = presence_for(
            "Arthur", provider=provider, recall=memory_for(manager, "Arthur").recall
        )
        assert alfred_presence.avatar != arthur_presence.avatar


# ---------------------------------------------------------------------------
# 7. AgentPresence: attaches to the canonical agent, never builds one.
# ---------------------------------------------------------------------------


class TestAgentPresence:
    def test_construction_requires_a_named_agent(self):
        with pytest.raises(ValueError):
            AgentPresence(agent_key="  ", avatar=AvatarProfile(avatar_id="a", label="A"))

    def test_defaults_to_idle_and_carries_a_real_avatar(self):
        presence = AgentPresence(agent_key="Alfred", avatar=AvatarProfile(avatar_id="a", label="A"))
        assert presence.state == PresenceState.IDLE

    def test_to_dict_is_plain_and_json_shaped(self):
        presence = AgentPresence(
            agent_key="Alfred",
            avatar=AvatarProfile(avatar_id="a", label="A"),
            state=PresenceState.LISTENING,
        )
        assert presence.to_dict() == {
            "agent_key": "Alfred",
            "avatar": {"avatar_id": "a", "label": "A", "body_type": "", "accent_color": "", "style": {}},
            "state": "listening",
        }

    def test_presence_for_reads_the_canonical_manager_without_constructing_anything(self):
        _, manager, _, _, _ = system()
        registry_size_before = len(manager.registry.all_agents())
        memory_before = manager.memory

        presence = presence_for(
            manager.own_skills.name,
            provider=DefaultAvatarProvider(),
            recall=manager.memory.recall,
        )

        assert isinstance(presence, AgentPresence)
        assert presence.agent_key == manager.own_skills.name
        assert len(manager.registry.all_agents()) == registry_size_before
        assert manager.memory is memory_before

    def test_presence_state_is_only_ever_what_the_caller_asserts(self):
        """State is accepted, never computed - presence_for() has no
        route to a Conversation, so it cannot discover SPEAKING/
        LISTENING on its own; a caller with real evidence must say so."""
        _, manager, _, _, _ = system()
        idle = presence_for(
            manager.own_skills.name, provider=DefaultAvatarProvider(), recall=manager.memory.recall
        )
        assert idle.state == PresenceState.IDLE

        speaking = presence_for(
            manager.own_skills.name,
            provider=DefaultAvatarProvider(),
            recall=manager.memory.recall,
            state=PresenceState.SPEAKING,
        )
        assert speaking.state == PresenceState.SPEAKING

    def test_the_control_center_snapshot_attaches_real_presence_to_every_agent(self):
        _, manager, _, _, centre = system()
        overview = centre.overview()
        for view in overview.agents:
            assert view.presence is not None
            assert isinstance(view.presence, AgentPresence)
            assert view.presence.agent_key == view.name


# ---------------------------------------------------------------------------
# 8. Conversation Room: independent presence per agent, and the speaking
#    agent's identity is representable (name -> its own avatar/state).
# ---------------------------------------------------------------------------


class TestConversationRoomPresence:
    def test_two_joined_agents_carry_independent_presence_and_avatars(self):
        _, manager, channel, conversation, centre = system()
        exchange(channel, conversation, "what can you do?", addressed="Arthur")

        overview = centre.overview()
        alfred_view = overview.agent(manager.own_skills.name)
        arthur_view = overview.agent("Arthur")

        assert alfred_view.presence.agent_key == manager.own_skills.name
        assert arthur_view.presence.agent_key == "Arthur"
        assert alfred_view.presence.avatar != arthur_view.presence.avatar

    def test_the_speaking_agents_name_is_recorded_and_resolves_to_its_own_presence(self):
        _, manager, channel, conversation, centre = system()
        exchange(channel, conversation, "what can you do?", addressed="Arthur")

        speaker = conversation.utterances[-1].speaker
        assert speaker == "Arthur"
        view = centre.overview().agent(speaker)
        assert view is not None
        assert view.presence.agent_key == speaker


# ---------------------------------------------------------------------------
# 9. Provider replacement: a synthetic AvatarProvider works with no
#    change anywhere else, and the factory seam is real.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OneAvatarProvider(AvatarProvider):
    """A minimal, synthetic provider - proof that nothing above depends
    on DefaultAvatarProvider specifically, only on the AvatarProvider
    contract."""

    only: AvatarProfile = AvatarProfile(avatar_id="only", label="Only")

    @property
    def name(self) -> str:
        return "one"

    def catalog(self) -> tuple[AvatarProfile, ...]:
        return (self.only,)

    def default_for(self, agent_key: str) -> AvatarProfile:
        return self.only


class TestAvatarProviderIsReplaceable:
    def test_a_synthetic_provider_works_through_the_same_presence_for_seam(self):
        _, manager, _, _, _ = system()
        presence = presence_for(
            manager.own_skills.name, provider=_OneAvatarProvider(), recall=manager.memory.recall
        )
        assert presence.avatar.avatar_id == "only"

    def test_agent_presence_never_imports_a_concrete_renderer(self):
        """Agent Presence -> AvatarProvider -> Renderer: the agent side
        must not know about a concrete renderer. There is no renderer
        module in this codebase yet; this pins that presence.py stays
        free of one the day it exists, by staying free of one now."""
        import ast

        tree = ast.parse(
            (REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8")
        )
        imported = {
            n.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for n in [node]
        }
        assert not any(m and "render" in m for m in imported)

    def test_default_is_registered_and_resolves_through_settings(self):
        assert "default" in available_avatar_providers()
        provider = build_avatar_provider(settings())
        assert isinstance(provider, DefaultAvatarProvider)

    def test_an_unknown_configured_provider_fails_fast(self):
        with pytest.raises(ValueError):
            build_avatar_provider(settings(avatar_provider="not-a-real-provider"))

    def test_runtime_carries_exactly_one_avatar_provider(self):
        runtime, *_ = system()
        assert isinstance(runtime.avatar_provider, AvatarProvider)
        assert "avatar_provider=default" in runtime.describe()


# ---------------------------------------------------------------------------
# 10. Cross-device: no phone-model, GPU or OS-specific assumptions
#     anywhere this phase touches.
# ---------------------------------------------------------------------------


class TestCrossDeviceHonesty:
    # "pixel" is deliberately excluded: devicePixelRatio/setPixelRatio are
    # legitimate, generic WebGL API terms this project's own 3D surfaces
    # already use, not a reference to the Google Pixel device line.
    DEVICE_NAMES = (
        "redmi", "iphone", "ipad", "samsung", "galaxy", "android",
    )

    def test_no_backend_avatar_code_names_a_device(self):
        for path in (
            REPO_ROOT / "src" / "providers" / "avatar.py",
            REPO_ROOT / "src" / "providers" / "default_avatar_provider.py",
            REPO_ROOT / "src" / "providers" / "avatar_factory.py",
            REPO_ROOT / "src" / "services" / "identity" / "presence.py",
        ):
            lowered = path.read_text("utf-8").lower()
            for device in self.DEVICE_NAMES:
                assert device not in lowered, f"{path.name} names a device: {device}"

    def test_the_interface_shell_names_no_device_model(self):
        lowered = UI.read_text("utf-8").lower()
        for device in self.DEVICE_NAMES:
            assert device not in lowered, f"ui/index.html names a device: {device}"

    def test_the_presence_facet_and_picker_use_relative_responsive_units(self):
        """Not a full responsive audit (out of scope for this phase) -
        only pins that the two Stage 17 additions (.pdot, .who-picker/
        .pick) introduce no fixed device-width media query of their
        own; the page's one existing phone breakpoint is untouched."""
        shell = UI.read_text("utf-8")
        for marker in (".pdot{", ".who-picker{", ".pick{"):
            block = shell.split(marker, 1)[1].split("}", 1)[0]
            assert "px)" not in block or "@media" not in shell.split(marker, 1)[0][-40:]


# ---------------------------------------------------------------------------
# 11. Live path: the explicit "to" selector reaches exchange()'s own
#     addressed override end to end, through the real HTTP-shaped
#     functions (no socket) - and the avatar write path the same way.
# ---------------------------------------------------------------------------


class TestLiveAvatarAndAddressingRoutes:
    def test_posting_to_selects_the_named_agent_end_to_end(self):
        from scripts.serve_ui import build_live_system, route_message

        live = build_live_system(settings())
        _, body, _ = route_message("what can you do?", live, to="Arthur")
        import json

        turn = json.loads(body)
        assert turn["handled_by"] == "Arthur"

    def test_posting_an_avatar_choice_is_visible_in_the_next_snapshot(self):
        from scripts.serve_ui import build_live_system, route, route_avatar

        live = build_live_system(settings())
        provider = live.runtime.avatar_provider
        current = presence_for(
            live.manager.own_skills.name, provider=provider, recall=live.manager.memory.recall
        ).avatar.avatar_id
        other = next(p.avatar_id for p in provider.catalog() if p.avatar_id != current)

        status, _, _ = route_avatar(live.manager.own_skills.name, other, live)
        assert status == 200

        import json

        _, body, _ = route("/snapshot.json", live)
        data = json.loads(body)
        alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
        assert alfred["avatar"]["avatar"]["avatar_id"] == other

    def test_an_unknown_agent_avatar_write_is_rejected_not_invented(self):
        from scripts.serve_ui import build_live_system, route_avatar

        live = build_live_system(settings())
        status, _, _ = route_avatar("Nobody", "warm_formal", live)
        assert status == 404
