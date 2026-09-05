"""Stage 17.15 - Facial Identity + Hair Variants + Environment Refinement.

Inspection finding this phase starts from: Stage 17.14 gave every body
real, unconditional hair (one fixed shape) but no facial structure
beyond the bare head, and the symbolic environments (Stage 17.13) had
only one of the two rotating.

This phase adds, in ``ui/index.html`` and ``src/services/identity/
presence.py`` only:

    hair_style              an eleventh DigitalHumanProfile field (the
                             same one-line-addition extensibility the
                             tenth, symbolic_environment, already
                             proved) - hair moves from one unconditional
                             shape to a real, curated, genuinely-
                             different-geometry registry
    AVATAR_HAIR_STYLES
    avatarHairStyleFor()    the identical registry-plus-honest-fallback
                             shape avatarBodyEnvironmentFor()/
                             symbolicEnvironmentFor() already establish;
                             "short-neat" is the fallback (the exact
                             single shape every body already had, so no
                             already-saved profile changes appearance),
                             "short-textured" is a second, genuinely
                             different hand-built shape, "none" removes
                             hair entirely
    nose/ears/jaw            real, unconditional facial structure (a
                             box, two flattened spheres, a tapered
                             cylinder) - every body's own single real
                             shape, never a per-agent choice; the
                             AVATAR_STUDIO_ROADMAP entry was renamed
                             "FACE / HEAD SHAPE" -> "FACE SHAPE
                             VARIANTS" to keep the honesty boundary
                             accurate (real structure now exists,
                             choosing between variants still does not)
    buildBatmanEmblem        refined, not rebuilt: enlarged panels for a
                             stronger silhouette, and userData.rotor
                             marked true so it now rotates the same slow,
                             controlled way buildNightwingEmblem already
                             did - both symbols now behave identically
                             in that respect, through the one existing
                             rotor-check in mountAvatarBody3D (no new
                             branch there)

No new environment registry, no new mount surface, no new provider, no
lip-sync, no facial-expression rendering - all still honestly absent.
Alfred and Arthur's own real hair_style value was applied through the
same governed route_profile path prior stages used - see the
accompanying report, not this test file (which verifies the generic
mechanism, never one agent's specific choice).

Verified with the same methods every Stage 17.x file since 17.5 uses,
plus Stage 17.11's own `node --check` proof.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.presence import (
    DIGITAL_HUMAN_STYLE_FIELDS,
    digital_human_profile_for,
    digital_human_style_for,
    presence_for,
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


def _builder_body() -> str:
    shell = _shell()
    return shell.split("function buildHumanoidBody(THREE", 1)[1].split(
        "\n  const AVATAR_BODY_VISUAL_ENVIRONMENTS", 1
    )[0]


def _hair_registry_block() -> str:
    shell = _shell()
    return shell.split("const AVATAR_HAIR_STYLES = {", 1)[1].split("\n  };", 1)[0]


# ---------------------------------------------------------------------------
# 1. Facial identity - real, unconditional geometry.
# ---------------------------------------------------------------------------


class TestFacialFeaturesAreRealGeometry:
    def test_a_real_nose_exists(self):
        body = _builder_body()
        # Re-anchored by Stage 17.18's anatomy rebuild: the head is now a
        # real cranium roughly a sixth the figure's height rather than a
        # third of it, so every facial feature is proportionally smaller
        # and sits at a different landmark. The feature itself is exactly
        # as real as it was.
        assert "const nose = new THREE.Mesh(new THREE.BoxGeometry(0.032, 0.062, 0.055), skin);" in body
        assert "group.add(nose);" in body

    def test_real_ears_exist_on_both_sides(self):
        body = _builder_body()
        assert "const earL = new THREE.Mesh(earGeo, skin);" in body
        assert "const earR = new THREE.Mesh(earGeo, skin);" in body
        assert "earL.position.set(-0.146, 1.012, -0.008);" in body
        assert "earR.position.set(0.146, 1.012, -0.008);" in body

    def test_a_real_tapered_jaw_exists(self):
        body = _builder_body()
        assert "const jaw = new THREE.Mesh(new THREE.CylinderGeometry(0.132, 0.072, 0.16, 14), skin);" in body
        # Genuinely tapered - top and bottom radii differ - never a
        # uniform cylinder relabelled as a jaw.
        assert "0.132, 0.072" in body

    def test_facial_features_are_unconditional_no_field_gates_them(self):
        """Every body gets the identical single face shape - see
        AVATAR_STUDIO_ROADMAP's own "FACE SHAPE VARIANTS" entry for the
        honest boundary this deliberately does not cross yet."""
        body = _builder_body()
        region = body.split("group.add(eyeR);", 1)[1].split(
            "const appearanceKey = ", 1
        )[0]
        assert "if(" not in region

    def test_facial_features_reuse_the_head_skin_material_not_a_new_one(self):
        body = _builder_body()
        region = body.split("group.add(eyeR);", 1)[1].split(
            "const appearanceKey = ", 1
        )[0]
        assert "new THREE.MeshStandardMaterial" not in region
        assert region.count(", skin)") + region.count(", skin);") >= 4

    def test_only_already_proven_geometry_constructors_are_used(self):
        body = _builder_body()
        for forbidden in ("TextureLoader", "GLTFLoader", "ShapeGeometry", "CapsuleGeometry", "ExtrudeGeometry", "ConeGeometry"):
            assert forbidden not in body


# ---------------------------------------------------------------------------
# 2. Hair is now configurable, through a real registry, genuinely
#    different geometry per choice.
# ---------------------------------------------------------------------------


class TestHairIsConfigurableWithRealDistinctGeometry:
    def test_the_registry_has_exactly_two_real_hand_authored_styles(self):
        block = _hair_registry_block()
        keys = set(re.findall(r'"([\w-]+)":', block))
        assert keys == {"short-neat", "short-textured"}

    def test_short_neat_and_short_textured_use_different_geometry_constructors(self):
        shell = _shell()
        neat = shell.split("function buildHairShortNeat(THREE", 1)[1].split("\n  }\n", 1)[0]
        textured = shell.split("function buildHairShortTextured(THREE", 1)[1].split("\n  }\n", 1)[0]
        assert "IcosahedronGeometry" in neat
        assert "BoxGeometry" in textured
        assert "IcosahedronGeometry" not in textured

    def test_short_textured_is_built_from_multiple_distinct_pieces(self):
        """Genuinely different silhouette, not the same single mesh
        relabelled - five separately positioned/rotated strands."""
        shell = _shell()
        textured = shell.split("function buildHairShortTextured(THREE", 1)[1].split("\n  }\n", 1)[0]
        assert textured.count("new THREE.Mesh(new THREE.BoxGeometry") == 1  # one shared geometry call...
        assert textured.count("group.add(strand);") == 1  # ...added once per loop iteration
        assert textured.count("strand.position.set(") == 1  # inside the forEach - 5 real calls at runtime

    def test_the_fallback_is_short_neat_the_pre_1715_default_look(self):
        shell = _shell()
        assert (
            'const avatarHairStyleFor = key =>\n'
            '    AVATAR_HAIR_STYLES[String(key || "").toLowerCase()] || AVATAR_HAIR_STYLES["short-neat"];'
        ) in shell

    def test_none_is_a_real_absence_not_a_registry_key(self):
        block = _hair_registry_block()
        assert '"none"' not in block
        body = _builder_body()
        assert 'if(hairStyleKey !== "none"){' in body

    def test_hair_style_is_a_real_open_field_never_a_closed_enum(self):
        """The curated JS menu is a suggestion; set_digital_human_style()
        itself must still accept an arbitrary value."""
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "Alfred", {"hair_style": "hand-typed-custom-cut"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("Alfred", recall=memory.recall)
        assert loaded["hair_style"] == "hand-typed-custom-cut"

    def test_hair_style_is_the_eleventh_field(self):
        assert "hair_style" in DIGITAL_HUMAN_STYLE_FIELDS
        assert len(DIGITAL_HUMAN_STYLE_FIELDS) == 11


# ---------------------------------------------------------------------------
# 3. Environment registry isolation and refinement.
# ---------------------------------------------------------------------------


class TestEnvironmentRegistryRefinement:
    def test_both_symbols_now_carry_the_same_rotor_marker(self):
        shell = _shell()
        batman = shell.split("function buildBatmanEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        nightwing = shell.split("function buildNightwingEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        assert "group.userData.rotor = true;" in batman
        assert "group.userData.rotor = true;" in nightwing

    def test_rotation_still_flows_through_the_one_existing_check(self):
        """No new branch in mountAvatarBody3D was needed - refining the
        Batman emblem to rotate reused the exact existing rotor check."""
        shell = _shell()
        mount = shell.split("function mountAvatarBody3D(container", 1)[1].split(
            "\n  }\n\n  let agent3DStages", 1
        )[0]
        assert mount.count("symbolicGroup.userData.rotor") == 1

    def test_the_registry_still_has_exactly_the_two_real_symbols(self):
        shell = _shell()
        block = shell.split("const AVATAR_SYMBOLIC_ENVIRONMENTS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        keys = set(re.findall(r'"([\w-]+)":', block))
        assert keys == {"batman-emblem", "nightwing-emblem"}

    def test_batman_and_nightwing_remain_structurally_distinct(self):
        shell = _shell()
        batman = shell.split("function buildBatmanEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        nightwing = shell.split("function buildNightwingEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        # Distinctness, asserted as distinctness rather than as one fixed
        # constructor split. Stage 17.18 rebuilt both forms - the bat is
        # now a cowl with horns and two falling scalloped membranes, the
        # other a spar and ring with two rising feathered wings - and each
        # still reaches for at least one constructor the other does not,
        # which is a real structural difference rather than a recolour.
        import re as _re
        b_used = set(_re.findall(r"THREE\.(\w+Geometry)", batman))
        n_used = set(_re.findall(r"THREE\.(\w+Geometry)", nightwing))
        assert b_used - n_used, "the bat form uses nothing the other does not"
        assert n_used - b_used, "the other form uses nothing the bat does not"
        # And their outlines are genuinely different functions, not one
        # shape with two names.
        b_edge = batman.split("const edge = u => {", 1)[1].split("};", 1)[0]
        n_edge = nightwing.split("const edge = u => {", 1)[1].split("};", 1)[0]
        assert b_edge != n_edge

    def test_no_agent_name_in_either_symbol_builder(self):
        shell = _shell()
        batman = shell.split("function buildBatmanEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        nightwing = shell.split("function buildNightwingEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        for fn in (batman, nightwing):
            for name in ("Alfred", "Arthur"):
                assert name not in fn, name


# ---------------------------------------------------------------------------
# 4. No hardcoded Alfred/Arthur anywhere in the new renderer code.
# ---------------------------------------------------------------------------


class TestGenericAcrossAllAgentsNoHardcoding:
    def test_no_agent_name_in_the_face_or_hair_code(self):
        body = _builder_body()
        region = body.split("group.add(head);", 1)[1].split(
            "const appearanceKey = ", 1
        )[0]
        for name in ("Alfred", "Arthur"):
            assert name not in region, name

    def test_no_agent_name_in_the_hair_curated_option_list(self):
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        for name in ("Alfred", "Arthur"):
            assert name not in options_block, name

    def test_a_future_specialist_gets_the_same_hair_and_face_mechanism(self):
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "FutureSpecialist", {"hair_style": "short-textured", "symbolic_environment": "nightwing-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("FutureSpecialist", recall=memory.recall)
        assert loaded["hair_style"] == "short-textured"
        assert loaded["symbolic_environment"] == "nightwing-emblem"


# ---------------------------------------------------------------------------
# 5. Alfred/Arthur (and any two agents) independence, persistence.
# ---------------------------------------------------------------------------


class TestIsolationAndPersistence:
    def test_alfred_and_arthur_get_independent_hair_styles(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            manager.own_skills.name, {"hair_style": "short-neat"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"hair_style": "short-textured"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        alfred = digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)
        arthur = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert alfred["hair_style"] == "short-neat"
        assert arthur["hair_style"] == "short-textured"

    def test_changing_alfreds_hair_never_touches_arthurs(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            "Arthur", {"hair_style": "short-textured"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        before = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        set_digital_human_style(
            manager.own_skills.name, {"hair_style": "none"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        after = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert before == after

    def test_hair_style_survives_a_fresh_recall(self):
        memory = EphemeralMemory()
        set_digital_human_style(
            "Alfred", {"hair_style": "short-textured"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        reloaded = digital_human_style_for("Alfred", recall=memory.recall)
        assert reloaded["hair_style"] == "short-textured"


# ---------------------------------------------------------------------------
# 6. LLM / Brain / Memory / Voice independence.
# ---------------------------------------------------------------------------


class TestLLMBrainMemoryVoiceIndependence:
    def test_swapping_the_brain_does_not_change_hair_style(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"hair_style": "short-textured"},
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
        assert after.hair_style == "short-textured"

    def test_changing_hair_never_touches_the_brain(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        brain_before = manager.brain
        set_digital_human_style(
            manager.own_skills.name, {"hair_style": "none"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert manager.brain is brain_before

    def test_voiceprofile_is_unaffected(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        voice_before = voice_for(manager.own_skills)
        set_digital_human_style(
            manager.own_skills.name, {"hair_style": "short-textured"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert voice_for(manager.own_skills) == voice_before

    def test_survives_a_full_llm_provider_replacement(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        provider = DefaultAvatarProvider()
        set_digital_human_style(
            manager.own_skills.name, {"hair_style": "short-textured"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)
        assert after.profile == before.profile

    def test_no_second_profile_or_memory_system_was_created(self):
        import ast

        tree = ast.parse((REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert classes == {
            "PresenceState", "PresenceRegistry", "DigitalHumanProfile",
            "AgentPresence", "PresentationIntent",
        }


# ---------------------------------------------------------------------------
# 7. Existing behavior preserved; honesty boundary re-affirmed.
# ---------------------------------------------------------------------------


class TestExistingBehaviorAndHonestyPreserved:
    def test_all_named_animation_behaviors_still_present(self):
        shell = _shell()
        mount = shell.split("function mountAvatarBody3D(container", 1)[1].split(
            "\n  }\n\n  let agent3DStages", 1
        )[0]
        assert "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;" in mount
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
        assert 'S.agents.some(a => a.name !== agentName && a.activity === "speaking")' in mount
        assert "group.rotation.y += attentionYaw;" in mount

    def test_no_lip_sync_or_facial_expression_rendering_claim_was_added(self):
        body = _builder_body()
        face_region = body.split("group.add(eyeR);", 1)[1].split(
            "const appearanceKey = ", 1
        )[0]
        hair_region = body.split("const hairStyleKey = ", 1)[1].split(
            "const eyeGeo = ", 1
        )[0]
        for region in (face_region, hair_region):
            lowered = region.lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme", "facialexpression"):
                assert word not in lowered

    def test_expression_style_still_has_no_rendering_effect(self):
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        assert "expression_style" not in options_block

    def test_no_new_media_query_was_added(self):
        shell = _shell()
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_no_new_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text


# ---------------------------------------------------------------------------
# 8. Evolution / Extensions / Upgrade decoupling.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_upgrade_file_references_this_phases_additions(self):
        new_identifiers = (
            "AVATAR_HAIR_STYLES", "avatarHairStyleFor", "buildHairShortNeat",
            "buildHairShortTextured", "hair_style", "hairStyleKey",
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


# ---------------------------------------------------------------------------
# 9. Script syntax is genuinely valid JavaScript.
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
