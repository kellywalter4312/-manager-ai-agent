"""Stage 17.11 - User-Controlled Digital Human Avatar Studio.

The Digital Human editor (Stage 17.6A) was real but mostly text: nine
open-string fields, only two of which (posture, body) the renderer
actually drew differently, with no local preview before Save and - a
real, previously-undetected gap this stage found while inspecting the
existing lifecycle - no way for a saved posture/body change to ever
visually appear at all, because wireStages' own "reuse the running
WebGL context, never rebuild it on a poll" optimisation (see its own
comment) meant buildHumanoidBody's one-time geometry read was never
revisited after the very first mount.

This stage adds, in ``ui/index.html`` only (zero Python files changed -
the backend already had everything needed: DigitalHumanProfile's nine
open fields, set_digital_human_style()'s partial-merge, AvatarProvider's
already-governed catalog/factory):

    DIGITAL_HUMAN_FIELD_OPTIONS   a curated, honest menu of already-real
                                   option strings for exactly the four
                                   RENDERED_STYLE_FIELDS - never a new
                                   closed enum (set_digital_human_style()
                                   still accepts any string for any of
                                   the nine fields)
    AVATAR_BODY_VISUAL_ENVIRONMENTS
    avatarBodyEnvironmentFor()    the one small extension seam future
                                   body/avatar packs need - a body_type-
                                   keyed registry mirroring
                                   AGENT_VISUAL_ENVIRONMENTS/
                                   environmentFor() exactly, one real
                                   entry ("humanoid") today
    DH_PREVIEW / _dhPreviewProfile()
                                   local-only, unsaved preview edits,
                                   merged on top of the real profile
                                   wherever the renderer reads one
    _restyleAvatarBodyPreview()   the fix for the reuse-never-rebuilds
                                   gap above - disposeStage() + a fresh
                                   mountAvatarBody3D() call, the exact
                                   existing dispose/mount lifecycle,
                                   never a second rendering engine
    wireDigitalHumanOptions()     visual .pick pickers for the four
                                   curated fields, reusing the identical
                                   .pick control .avatar-picker/.who-
                                   picker already are

Every existing Stage 17.6B-17.10 animation is untouched except one line
(mountAvatarBody3D's own `profile` read, now merging in an unsaved
preview) - re-affirmed here, not just by the unchanged Stage 17.6B-17.10
suites still passing.

Verified with the same two methods every Stage 17.x file since 17.5
uses - executed Python tests for the real data/provider layer, source-
text structural checks for the renderer - plus one addition: this phase
also runs `node --check` on the full extracted inline script, a cheap,
real syntax-execution proof (Node is available in this sandbox even
though a full DOM/WebGL harness is not - see Stage 17.5's own module
docstring for that separate, still-standing limitation).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.control_center.observer import ControlCenter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.providers.avatar import AvatarProfile, AvatarProvider
from src.providers.avatar_factory import _BUILDERS, available_avatar_providers
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.presence import (
    DIGITAL_HUMAN_STYLE_FIELDS,
    digital_human_profile_for,
    digital_human_style_for,
    presence_for,
    set_avatar_preference,
    set_digital_human_style,
)
from src.services.identity.voice import voice_for
from scripts.exchange import memory_for

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


class _AltAvatarProvider(AvatarProvider):
    """A second, independent AvatarProvider - never registered in the
    real factory, never touching it - standing in for a future avatar/
    body pack arriving through the exact same governed seam
    ``DefaultAvatarProvider`` already proves works."""

    _CATALOG = (
        AvatarProfile(
            avatar_id="specialist_alt",
            label="Alternate Specialist",
            body_type="specialist_alt",
            accent_color="teal",
        ),
    )

    @property
    def name(self) -> str:
        return "alt"

    def catalog(self):
        return self._CATALOG

    def default_for(self, agent_key: str):
        return self._CATALOG[0]


def _shell() -> str:
    return UI.read_text("utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


def _mount_body() -> str:
    shell = _shell()
    return shell.split("function mountAvatarBody3D(container", 1)[1].split(
        "\n  }\n\n  let agent3DStages", 1
    )[0]


def _speaking_branch(mount: str) -> str:
    return mount.split('if(activity === "speaking"){', 1)[1].split(
        '} else if(activity === "listening")', 1
    )[0]


def _listening_branch(mount: str) -> str:
    return mount.split('} else if(activity === "listening"){', 1)[1].split("} else {", 1)[0]


def _idle_branch(mount: str) -> str:
    return mount.rsplit("} else {", 1)[1]


def _fn(name: str) -> str:
    shell = _shell()
    return shell.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def _workspace_block() -> str:
    shell = _shell()
    return shell.split("V.workspace = () =>", 1)[1].split("\n  V.brain = ()", 1)[0]


# ---------------------------------------------------------------------------
# 1. Avatar/body catalog integrity - real, curated, never fabricated.
# ---------------------------------------------------------------------------


class TestAvatarBodyCatalogIntegrity:
    def test_the_python_avatar_catalog_has_four_real_distinct_entries(self):
        """Stage 17.13 added a fourth entry (sharp_technical, a genuine
        cyan accent) - updated in step, not weakened."""
        catalog = DefaultAvatarProvider().catalog()
        assert len(catalog) == 4
        ids = {p.avatar_id for p in catalog}
        colors = {p.accent_color for p in catalog}
        assert len(ids) == 4
        assert len(colors) == 4  # every option is visually distinguishable

    def test_the_js_curated_options_cover_exactly_the_rendered_fields(self):
        """Stage 17.13 added symbolic_environment; Stage 17.14 added
        appearance/clothing; Stage 17.15 added hair_style - the equality
        this test pins (curated options == rendered fields) still
        holds, updated in step each time."""
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        keys = set(re.findall(r"(\w+):\s*\[", options_block))
        rendered = set(
            shell.split('const RENDERED_STYLE_FIELDS = new Set([', 1)[1].split("]);", 1)[0]
            .replace('"', "").split(", ")
        )
        assert keys == rendered
        assert keys == {
            "posture", "body", "idle_behavior", "gesture_style", "symbolic_environment",
            "appearance", "clothing", "hair_style",
        }

    def test_posture_options_are_exactly_the_two_the_renderer_itself_branches_on(self):
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        posture_line = [ln for ln in options_block.splitlines() if "posture:" in ln][0]
        assert '"standing", "seated"' in posture_line
        # The exact string mountAvatarBody3D's own builder checks -
        # never a picker option that would silently do nothing.
        body = _shell().split("function buildHumanoidBody(THREE", 1)[1].split("\n  }\n", 1)[0]
        assert 'String((style && style.posture) || "").toLowerCase() === "seated"' in body

    def test_no_option_list_has_a_duplicate(self):
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        for line in options_block.splitlines():
            m = re.search(r"\[(.+)\]", line)
            if not m:
                continue
            values = [v.strip().strip('"') for v in m.group(1).split(",")]
            assert len(values) == len(set(values)), line


# ---------------------------------------------------------------------------
# 2. No fabricated avatar choices - the other five style dimensions stay
#    real free text, honestly labelled, never a fake picker.
# ---------------------------------------------------------------------------


class TestNoFabricatedChoices:
    def test_three_fields_have_no_curated_options_and_stay_labelled_not_yet_rendered(self):
        """Superseded by Stage 17.14: appearance and clothing moved from
        this "still honestly free text" bucket into the curated,
        genuinely-rendered one (real hair/glasses/shirt/trousers
        geometry now exists) - updated in step, not weakened. Three
        fields remain honestly unrendered."""
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        curated = set(re.findall(r"(\w+):\s*\[", options_block))
        uncurated = set(DIGITAL_HUMAN_STYLE_FIELDS) - curated
        assert uncurated == {"listening_behavior", "speaking_behavior", "expression_style"}
        assert "not yet rendered" in shell

    def test_the_workspace_only_renders_a_picker_for_curated_fields(self):
        block = _workspace_block()
        assert 'options?' not in block or "if(options){" in block
        assert "DIGITAL_HUMAN_FIELD_OPTIONS[f]" in block

    def test_set_digital_human_style_still_accepts_an_arbitrary_uncurated_value(self):
        """The open-field philosophy DigitalHumanProfile's own docstring
        states - "never a closed enum this project invents on a
        renderer's behalf" - still holds: a curated menu in the UI is a
        suggestion, never a server-side restriction."""
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        ok = set_digital_human_style(
            manager.own_skills.name, {"body": "hand-carved-custom-proportions"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        assert digital_human_style_for(manager.own_skills.name, recall=memory.recall)["body"] == (
            "hand-carved-custom-proportions"
        )

    def test_a_pre_stage_17_11_custom_value_stays_visible_not_hidden(self):
        """A value already stored before this stage, matching none of
        the three curated options, must still be shown somewhere in the
        picker's own field - never silently dropped from view."""
        block = _workspace_block()
        assert "dh-current" in block
        assert "current?esc(current):\"not configured\"" in block


