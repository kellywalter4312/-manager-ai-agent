"""Stage 17.4 - the user-controlled Digital Human / Agent Presence
profile.

Stage 17.1 gave each agent exactly one configurable presentation
dimension: which AvatarProfile it uses. This stage's own brief asks for
the fuller shape that was always implied - appearance, body, posture,
clothing, idle/listening/speaking behaviour, expression and gesture
style - stored as user preference, never as a second Brain, Memory, or
Agent, and never claiming a renderer exists to act on any of it yet.

    AgentPresence
          +-- avatar: AvatarProfile           (Stage 17.1, unchanged)
          +-- profile: DigitalHumanProfile     (Stage 17.4, new,
                +-- avatar_id                   composed from TWO
                +-- appearance/body/posture/    governed preference
                    clothing/idle_behavior/     sources - never a
                    listening_behavior/         second database)
                    speaking_behavior/
                    expression_style/
                    gesture_style

This file proves the profile is real, per-agent, additive (nothing
Stage 17.1-17.3 already relied on changed shape), and honestly inert
where no renderer exists to consume it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.avatar import AvatarProfile, AvatarProvider
from src.providers.availability import Availability, AvailabilityState
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import (
    DIGITAL_HUMAN_STYLE_FIELDS,
    AgentPresence,
    DigitalHumanProfile,
    digital_human_profile_for,
    digital_human_style_for,
    presence_for,
    set_avatar_preference,
    set_digital_human_style,
)
from src.services.identity.voice import voice_for
from scripts.exchange import exchange, memory_for

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


def system(**overrides):
    runtime = build_runtime(settings(**overrides))
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    channel = build_channel(runtime, manager)
    conversation = build_conversation(manager)
    centre = ControlCenter(runtime, manager)
    return runtime, manager, channel, conversation, centre


# ---------------------------------------------------------------------------
# 1. DigitalHumanProfile: construction, validation, honest defaults.
# ---------------------------------------------------------------------------


class TestDigitalHumanProfile:
    def test_a_bare_avatar_id_is_enough_to_construct(self):
        p = DigitalHumanProfile(avatar_id="warm_formal")
        assert p.avatar_id == "warm_formal"
        assert all(getattr(p, f) == "" for f in DIGITAL_HUMAN_STYLE_FIELDS)

    def test_an_empty_avatar_id_is_rejected(self):
        with pytest.raises(ValueError):
            DigitalHumanProfile(avatar_id="   ")

    def test_avatar_id_is_normalised_to_lowercase(self):
        p = DigitalHumanProfile(avatar_id="  WARM_Formal  ")
        assert p.avatar_id == "warm_formal"

    def test_style_fields_are_whitespace_collapsed_not_lowercased(self):
        """Open, renderer-owned labels - the same "characteristics,
        never a rendering asset" contract as AvatarProfile.label,
        which is also never lowercased."""
        p = DigitalHumanProfile(avatar_id="a", posture="Standing   Tall")
        assert p.posture == "Standing Tall"

    def test_is_frozen(self):
        p = DigitalHumanProfile(avatar_id="a")
        with pytest.raises(Exception):
            p.posture = "seated"  # type: ignore[misc]

    def test_to_dict_is_plain_and_json_shaped(self):
        p = DigitalHumanProfile(avatar_id="a", posture="standing")
        d = p.to_dict()
        assert d["avatar_id"] == "a"
        assert d["posture"] == "standing"
        assert set(d) == {"avatar_id"} | set(DIGITAL_HUMAN_STYLE_FIELDS)

    def test_exactly_the_briefs_own_eleven_dimensions_no_more_no_less(self):
        """Pinned deliberately: this stage's own brief named nine
        dimensions beyond avatar_id. Stage 17.13 added a tenth
        (``symbolic_environment``) and Stage 17.15 an eleventh
        (``hair_style``) - each a real, reviewed decision (that phase's
        own explicit brief), not an accidental drift; this test was
        updated in step each time, not weakened."""
        assert set(DIGITAL_HUMAN_STYLE_FIELDS) == {
            "appearance", "body", "posture", "clothing",
            "idle_behavior", "listening_behavior", "speaking_behavior",
            "expression_style", "gesture_style", "symbolic_environment",
            "hair_style",
        }


# ---------------------------------------------------------------------------
# 2/3. Per-agent independence.
# ---------------------------------------------------------------------------


class TestPerAgentIndependence:
    def test_each_agent_starts_with_an_all_empty_style(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        assert digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall) == {
            f: "" for f in DIGITAL_HUMAN_STYLE_FIELDS
        }
        assert digital_human_style_for("Arthur", recall=arthur_memory.recall) == {
            f: "" for f in DIGITAL_HUMAN_STYLE_FIELDS
        }

    def test_changing_alfreds_profile_does_not_change_arthurs(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")

        ok = set_digital_human_style(
            manager.own_skills.name, {"posture": "standing", "clothing": "formal"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        assert ok

        alfred_style = digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)
        arthur_style = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert alfred_style["posture"] == "standing"
        assert arthur_style["posture"] == ""
        assert arthur_style == {f: "" for f in DIGITAL_HUMAN_STYLE_FIELDS}

    def test_repeated_style_writes_replace_rather_than_accumulate(self):
        """The identical guarantee Stage 17.1 already proved for
        avatar preference (test_choosing_again_replaces_rather_than_
        accumulates), re-proven for the style write path: reading the
        current value alone cannot catch this - recall() surfaces the
        newest record regardless of how many stale ones pile up
        underneath it - so this counts the tagged records directly."""
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)

        for posture in ("standing", "seated", "leaning"):
            set_digital_human_style(
                manager.own_skills.name, {"posture": posture},
                recall=memory.recall, remember=memory.remember, forget=memory.forget,
            )

        tagged = [
            r for r in memory.recall(limit=50)
            if (r.context or {}).get("fact_type") == "digital_human_profile"
        ]
        assert len(tagged) == 1
        assert tagged[0].context["posture"] == "leaning"

    def test_both_agents_can_be_independently_configured_at_once(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")

        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"posture": "seated"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )

        assert digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)["posture"] == "standing"
        assert digital_human_style_for("Arthur", recall=arthur_memory.recall)["posture"] == "seated"


# ---------------------------------------------------------------------------
# 4-9. Profile changes never touch Agent/Brain/Memory/VoiceProfile/
#      Capabilities/Connections.
# ---------------------------------------------------------------------------


class TestProfileChangeIsIsolated:
    def test_no_new_agent_brain_or_memory_is_constructed(self):
        runtime, manager, _, _, _ = system()
        registry_size_before = len(manager.registry.all_agents())
        brain_before = manager.brain
        memory_before = manager.memory
        skills_before = manager.own_skills

        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        assert len(manager.registry.all_agents()) == registry_size_before
        assert manager.brain is brain_before
        assert manager.memory is memory_before
        assert manager.own_skills is skills_before

    def test_voiceprofile_is_unchanged(self):
        _, manager, _, _, _ = system()
        skills = manager.own_skills
        voice_before = voice_for(skills)
        personality_before = personality_for(skills)

        memory = memory_for(manager, skills.name)
        set_digital_human_style(
            skills.name, {"expression_style": "warm", "gesture_style": "restrained"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        assert voice_for(skills) == voice_before
        assert personality_for(skills) == personality_before

    def test_capabilities_and_connection_are_unchanged(self):
        runtime, manager, _, _, centre = system()
        caps_before = tuple(
            c.name for c in centre.overview().agent(manager.own_skills.name).capabilities
        )
        connection_before = runtime.connection

        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"idle_behavior": "subtle breathing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        caps_after = tuple(
            c.name for c in centre.overview().agent(manager.own_skills.name).capabilities
        )
        assert caps_after == caps_before
        assert runtime.connection is connection_before

    def test_the_avatar_choice_itself_is_unaffected_by_a_style_change(self):
        """avatar_id and the other nine dimensions are two genuinely
        separate write paths - proving neither leaks into the other."""
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            manager.own_skills.name, "calm_composed", provider=runtime.avatar_provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        avatar = next(
            p for p in runtime.avatar_provider.catalog() if p.avatar_id == "calm_composed"
        )
        profile = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)
        assert profile.avatar_id == "calm_composed"
        assert profile.posture == "standing"

    def test_a_style_change_does_not_touch_the_stored_avatar_preference(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        from src.services.identity.presence import avatar_preference_for

        set_avatar_preference(
            manager.own_skills.name, "warm_formal", provider=runtime.avatar_provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            manager.own_skills.name, {"clothing": "formal"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert avatar_preference_for(manager.own_skills.name, recall=memory.recall) == "warm_formal"


# ---------------------------------------------------------------------------
# 10/11. LLM and Voice independence.
# ---------------------------------------------------------------------------


class TestLLMAndVoiceIndependence:
    def test_swapping_the_manager_brain_does_not_change_the_stored_profile(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = digital_human_style_for(manager.own_skills.name, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        assert after == before == {**{f: "" for f in DIGITAL_HUMAN_STYLE_FIELDS}, "posture": "standing"}

    def test_two_agents_with_different_voices_still_get_independent_profiles(self):
        _, manager, _, _, _ = system()
        arthur = manager.registry.get("Arthur")
        assert voice_for(arthur) != voice_for(manager.own_skills)

        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"posture": "seated"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        assert digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)["posture"] == "standing"
        assert digital_human_style_for("Arthur", recall=arthur_memory.recall)["posture"] == "seated"


# ---------------------------------------------------------------------------
# 12. Provider replacement: a synthetic AvatarProvider still composes a
#     real, correct DigitalHumanProfile with no change to canonical
#     Agent/Brain/Memory architecture.
# ---------------------------------------------------------------------------


class _OneAvatarProvider(AvatarProvider):
    def __init__(self):
        self._only = AvatarProfile(avatar_id="only", label="Only")

    @property
    def name(self) -> str:
        return "one"

    def catalog(self):
        return (self._only,)

    def default_for(self, agent_key: str):
        return self._only


class TestProviderReplacement:
    def test_a_synthetic_provider_still_composes_a_real_profile(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        presence = presence_for(
            manager.own_skills.name, provider=_OneAvatarProvider(), recall=memory.recall
        )
        assert presence.profile.avatar_id == "only"
        assert presence.profile.posture == "standing"

    def test_switching_providers_does_not_touch_registry_brain_or_memory(self):
        runtime, manager, _, _, _ = system()
        registry_size_before = len(manager.registry.all_agents())
        brain_before = manager.brain
        memory_before = manager.memory

        presence_for(manager.own_skills.name, provider=_OneAvatarProvider(), recall=manager.memory.recall)

        assert len(manager.registry.all_agents()) == registry_size_before
        assert manager.brain is brain_before
        assert manager.memory is memory_before


# ---------------------------------------------------------------------------
# 13. Unknown values rejected honestly.
# ---------------------------------------------------------------------------


class TestValidationIsHonest:
    def test_an_unknown_field_name_is_rejected_and_writes_nothing(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        before = len(memory.recall(limit=50))

        ok = set_digital_human_style(
            manager.own_skills.name, {"made_up_dimension": "x"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        assert ok is False
        assert len(memory.recall(limit=50)) == before

    def test_an_empty_value_is_rejected_and_writes_nothing(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        before = len(memory.recall(limit=50))

        ok = set_digital_human_style(
            manager.own_skills.name, {"posture": "   "},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        assert ok is False
        assert len(memory.recall(limit=50)) == before

    def test_a_mixed_batch_with_one_bad_field_rejects_the_whole_write(self):
        """Atomic, not partial - a batch is accepted or refused whole,
        never silently applying the good half."""
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)

        ok = set_digital_human_style(
            manager.own_skills.name, {"posture": "standing", "made_up": "x"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        assert ok is False
        assert digital_human_style_for(manager.own_skills.name, recall=memory.recall)["posture"] == ""

    def test_avatar_id_is_not_a_valid_style_field(self):
        """The two write paths stay genuinely separate: avatar_id can
        never be smuggled through set_digital_human_style()."""
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)

        ok = set_digital_human_style(
            manager.own_skills.name, {"avatar_id": "warm_formal"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is False


# ---------------------------------------------------------------------------
# 14/15/16. Existing Stage 17.1-17.3 behaviour remains intact.
# ---------------------------------------------------------------------------


class _ScriptedSpeaker(SpeechAdapter):
    name = "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout=None):
        raise NotImplementedError

    def speak(self, text, voice=None):
        return True


class TestExistingStage17BehaviourIntact:
    def test_agent_selection_still_resolves_through_the_canonical_registry(self):
        """Neutral text ("what can you do?" - Alfred's own ordinary
        text, nothing Arthur-shaped in it) isolates the explicit
        ``addressed`` mechanism from natural capability routing - the
        same isolation Stage 17.3's own equivalent test needed, so
        this genuinely proves the selection mechanism, not just that
        Arthur happened to answer anyway."""
        _, manager, channel, conversation, _ = system()
        turn = exchange(channel, conversation, "what can you do?", addressed="Arthur")
        assert turn.handled_by == "Arthur"
        assert manager.registry.get("Arthur") is not None

    def test_speaking_still_synchronises_through_the_real_deliver_call(self):
        from src.services.identity.presence import PresenceState

        runtime, manager, _, conversation, _ = system()
        real_channel = VoiceChannel(
            manager, speech=_ScriptedSpeaker(), presence=runtime.presence_registry
        )
        exchange(real_channel, conversation, "what can you do?", speak=True)
        # Momentary but real for the shipped scripted speaker; must
        # settle back to IDLE, exactly as Stage 17.2/17.3 already proved.
        assert runtime.presence_registry.get(manager.own_skills.name) is None

    def test_avatar_preference_persistence_still_works_unchanged(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        from src.services.identity.presence import avatar_preference_for

        ok = set_avatar_preference(
            manager.own_skills.name, "calm_composed", provider=runtime.avatar_provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok
        assert avatar_preference_for(manager.own_skills.name, recall=memory.recall) == "calm_composed"

    def test_presence_for_still_returns_a_fully_backward_compatible_agentpresence(self):
        """A caller that never heard of Stage 17.4 (constructs
        AgentPresence directly with no profile=) is unaffected; the
        to_dict() shape it already relies on gains no new required
        key."""
        p = AgentPresence(agent_key="Alfred", avatar=AvatarProfile(avatar_id="a", label="A"))
        assert p.profile is None
        assert "profile" not in p.to_dict()


# ---------------------------------------------------------------------------
# 17/18. Mobile/laptop responsiveness and device independence.
# ---------------------------------------------------------------------------


class TestDeviceIndependence:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_device_name_anywhere_in_this_phases_backend_additions(self):
        for rel in (
            "src/services/identity/presence.py",
            "scripts/serve_ui.py",
            "scripts/control_center.py",
        ):
            lowered = (REPO_ROOT / rel).read_text("utf-8").lower()
            for device in self.DEVICE_NAMES:
                assert device not in lowered, f"{rel} names a device: {device}"

    def test_the_interface_shell_still_names_no_device(self):
        lowered = UI.read_text("utf-8").lower()
        for device in self.DEVICE_NAMES:
            assert device not in lowered, f"ui/index.html names a device: {device}"

    def test_the_new_presentation_pair_uses_no_new_fixed_width_media_query(self):
        """Renamed "Digital Human" by Stage 17.6A, merged with the
        avatar picker into one "Avatar Studio" panel by a later
        correction pass (still Stage 17.11's own surface - see that
        phase's own test suite) - still device-independent: no new
        @media(...px...) breakpoint, responsive purely through the
        grid/flex layout itself."""
        shell = UI.read_text("utf-8")
        block = shell.split('class="k">Avatar Studio', 1)[1].split("</div>\n          </div>", 1)[0]
        assert "px)" not in block


# ---------------------------------------------------------------------------
# No pretending: this phase does not claim a renderer exists.
# ---------------------------------------------------------------------------


class TestNoPretending:
    def test_no_3d_or_animation_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text

    def test_the_workspace_never_offers_a_catalog_picker_for_the_open_style_fields(self):
        """Only avatar_id has a real catalog (AvatarProvider) - a
        catalog-style .pick button naming one of a fixed, real set of
        AvatarProvider choices must never appear for the nine open
        DigitalHumanProfile fields.

        Stage 17.6A superseded this test's own original, narrower
        claim ("must stay read-only"); Stage 17.11 then gave four of
        those nine fields (posture/body/idle_behavior/gesture_style) a
        real curated .pick-button menu of their own, once the renderer
        genuinely drew each differently - not the fabrication this
        test exists to forbid, because each option is a real,
        already-accepted DigitalHumanProfile string, never an
        AvatarProvider catalog entry (contrast ``data-avatar-id``,
        which only the avatar_id picker ever writes). A later
        correction pass merged the avatar_id picker and the style
        field grid into one visual "Avatar Studio" panel, so this test
        now scopes to the ``.dh-fields`` grid specifically - the one
        boundary that still cleanly separates "the nine open fields"
        from avatar_id's own, separate, real catalog.
        """
        shell = UI.read_text("utf-8")
        block = shell.split('<div class="dh-fields" data-dh-agent', 1)[1].split(
            "\n            </div>\n          </div>", 1
        )[0]
        assert "data-avatar-id" not in block
        assert "avatar-picker" not in block

    def test_presentation_intent_gesture_seam_is_still_unwired(self):
        """A Stage 17.2 guarantee this phase must not accidentally
        start violating: nothing in the manager/agent layer
        constructs a PresentationIntent, and DigitalHumanProfile does
        not merge with or replace it - they remain two separate,
        still-mostly-inert seams."""
        for rel in ("src/manager", "src/agents"):
            for path in (REPO_ROOT / rel).rglob("*.py"):
                assert "PresentationIntent" not in path.read_text("utf-8")
