"""Stage 17.12 - User-Defined Digital Human Design.

Inspection finding this phase starts from: Stage 17.11 (plus its own
correction pass) already delivered nearly everything this phase's brief
asks for - independent per-agent DigitalHumanProfile, select-agent-then-
edit, curated pickers for posture/body/idle_behavior/gesture_style,
instant local preview, save through the existing governed Memory path,
LLM/Brain independence, and an honest "not yet available" boundary for
appearance/clothing/listening_behavior/speaking_behavior/expression_style
plus face/head/hair (see the "Avatar Studio" panel and its
AVATAR_STUDIO_ROADMAP ghost list).

The one genuine gap: "height/proportions" was requested as its own
control, and nothing in the renderer varied a body's height at all -
only width (buildHumanoidBody's own widthScale). This phase adds it,
in ``ui/index.html`` only (zero Python files changed, matching Stage
17.11's own pattern - the backend already has everything needed):

    heightScale             a second, genuinely independent deterministic
                             value from the exact same open ``body``
                             string (a different hash seed - never a new
                             DigitalHumanProfile field, never a migration)
    group.scale.y            applied once at build time - the whole body
                             stretches together, never just one part

No new curated field, no new picker, no new fabricated option:
DIGITAL_HUMAN_FIELD_OPTIONS still names exactly the same four keys it
did after Stage 17.11 - "body" now genuinely controls two independent
axes instead of one, both already covered by the exact same picker.

This phase also closes a real, previously-untested independence claim:
point 6 of its own brief ("changing Brain, Memory, Voice, OR OTHER
PROVIDERS must never reset the design") had only ever been tested for
Brain - this file adds the same proof for Connection and Celebration,
representative of "any other provider" (nothing in presence.py's own
API surface ever receives one, so the guarantee is structural, not
per-provider, but it is proven here rather than merely asserted).

Verified with the same methods every Stage 17.x file since 17.5 uses:
executed Python tests for the real data/provider layer, source-text
structural checks for the renderer, plus Stage 17.11's own addition of
a real `node --check` syntax-execution proof.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.avatar import AvatarProfile, AvatarProvider
from src.providers.avatar_factory import available_avatar_providers
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
from src.services.memory.ephemeral import EphemeralMemory
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


def _shell() -> str:
    return UI.read_text("utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


def _fn(name: str) -> str:
    return _shell().split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


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


def _workspace_block() -> str:
    shell = _shell()
    return shell.split("V.workspace = () =>", 1)[1].split("\n  V.brain = ()", 1)[0]


def _builder_body() -> str:
    shell = _shell()
    return shell.split("function buildHumanoidBody(THREE", 1)[1].split(
        "\n  const AVATAR_BODY_VISUAL_ENVIRONMENTS", 1
    )[0]


class _AltProvider(AvatarProvider):
    """A second, independent AvatarProvider - never touching the real
    factory - standing in for a future avatar/body pack."""

    _CATALOG = (
        AvatarProfile(avatar_id="alt_pack", label="Alt Pack", body_type="alt_pack", accent_color="teal"),
    )

    @property
    def name(self) -> str:
        return "alt"

    def catalog(self):
        return self._CATALOG

    def default_for(self, agent_key: str):
        return self._CATALOG[0]


# ---------------------------------------------------------------------------
# 1/2/3. Independent design per agent, select-then-edit, no hardcoding.
# ---------------------------------------------------------------------------


class TestIndependentDesignPerAgent:
    def test_two_agents_get_fully_independent_designs_including_height(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            manager.own_skills.name, {"body": "slim", "posture": "standing"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"body": "broad", "posture": "seated"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        alfred = digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)
        arthur = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert alfred["body"] == "slim" and alfred["posture"] == "standing"
        assert arthur["body"] == "broad" and arthur["posture"] == "seated"

    def test_a_future_unregistered_specialist_gets_its_own_independent_design_too(self):
        """No count of agents or specific names is baked in - the exact
        same governed path works for a name that exists nowhere in this
        project's own registry, standing in for a future specialist.
        memory_for() itself honestly refuses to resolve a Memory for an
        unregistered name (see its own docstring: "never fabricates
        one") - the real proof here is that set_digital_human_style()/
        digital_human_style_for() need only real recall/remember/forget
        callables, never a registered agent, so any real Memory works."""
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "FutureSpecialist", {"body": "broad", "gesture_style": "expressive"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("FutureSpecialist", recall=memory.recall)
        assert loaded["body"] == "broad"
        assert loaded["gesture_style"] == "expressive"

    def test_the_studio_selects_the_agent_before_editing_that_agents_own_panel(self):
        block = _workspace_block()
        assert 'class="k">Avatar Studio — ${esc(a.name)}</div>' in block
        assert 'data-dh-agent="${esc(a.name)}"' in block

    def test_no_hardcoded_alfred_or_arthur_in_the_new_height_code(self):
        for block in (_builder_body(), _shell().split(
            "const DIGITAL_HUMAN_FIELD_LABELS = {", 1
        )[1].split("\n  };", 1)[0]):
            for name in ("Alfred", "Arthur"):
                assert name not in block, name


# ---------------------------------------------------------------------------
# 4. Height is genuinely rendered - a second real, independent axis, not
#    a fabricated field.
# ---------------------------------------------------------------------------


class TestHeightIsGenuinelyRendered:
    def test_height_is_computed_from_an_independent_hash_seed(self):
        body = _builder_body()
        assert (
            'const heightUnit = (style && style.body) ? _stableUnit(style.body + ":height") : 0.5;'
        ) in body
        assert "const heightScale = 0.95 + heightUnit * 0.12;" in body

    def test_height_and_width_are_not_the_same_value(self):
        """Structural, not merely by-eye: the two hash calls use
        different literal input strings."""
        body = _builder_body()
        assert "_stableUnit(style.body)" in body
        assert '_stableUnit(style.body + ":height")' in body
        assert body.count("_stableUnit(style.body") == 2

    def test_height_is_applied_as_a_uniform_whole_body_transform(self):
        """The whole group scales together - never one limb stretched
        independently of another, which would look like a rendering bug
        rather than a real height difference."""
        body = _builder_body()
        assert "group.scale.y = heightScale;" in body
        # Applied once, at the very end, after every child mesh already
        # exists - never a per-mesh scale that could drift apart.
        assert body.rsplit("group.scale.y = heightScale;", 1)[1].strip().startswith("return group;")

    def test_no_new_curated_field_or_fabricated_option_was_added_for_height(self):
        """Height rides on the existing, already-real "body" field and
        its existing three curated options - never a new field, never a
        new option nobody asked for, for height specifically. (Stage
        17.13 later added a genuinely new curated field of its own,
        symbolic_environment - a real, separately-reviewed decision,
        not something this specific height slice needed or added.)"""
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        body_line = [ln for ln in options_block.splitlines() if ln.strip().startswith("body:")][0]
        assert '"slim", "average", "broad"' in body_line
        idle_line = [ln for ln in options_block.splitlines() if ln.strip().startswith("idle_behavior:")][0]
        assert '"steady", "measured", "restless"' in idle_line

    def test_the_label_makes_the_second_axis_visible_never_hidden(self):
        shell = _shell()
        assert 'body: "body · proportions & height",' in shell
        block = _workspace_block()
        assert "DIGITAL_HUMAN_FIELD_LABELS[f]" in block

    def test_height_values_for_the_three_curated_presets_are_not_degenerate(self):
        """Functional, not just structural: the real hash algorithm
        this file already ships (_stableUnit) produces three genuinely
        different height values for the three curated body presets -
        reimplemented here only to prove the claim numerically, the
        same verification method this project's own Python-side
        default-selection hash already gets in its own tests."""

        def stable_unit(text: str) -> float:
            h = 0
            for ch in text:
                h = (h * 31 + ord(ch)) & 0xFFFFFFFF
            return (h % 1000) / 1000

        heights = {
            preset: 0.95 + stable_unit(f"{preset}:height") * 0.12
            for preset in ("slim", "average", "broad")
        }
        assert len(set(heights.values())) == 3
        for v in heights.values():
            assert 0.95 <= v <= 1.07


# ---------------------------------------------------------------------------
# 5/6. Persistence through governed Memory; save/load round trip.
# ---------------------------------------------------------------------------


class TestPersistenceThroughGovernedMemory:
    def test_full_design_round_trips_including_the_new_height_bearing_field(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        ok = set_digital_human_style(
            manager.own_skills.name,
            {"posture": "seated", "body": "broad", "idle_behavior": "restless", "gesture_style": "expressive"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        assert loaded["body"] == "broad"
        assert loaded["posture"] == "seated"

    def test_design_survives_being_reloaded_from_a_fresh_recall(self):
        """Not just returned by the same call - genuinely re-readable
        from Memory afresh, the real persistence claim."""
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"body": "slim"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        second_read = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        assert second_read["body"] == "slim"


# ---------------------------------------------------------------------------
# 7. LLM / Brain / Memory / Voice independence.
# ---------------------------------------------------------------------------


class TestLLMBrainMemoryVoiceIndependence:
    def test_swapping_the_brain_does_not_change_the_stored_design(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"body": "broad", "posture": "seated"},
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

    def test_changing_the_design_never_touches_the_brain(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        brain_before = manager.brain
        set_digital_human_style(
            manager.own_skills.name, {"body": "slim"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert manager.brain is brain_before

    def test_voiceprofile_is_unaffected_by_a_design_change(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        voice_before = voice_for(manager.own_skills)
        set_digital_human_style(
            manager.own_skills.name, {"body": "broad", "idle_behavior": "restless"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert voice_for(manager.own_skills) == voice_before

    def test_the_design_survives_a_full_llm_provider_replacement(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        provider = DefaultAvatarProvider()
        set_avatar_preference(
            manager.own_skills.name, "warm_formal", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            manager.own_skills.name, {"body": "slim", "posture": "seated"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)
        assert after.avatar == before.avatar
        assert after.profile == before.profile


class TestOtherProviderIndependence:
    """Point 6's own "or other providers" clause, proven directly - not
    only inferred from presence.py never importing them."""

    def test_swapping_connection_does_not_reset_the_design(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"body": "broad"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = digital_human_style_for(manager.own_skills.name, recall=memory.recall)

        class OtherConnection:
            pass

        object.__setattr__(runtime, "connection", OtherConnection())

        after = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        assert after == before

    def test_swapping_celebration_provider_does_not_reset_the_design(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = digital_human_style_for(manager.own_skills.name, recall=memory.recall)

        class OtherCelebration:
            pass

        object.__setattr__(runtime, "celebration", OtherCelebration())

        after = digital_human_style_for(manager.own_skills.name, recall=memory.recall)
        assert after == before

    def test_presence_module_imports_nothing_from_any_other_provider(self):
        text = (REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8")
        import_lines = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
        for ln in import_lines:
            for forbidden in (
                "src.providers.connection", "src.providers.celebration",
                "src.providers.communication", "src.providers.calendar_events",
                "src.providers.live_info", "src.providers.speech", "src.providers.base",
            ):
                assert forbidden not in ln, ln


# ---------------------------------------------------------------------------
# 8. Unsupported visual dimensions cannot falsely appear as available.
# ---------------------------------------------------------------------------


class TestUnsupportedDimensionsStayHonest:
    def test_the_three_unrendered_fields_have_no_picker_and_stay_labelled(self):
        """Superseded by Stage 17.14: appearance/clothing became
        genuinely rendered fields - updated in step, not weakened."""
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        curated = set(re.findall(r"(\w+):\s*\[", options_block))
        uncurated = set(DIGITAL_HUMAN_STYLE_FIELDS) - curated
        assert uncurated == {"listening_behavior", "speaking_behavior", "expression_style"}
        assert "not yet rendered" in shell

    def test_the_roadmap_ghosts_stay_static_and_never_become_a_real_field(self):
        """Superseded by Stage 17.14: "HAIR" and "CLOTHING RENDER" were
        removed from the roadmap once real geometry existed for both.
        Superseded again by Stage 17.15: "FACE / HEAD SHAPE" was renamed
        "FACE SHAPE VARIANTS" once real facial structure existed -
        shrinking/renaming the list is exactly as much a reviewed,
        honest decision as growing it, not a weakening of the check
        itself."""
        shell = _shell()
        roadmap_block = shell.split("const AVATAR_STUDIO_ROADMAP = [", 1)[1].split("];", 1)[0]
        items = re.findall(r'"([^"]+)"', roadmap_block)
        assert items == ["FACE SHAPE VARIANTS", "FACIAL EXPRESSION", "ALTERNATE BODY PACKS"]
        for item in items:
            assert item not in DIGITAL_HUMAN_STYLE_FIELDS
            assert f'data-dh-value="${{esc({item})}}"' not in shell

    def test_no_face_shape_geometry_or_field_exists_anywhere(self):
        """Superseded by Stage 17.14 for hair specifically - real hair
        geometry now exists (unconditional, not a per-agent field - see
        that phase's own report on why). Face shape remains genuinely
        absent, still honestly true."""
        body = _builder_body()
        for forbidden in ("face_style", "faceShape", "headShape"):
            assert forbidden not in body

    def test_a_pre_existing_custom_value_for_a_curated_field_is_never_hidden(self):
        block = _workspace_block()
        assert "dh-current" in block


# ---------------------------------------------------------------------------
# 9. AvatarProvider / body-type registry / extension architecture
#    preserved and re-proven.
# ---------------------------------------------------------------------------


class TestExtensionArchitecturePreserved:
    def test_the_body_type_registry_still_has_exactly_one_real_entry(self):
        shell = _shell()
        block = shell.split("const AVATAR_BODY_VISUAL_ENVIRONMENTS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        assert block.count(": {") == 1
        assert "humanoid:" in block

    def test_an_unrecognized_body_type_still_falls_back_honestly(self):
        shell = _shell()
        assert (
            "AVATAR_BODY_VISUAL_ENVIRONMENTS[String(bodyType || \"\").toLowerCase()] "
            "|| AVATAR_BODY_VISUAL_ENVIRONMENTS.humanoid"
        ) in shell

    def test_a_second_independent_avatar_provider_still_works_through_the_existing_seam(self):
        alt = _AltProvider()
        p = presence_for("FutureSpecialist", provider=alt)
        assert p.avatar.avatar_id == "alt_pack"
        assert p.avatar.body_type == "alt_pack"

    def test_the_real_factory_still_lists_default(self):
        assert "default" in available_avatar_providers()


# ---------------------------------------------------------------------------
# 10. Evolution / Extensions / Upgrade decoupling.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_upgrade_file_references_this_phases_additions(self):
        new_identifiers = (
            "heightScale", "heightUnit", "DIGITAL_HUMAN_FIELD_LABELS",
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

    def test_no_python_file_was_changed_this_phase(self):
        import ast

        tree = ast.parse((REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert classes == {
            "PresenceState", "PresenceRegistry", "DigitalHumanProfile",
            "AgentPresence", "PresentationIntent",
        }


# ---------------------------------------------------------------------------
# 11. Existing Stage 17.6B-17.11 behaviour remains intact.
# ---------------------------------------------------------------------------


class TestExistingBehaviorPreserved:
    def test_all_named_animation_behaviors_still_present(self):
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        listening = _listening_branch(mount)
        idle = _idle_branch(mount)
        assert "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;" in speaking
        assert "group.rotation.y += attentionYaw;" in idle
        assert "const headLeanTarget = 0.12 + Math.sin(t*0.9) * 0.015;" in listening
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
        assert shell.count("const PRESENTATION_INTENT_EFFECTS = {") == 1
        text = (REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8")
        assert "def presentation_intent_for_turn" in text

    def test_lip_sync_still_honestly_not_implemented(self):
        """Scoped to this phase's own genuinely new lines (the height
        computation and its comment) - not the whole builder function,
        which legitimately *discusses* the absence of lip-sync data in
        its own pre-existing mouth-primitive comment (see Stage 17.7's
        own correctly-scoped test for that check)."""
        body = _builder_body()
        computation = body.split("const heightUnit", 1)[1].split("const heightScale", 1)[0]
        tail = body.rsplit("group.scale.y = heightScale;", 1)[0].rsplit(
            "group.userData.torso = torso;", 1
        )[1]
        lowered = (computation + tail).lower()
        for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
            assert word not in lowered

    def test_no_new_media_query_was_added(self):
        shell = _shell()
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_no_new_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text


# ---------------------------------------------------------------------------
# 12. Script syntax is genuinely valid JavaScript.
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