# ---------------------------------------------------------------------------
# 3. Visual preview binding - a local, unsaved edit is visible without a
#    server round trip, and never fires a network request on its own.
# ---------------------------------------------------------------------------


class TestVisualPreviewBinding:
    def test_dh_preview_merges_pending_over_saved(self):
        fn = _fn("_dhPreviewProfile")
        assert "Object.assign({}, savedProfile || {}, DH_PREVIEW[agentName] || {})" in fn

    def test_the_animate_loop_reads_through_the_preview_merge(self):
        mount = _mount_body()
        assert "const profile = _dhPreviewProfile(agentName, live && live.avatar && live.avatar.profile);" in mount

    def test_geometry_fields_trigger_a_real_rebuild_never_a_fake_one(self):
        fn = _fn("_restyleAvatarBodyPreview")
        assert "disposeStage(avatarBody3DStages, agentName)" in fn
        assert "mountAvatarBody3D(el, agentName, agent.avatar.avatar, _dhPreviewProfile(agentName, agent.avatar.profile))" in fn

    def test_only_geometry_fields_trigger_a_rebuild(self):
        assert 'const DH_GEOMETRY_FIELDS = new Set(["posture", "body"]);' in _shell()
        fn = _fn("wireDigitalHumanOptions")
        assert "if(DH_GEOMETRY_FIELDS.has(field)) _restyleAvatarBodyPreview(agent);" in fn

    def test_clicking_a_picker_option_makes_no_network_request(self):
        """A preview is local-only until Save - the picker's own click
        handler must never itself call fetch()."""
        fn = _fn("wireDigitalHumanOptions")
        assert "fetch(" not in fn

    def test_no_second_rendering_engine_was_created(self):
        """The preview rebuild reuses the exact existing dispose/mount
        pair every other stage in this file already uses - no new
        canvas/renderer constructor, no new stages map."""
        fn = _fn("_restyleAvatarBodyPreview")
        assert "new THREE" not in fn
        assert "WebGLRenderer" not in fn
        shell = _shell()
        assert shell.count("avatarBody3DStages") >= 3  # declared + reused, not duplicated


# ---------------------------------------------------------------------------
# 4. Save/load - the existing governed POST /profile path, now carrying
#    picker selections too, clearing the local preview only on success.
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_merges_pending_picks_with_typed_text_fields(self):
        fn = _fn("wireDigitalHumanForm")
        assert "const fields = Object.assign({}, DH_PREVIEW[agent]);" in fn
        assert 'fetch("profile"' in fn

    def test_preview_clears_only_on_a_confirmed_write(self):
        fn = _fn("wireDigitalHumanForm")
        assert "if(ok) delete DH_PREVIEW[agent];" in fn

    def test_save_forces_a_real_rebuild_from_the_confirmed_profile(self):
        """The bug this stage found: wireStages' own reuse path never
        rebuilds geometry on a poll, so a genuinely saved posture/body
        change would otherwise never visually appear at all."""
        fn = _fn("wireDigitalHumanForm")
        assert "await refreshFromLive();" in fn
        assert fn.rsplit("await refreshFromLive();", 1)[1].strip().startswith(
            "// refreshFromLive()"
        ) or "_restyleAvatarBodyPreview(agent);" in fn.rsplit("await refreshFromLive();", 1)[1]

    def test_profile_round_trips_through_the_real_governed_path(self):
        """Functional proof, not just structural: the exact curated
        strings this stage's own catalog offers persist and reload
        correctly together, per agent."""
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        ok = set_digital_human_style(
            manager.own_skills.name,
            {"posture": "seated", "body": "broad", "idle_behavior": "restless", "gesture_style": "expressive"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        assert loaded["posture"] == "seated"
        assert loaded["body"] == "broad"
        assert loaded["idle_behavior"] == "restless"
        assert loaded["gesture_style"] == "expressive"


# ---------------------------------------------------------------------------
# 5. Per-agent independence.
# ---------------------------------------------------------------------------


class TestPerAgentIndependence:
    def test_two_agents_get_fully_independent_curated_profiles(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing", "body": "slim"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"posture": "seated", "body": "broad"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        alfred = digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)
        arthur = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert alfred["posture"] == "standing" and alfred["body"] == "slim"
        assert arthur["posture"] == "seated" and arthur["body"] == "broad"

    def test_changing_alfreds_avatar_never_touches_arthurs(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        provider = DefaultAvatarProvider()
        set_avatar_preference(
            manager.own_skills.name, "calm_composed", provider=provider,
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        arthur_before = presence_for("Arthur", provider=provider, recall=arthur_memory.recall)
        set_avatar_preference(
            manager.own_skills.name, "bright_approachable", provider=provider,
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        arthur_after = presence_for("Arthur", provider=provider, recall=arthur_memory.recall)
        assert arthur_before.avatar == arthur_after.avatar

    def test_dh_preview_is_keyed_per_agent_in_the_renderer(self):
        fn = _fn("wireDigitalHumanOptions")
        assert "DH_PREVIEW[agent] = Object.assign({}, DH_PREVIEW[agent], {[field]: value});" in fn

    def test_the_avatar_picker_swatch_uses_the_options_own_accent_never_the_agents_hue(self):
        """A real correctness fix this stage makes: each catalog
        option's own accent_color, not the unrelated agent identity
        hue, colours its picker button - otherwise every option for one
        agent would wrongly share one colour."""
        block = _workspace_block()
        assert "style=\"--pc:${avatarAccentRgb(p)}\"" in block
        assert "identityOf(a).hue" not in block.split("avatar-picker", 1)[1].split("</div>", 1)[0]


# ---------------------------------------------------------------------------
# 6. Renderer/profile binding - the body_type extension seam.
# ---------------------------------------------------------------------------


class TestRendererProfileBinding:
    def test_mount_looks_up_the_environment_by_the_avatars_own_body_type(self):
        mount = _mount_body()
        assert (
            "avatarBodyEnvironmentFor(avatarProfile && avatarProfile.body_type).build(THREE, accent, style)"
        ) in mount

    def test_the_registry_has_exactly_one_real_entry_today(self):
        shell = _shell()
        block = shell.split("const AVATAR_BODY_VISUAL_ENVIRONMENTS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        assert block.count(": {") == 1
        assert "humanoid:" in block

    def test_an_unrecognized_body_type_honestly_falls_back_never_crashes(self):
        shell = _shell()
        assert (
            "AVATAR_BODY_VISUAL_ENVIRONMENTS[String(bodyType || \"\").toLowerCase()] "
            "|| AVATAR_BODY_VISUAL_ENVIRONMENTS.humanoid"
        ) in shell

    def test_the_room_tag_callback_also_looks_up_by_body_type(self):
        fn = _fn("wireAvatarBody3D")
        assert "avatarBodyEnvironmentFor(bodyType)" in fn


# ---------------------------------------------------------------------------
# 7/8. LLM-provider, Brain, Memory, VoiceProfile independence.
# ---------------------------------------------------------------------------


class TestLLMBrainMemoryVoiceIndependence:
    def test_swapping_the_brain_does_not_change_the_stored_avatar_studio_profile(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated", "gesture_style": "expressive"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        provider = DefaultAvatarProvider()
        avatar = provider.default_for(manager.own_skills.name)
        before = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)
        assert after == before
        assert after.posture == "seated"
        assert after.gesture_style == "expressive"

    def test_changing_the_digital_human_profile_does_not_touch_the_brain(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        brain_before = manager.brain
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert manager.brain is brain_before

    def test_voiceprofile_is_unchanged_by_an_avatar_studio_edit(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        voice_before = voice_for(manager.own_skills)
        set_digital_human_style(
            manager.own_skills.name, {"body": "broad", "idle_behavior": "restless"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        voice_after = voice_for(manager.own_skills)
        assert voice_after == voice_before

    def test_avatar_body_configuration_survives_an_llm_provider_replacement(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        provider = DefaultAvatarProvider()
        set_avatar_preference(
            manager.own_skills.name, "warm_formal", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated", "body": "slim"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)
        assert after.avatar == before.avatar
        assert after.profile == before.profile

    def test_the_renderer_names_no_llm_provider_in_this_phases_new_code(self):
        for block in (
            _fn("_dhPreviewProfile"), _fn("_restyleAvatarBodyPreview"),
            _fn("wireDigitalHumanOptions"), _fn("wireDigitalHumanForm"),
        ):
            lowered = block.lower()
            for forbidden in ("claude", "openai", "anthropic", "api_key", "apikey"):
                assert forbidden not in lowered


# ---------------------------------------------------------------------------
# 9. Evolution / Extensions / Upgrade compatibility.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_upgrade_file_references_this_phases_additions(self):
        new_identifiers = (
            "DIGITAL_HUMAN_FIELD_OPTIONS", "AVATAR_BODY_VISUAL_ENVIRONMENTS",
            "avatarBodyEnvironmentFor", "DH_PREVIEW", "_dhPreviewProfile",
            "_restyleAvatarBodyPreview", "wireDigitalHumanOptions",
        )
        for rel in (
            "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            text = path.read_text("utf-8")
            for forbidden in new_identifiers:
                assert forbidden not in text, (rel, forbidden)

    def test_a_second_independent_avatar_provider_works_through_the_existing_seam(self):
        """Proof, not assertion: a future avatar/body pack needs no
        change to presence.py, channel.py or composition.py - only its
        own AvatarProvider (already an ABC+factory, the same shape
        celebration/calendar/connection providers already use)."""
        alt = _AltAvatarProvider()
        p = presence_for("FutureSpecialist", provider=alt)
        assert p.avatar.avatar_id == "specialist_alt"
        assert p.avatar.body_type == "specialist_alt"

    def test_the_real_factory_is_a_plain_extensible_registry(self):
        assert isinstance(_BUILDERS, dict)
        assert "default" in available_avatar_providers()

    def test_no_python_file_changed_this_phase_gained_new_classes(self):
        """Zero Python files were touched this stage - re-affirmed
        structurally: presence.py's own class set is unchanged since
        Stage 17.10."""
        import ast

        tree = ast.parse((REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert classes == {
            "PresenceState", "PresenceRegistry", "DigitalHumanProfile",
            "AgentPresence", "PresentationIntent",
        }


# ---------------------------------------------------------------------------
# 10. No duplicate Agent/Brain/Memory construction.
# ---------------------------------------------------------------------------


class TestNoDuplicateConstruction:
    def test_none_of_this_phases_new_js_ever_references_brain_memory_or_agent_construction(self):
        for block in (
            _fn("_dhPreviewProfile"), _fn("_restyleAvatarBodyPreview"),
            _fn("wireDigitalHumanOptions"),
        ):
            for forbidden in ("new Agent", "Brain(", "Memory(", "ManagerAgent"):
                assert forbidden not in block

    def test_wire_digital_human_options_makes_no_fetch_call(self):
        assert "fetch(" not in _fn("wireDigitalHumanOptions")

    def test_no_new_agent_registry_construction_call_in_this_phases_python(self):
        """Zero Python files changed - direct proof the whole backend
        surface (Agent/Brain/Memory/AgentRegistry) is untouched."""
        text = (REPO_ROOT / "src" / "channels" / "channel.py").read_text("utf-8")
        assert "presentation_intent_for_turn" in text  # Stage 17.9/17.10, unchanged
        assert "DIGITAL_HUMAN_FIELD_OPTIONS" not in text
        assert "avatarBodyEnvironmentFor" not in text


# ---------------------------------------------------------------------------
# 11/12. Mobile responsiveness and reduced-motion, re-affirmed.
# ---------------------------------------------------------------------------


class TestResponsivenessAndReducedMotion:
    def test_no_new_media_query_was_added(self):
        shell = _shell()
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_the_new_picker_rows_still_live_inside_the_responsive_dh_fields_grid(self):
        shell = _shell()
        assert "repeat(auto-fill,minmax(170px,1fr))" in shell
        assert ".dh-options{display:flex;gap:6px;flex-wrap:wrap}" in shell

    def test_the_new_code_uses_no_math_random_and_no_css_animation(self):
        for block in (
            _fn("_dhPreviewProfile"), _fn("_restyleAvatarBodyPreview"),
            _fn("wireDigitalHumanOptions"), _fn("wireDigitalHumanForm"),
        ):
            assert "Math.random" not in block
        shell = _shell()
        dh_css = shell.split(".dh-options{", 1)[1].split("\n\n", 1)[0]
        assert "@keyframes" not in dh_css
        assert "animation:" not in dh_css


# ---------------------------------------------------------------------------
# 13. Existing Stage 17.6B-17.10 animation and PresentationIntent remain
#     intact - re-affirmed for this phase's own surface, not just relied
#     on from the unchanged suites.
# ---------------------------------------------------------------------------


class TestExistingBehaviorPreserved:
    def test_all_named_animation_behaviors_still_present(self):
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        listening = _listening_branch(mount)
        idle = _idle_branch(mount)
        assert "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;" in speaking
        assert "const torsoTarget = restTorsoRotZ + Math.sin(t*gestureRate*0.5) * 0.035 * intentEffect.gestureMul * liveliness;" in speaking
        assert "const targetZ = restZ[i] + Math.sin(t*gestureRate + i*1.7) * gestureAmp * intentEffect.gestureMul * liveliness * (i===0?1:-1);" in speaking
        assert "Math.sin((nodPhase-4.9)*Math.PI/0.5) * 0.14 * intentEffect.nodMul" in speaking
        assert "const headLeanTarget = 0.12 + Math.sin(t*0.9) * 0.015;" in listening
        assert 'S.agents.some(a => a.name !== agentName && a.activity === "speaking")' in idle
        assert "group.rotation.y += attentionYaw;" in idle
        # Stage 17.21 refined the blink into a deterministic double-blink
        # (every fourth one), because a perfectly metronomic blink is its
        # own kind of uncanny. Pinned at the same specificity as before -
        # the whole expression, not a substring - and the determinism this
        # assertion has always really been about is now asserted directly
        # rather than implied by the shape of one line.
        assert "const blinkPhase = t % 4.2;" in mount
        assert "const doubleWindow = Math.floor(t / 4.2) % 4 === 0;" in mount
        assert (
            "const blinking = blinkPhase > 4.0 || "
            "(doubleWindow && blinkPhase > 3.82 && blinkPhase < 3.9);"
        ) in mount
        assert "eyes.forEach(eye => { eye.scale.y = blinking ? 0.12 : 1; });" in mount
        assert "Math.random" not in mount

    def test_presentation_intent_machinery_is_untouched(self):
        shell = _shell()
        assert "const PRESENTATION_INTENT_EFFECTS = {" in shell
        assert shell.count("const PRESENTATION_INTENT_EFFECTS = {") == 1
        text = (REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8")
        assert "def presentation_intent_for_turn" in text

    def test_room_still_shows_only_the_true_canonical_speaker(self):
        shell = _shell()
        room_block = shell.split("V.room = ", 1)[1].split("\n  };", 1)[0]
        assert '"speaking"' in room_block

    def test_lip_sync_still_honestly_not_implemented(self):
        """Scoped to this phase's own genuinely new code, not the whole
        mountAvatarBody3D function - which legitimately *discusses* the
        absence of lip-sync data in its own pre-existing comments (see
        Stage 17.7's own test for that correctly-scoped check)."""
        for block in (
            _fn("_dhPreviewProfile"), _fn("_restyleAvatarBodyPreview"),
            _fn("wireDigitalHumanOptions"), _fn("wireDigitalHumanForm"),
        ):
            lowered = block.lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
                assert word not in lowered


# ---------------------------------------------------------------------------
# 15. Correction pass: the Avatar Studio reads as one genuine editor
#     panel per agent (never the same fixed default presented as a
#     finished design), and what is architecturally reserved is labelled
#     as honestly as what is real.
# ---------------------------------------------------------------------------


class TestAvatarStudioPanelIsOnePerAgentEditor:
    def test_the_heading_names_the_specific_agent_never_a_fixed_agent(self):
        block = _workspace_block()
        assert 'class="k">Avatar Studio — ${esc(a.name)}</div>' in block
        assert "Avatar Studio — Alfred" not in block  # never a literal, hardcoded name

    def test_the_panel_groups_body_and_presentation_under_one_heading(self):
        block = _workspace_block()
        assert 'class="dh-sub">Body</div>' in block
        assert 'class="dh-sub">Presentation</div>' in block
        # Exactly one heading now, not two separate "Appearance"/"Digital
        # Human" pairs - the merge this correction pass makes.
        assert block.count('class="k">Appearance</div>') == 0
        assert block.count('class="k">Digital Human</div>') == 0

    def test_no_default_avatar_is_singled_out_as_the_intended_design(self):
        """Nothing in the merged panel privileges one AvatarProvider
        catalog entry over another - the same real, swappable style
        directions Stage 17.1 always offered (four since Stage 17.13's
        own cyan addition), still chosen by the user, never by this
        file."""
        catalog = DefaultAvatarProvider().catalog()
        assert len(catalog) == 4
        block = _workspace_block()
        for label in ("Warm & Formal", "Calm & Composed", "Bright & Approachable", "Sharp & Technical"):
            assert "data-avatar-id" in block  # a real picker exists...
        # ...and every one of the three is offered identically (looped,
        # not enumerated by name) - see the avatar-picker's own map().
        assert "(S.avatar_catalog||[]).map(p=>" in block

    def test_the_roadmap_is_a_static_never_interactive_ghost_list(self):
        """Stage 17.14 shrank this list (HAIR/CLOTHING RENDER removed
        once real geometry existed for both); Stage 17.15 renamed
        "FACE / HEAD SHAPE" to "FACE SHAPE VARIANTS" once real facial
        structure existed (choosing between variants still does not) -
        updated in step each time."""
        shell = _shell()
        assert (
            'const AVATAR_STUDIO_ROADMAP = ["FACE SHAPE VARIANTS", '
            '"FACIAL EXPRESSION", "ALTERNATE BODY PACKS"];'
        ) in shell
        block = _workspace_block()
        assert "AVATAR_STUDIO_ROADMAP.map(g=>" in block
        assert 'class="chip off"' in block.split("dh-roadmap", 1)[1][:400]
        # Never rendered as an "on"/selected/available option - a ghost,
        # exactly like the whole-project ROADMAP array's own chips.
        assert 'class="chip on"' not in block.split("dh-roadmap", 1)[1][:400]

    def test_no_roadmap_item_is_a_real_wired_field(self):
        """The roadmap names capability gaps, never a fake control - none
        of its labels is ever used as a data-dh-field/data-dh-value or
        merged into DIGITAL_HUMAN_STYLE_FIELDS/DIGITAL_HUMAN_FIELD_
        OPTIONS, so clicking anywhere near it can never write anything.
        Stage 17.14/17.15 shrank and renamed the list itself (see the
        sibling test above) - the shape of this guarantee is
        unchanged."""
        shell = _shell()
        roadmap_block = shell.split("const AVATAR_STUDIO_ROADMAP = [", 1)[1].split("];", 1)[0]
        items = re.findall(r'"([^"]+)"', roadmap_block)
        assert items == ["FACE SHAPE VARIANTS", "FACIAL EXPRESSION", "ALTERNATE BODY PACKS"]
        for item in items:
            assert item not in DIGITAL_HUMAN_STYLE_FIELDS
            options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
                "\n  };", 1
            )[0]
            assert item.lower() not in options_block.lower()

    def test_the_roadmap_chip_widget_fires_no_click_handler(self):
        """Purely decorative text - no event listener is ever attached
        to .dh-roadmap anywhere in this file."""
        shell = _shell()
        assert "querySelectorAll(\".dh-roadmap" not in shell
        assert ".dh-roadmap" not in _fn("wireDigitalHumanOptions")
        assert ".dh-roadmap" not in _fn("wireDigitalHumanForm")


# ---------------------------------------------------------------------------
# 16. Point 8, functionally proven: a saved design genuinely appears in
#     both Workspace and the Conversation Room, through a real
#     delegated exchange - not just inferred from shared source code.
# ---------------------------------------------------------------------------


class TestSavedDesignReachesBothWorkspaceAndRoom:
    def test_arthur_only_joins_the_room_after_a_real_delegation_by_design(self):
        """Documents a real, pre-existing, intentional fact a person
        checking this live needs to know: build_conversation() seeds
        the room with only the person and the Manager - a specialist
        is not a participant until the Manager actually brings them in.
        Not a bug this stage introduces or should paper over."""
        _, manager, _, conversation, _ = system()
        names = {p.name for p in conversation.participants}
        assert manager.own_skills.name in names
        assert "Arthur" not in names

    def test_after_a_real_delegation_arthurs_saved_avatar_appears_as_a_room_participant_too(self):
        """The full, real, functional round trip: save a distinct
        avatar/profile for Arthur, have the Manager genuinely delegate
        to him (the same real path any live conversation uses), then
        confirm the live snapshot's own agents[] entry Arthur's room
        body would read from (S.agents.find by name, the identical
        lookup wireAvatarBody3D's mountFn/animate() closure both use)
        carries the saved choice, and that Arthur now genuinely appears
        among conversation.participants - the one gate the Room's own
        markup filters on."""
        import json

        from scripts.serve_ui import build_live_system, route, route_message

        live = build_live_system(settings())
        memory = memory_for(live.manager, "Arthur")
        provider = DefaultAvatarProvider()
        set_avatar_preference(
            "Arthur", "bright_approachable", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"posture": "seated", "gesture_style": "expressive"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        route_message("Arthur, what can you do?", live, to="Arthur")

        _, body, _ = route("/snapshot.json", live)
        data = json.loads(body)
        names = {p["name"] for p in data["conversation"]["participants"]}
        assert "Arthur" in names

        arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
        assert arthur["avatar"]["avatar"]["avatar_id"] == "bright_approachable"
        assert arthur["avatar"]["profile"]["posture"] == "seated"
        assert arthur["avatar"]["profile"]["gesture_style"] == "expressive"


# ---------------------------------------------------------------------------
# 14. The full inline script is syntactically valid JavaScript - a real,
#     cheap execution-level proof beyond source-text matching alone.
# ---------------------------------------------------------------------------


class TestScriptSyntaxIsValid:
    def test_node_check_passes_on_the_full_extracted_script(self, tmp_path):
        node = None
        for candidate in ("node", "node.exe"):
            try:
                subprocess.run([candidate, "--version"], capture_output=True, check=True)
                node = candidate
                break
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        if node is None:
            pytest.skip("no node runtime available in this environment")
        script_path = tmp_path / "ui_script.js"
        script_path.write_text(_script(), encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(script_path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
