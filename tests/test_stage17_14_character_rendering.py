"""Stage 17.14 - Real Agent Character & Environment Rendering.

Inspection finding this phase starts from: Stage 17.13 delivered a real
symbolic-environment registry and a genuine cyan avatar option, but the
human body itself still had no hair, no glasses, and no clothing layer -
"appearance" and "clothing" were stored text with no geometric effect,
exactly the gap this stage's own brief calls out ("do not merely put
text ... and call it rendered").

This phase adds, in ``ui/index.html`` only (zero further Python changes
beyond what Stage 17.11-17.13 already added - appearance/clothing were
already real DigitalHumanProfile fields, just not yet rendered):

    hair            unconditional real geometry (a second, non-uniformly
                    scaled Icosahedron) - every body gets one, never a
                    per-agent field, because no per-style catalog is
                    honestly renderable yet (only a single silhouette is)
    glasses         real geometry (two BoxGeometry lenses + a bridge),
                    genuinely conditional on the curated
                    appearance="glasses" option - a real per-agent choice
    shirt + trim    real geometry (a second cylinder in a non-skin cloth
                    material, plus accent-lit trim strips), conditional
                    on the curated clothing="fitted"/"casual" options
    baggy trousers  a real, wider leg-cylinder radius, conditional on
                    clothing="casual" specifically - genuinely distinct
                    from "fitted"'s own narrower, trimmed silhouette

``appearance``/``clothing`` moved from Stage 17.11's "not yet rendered"
bucket into RENDERED_STYLE_FIELDS/DIGITAL_HUMAN_FIELD_OPTIONS, the
identical curated-menu-on-an-open-field contract every other rendered
dimension already has - never a closed enum
(set_digital_human_style() still accepts any string). The
AVATAR_STUDIO_ROADMAP ghost list shrank in step (HAIR/CLOTHING RENDER
removed - a solved gap left listed would itself be dishonest).

Alfred and Arthur's own real values (glasses / no glasses, fitted /
casual clothing) were applied through the same governed
route_avatar/route_profile path Stage 17.13 used - see the accompanying
report, not this test file (which verifies the generic mechanism, never
one agent's specific choice).

No lip-sync, no facial expression, no face-shape control was added -
still honestly absent. Verified with the same methods every Stage 17.x
file since 17.5 uses, plus Stage 17.11's own `node --check` proof.
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


def _glasses_block(body: str) -> str:
    return body.split('if(appearanceKey === "glasses"){', 1)[1].split(
        "\n    }\n\n    // Stage 17.7", 1
    )[0]


def _clothing_block(body: str) -> str:
    return body.split("const clothingKey = ", 1)[1].split(
        "const legWidthMul = ", 1
    )[0]


# ---------------------------------------------------------------------------
# 1. Hair - real, unconditional geometry.
# ---------------------------------------------------------------------------


class TestHairIsRealGeometry:
    """Superseded by Stage 17.15: hair moved from one unconditional
    shape (this class's own original claim) to a real, curated,
    genuinely-different-geometry registry (AVATAR_HAIR_STYLES/
    avatarHairStyleFor(), see tests/test_stage17_15_facial_identity.py)
    - the exact shape this class used to pin now lives inside
    buildHairShortNeat(), reached through that registry rather than
    inlined here. These three tests were updated in step, not weakened;
    the fourth (material color) still holds unchanged."""

    def test_a_real_hair_geometry_still_exists_reached_through_the_registry(self):
        body = _builder_body()
        assert "group.add(avatarHairStyleFor(hairStyleKey).build(THREE, hairMat));" in body
        shell = _shell()
        # Re-anchored and subdivided further by Stage 17.18's anatomy
        # rebuild - the cranium it caps is a real head-sized head now, and
        # at that size one subdivision read as a faceted lump.
        assert "const hair = new THREE.Mesh(new THREE.IcosahedronGeometry(0.158, 2), hairMat);" in shell

    def test_hair_is_now_genuinely_conditional_on_the_curated_field(self):
        """The opposite of this class's own original claim, by design -
        see this stage's own brief: "Hair must become configurable."""
        body = _builder_body()
        assert 'if(hairStyleKey !== "none"){' in body

    def test_hair_uses_a_genuinely_different_material_than_skin(self):
        body = _builder_body()
        assert "const hairMat = new THREE.MeshStandardMaterial({" in body
        hair_mat_block = body.split("const hairMat = new THREE.MeshStandardMaterial({", 1)[1].split(
            "});", 1
        )[0]
        assert "0x030405" in hair_mat_block  # near-black, distinct from 0x141a21 skin

    def test_the_default_short_neat_shape_is_non_uniformly_scaled(self):
        shell = _shell()
        assert "hair.scale.set(1.05, 0.8, 0.92);" in shell


# ---------------------------------------------------------------------------
# 2. Glasses - real, genuinely conditional geometry.
# ---------------------------------------------------------------------------


class TestGlassesAreRealAndConditional:
    def test_glasses_are_gated_on_the_curated_appearance_value(self):
        body = _builder_body()
        assert 'const appearanceKey = String((style && style.appearance) || "").toLowerCase();' in body
        assert 'if(appearanceKey === "glasses"){' in body

    def test_glasses_are_built_from_boxes_only(self):
        body = _builder_body()
        block = _glasses_block(body)
        assert block.count("new THREE.BoxGeometry(") == 2  # lensGeo shared by both lenses, plus the bridge
        assert "new THREE.Mesh(lensGeo, glassesMat)" in block or "lensGeo" in block
        assert "bridge" in block

    def test_glasses_never_appear_for_an_unrecognized_or_empty_appearance(self):
        body = _builder_body()
        # "none" (the curated default) and "" (unset) both take the
        # false branch of the if() above - proven by the branch reading
        # a strict equality, never a truthy check that "none" would pass.
        assert 'appearanceKey === "glasses"' in body
        assert 'appearanceKey !== ""' not in body

    def test_glasses_functionally_appear_only_when_configured(self):
        """A structural proxy for actual WebGL output: the exact literal
        condition the renderer checks, confirmed against the exact
        curated value the Avatar Studio can actually write."""
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        appearance_line = [ln for ln in options_block.splitlines() if ln.strip().startswith("appearance:")][0]
        assert '"none", "glasses"' in appearance_line


# ---------------------------------------------------------------------------
# 3. Clothing - real, genuinely conditional, two distinct silhouettes.
# ---------------------------------------------------------------------------


class TestClothingIsRealAndDistinct:
    def test_a_real_shirt_layer_exists_gated_on_clothing(self):
        body = _builder_body()
        block = _clothing_block(body)
        assert 'if(clothingKey === "fitted" || clothingKey === "casual"){' in block
        assert "const shirt = new THREE.Mesh(new THREE.CylinderGeometry(" in block
        assert "group.add(shirt);" in block

    def test_the_shirt_material_is_not_skin_toned(self):
        body = _builder_body()
        block = _clothing_block(body)
        shirt_mat = block.split("const shirtMat = new THREE.MeshStandardMaterial({", 1)[1].split(
            "});", 1
        )[0]
        assert "0x1c2530" in shirt_mat
        assert "0x141a21" not in shirt_mat  # the skin material's own color

    def test_fitted_adds_trim_strips_casual_does_not(self):
        body = _builder_body()
        block = _clothing_block(body)
        fitted_only = block.split('if(clothingKey === "fitted"){', 1)[1].split(
            "\n      }\n    }", 1
        )[0]
        assert "trimL" in fitted_only and "trimR" in fitted_only
        assert "casual" not in fitted_only

    def test_casual_widens_the_leg_geometry_fitted_does_not(self):
        body = _builder_body()
        assert 'const legWidthMul = clothingKey === "casual" ? 1.35 : 1;' in body
        assert "new THREE.CylinderGeometry(0.1*legWidthMul, 0.09*legWidthMul," in body

    def test_none_or_unset_clothing_stays_bare_the_exact_pre_1714_look(self):
        body = _builder_body()
        block = _clothing_block(body)
        # The bare torso itself (built above this block, unconditional)
        # is never touched by the clothing branch - only additive meshes
        # are ever added inside the if().
        assert "torso.geometry" not in block
        assert "torso.material" not in block

    def test_trim_uses_the_agents_own_accent_never_a_hardcoded_color(self):
        body = _builder_body()
        block = _clothing_block(body)
        trim_mat = block.split("const trimMat = new THREE.MeshStandardMaterial({", 1)[1].split(
            "});", 1
        )[0]
        assert "emissive: accent" in trim_mat


# ---------------------------------------------------------------------------
# 4. The fields are genuinely rendered now, curated on top of the same
#    open contract, never a closed enum.
# ---------------------------------------------------------------------------


class TestFieldsAreGenuinelyRenderedNotFabricated:
    def test_appearance_and_clothing_are_now_in_rendered_style_fields(self):
        shell = _shell()
        rendered = set(
            shell.split('const RENDERED_STYLE_FIELDS = new Set([', 1)[1].split("]);", 1)[0]
            .replace('"', "").split(", ")
        )
        assert {"appearance", "clothing"}.issubset(rendered)

    def test_exactly_three_fields_remain_honestly_unrendered(self):
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        curated = set(re.findall(r"(\w+):\s*\[", options_block))
        uncurated = set(DIGITAL_HUMAN_STYLE_FIELDS) - curated
        assert uncurated == {"listening_behavior", "speaking_behavior", "expression_style"}

    def test_the_backend_still_accepts_any_string_never_a_closed_enum(self):
        """The curated JS menu is a suggestion; set_digital_human_style()
        itself must still accept an arbitrary value for both fields."""
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "Alfred", {"appearance": "hand-typed-custom-look", "clothing": "hand-typed-custom-outfit"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("Alfred", recall=memory.recall)
        assert loaded["appearance"] == "hand-typed-custom-look"
        assert loaded["clothing"] == "hand-typed-custom-outfit"

    def test_the_roadmap_no_longer_lists_hair_or_clothing_render(self):
        """Stage 17.15 also renamed "FACE / HEAD SHAPE" to "FACE SHAPE
        VARIANTS" once real facial structure existed - updated in step."""
        shell = _shell()
        roadmap_block = shell.split("const AVATAR_STUDIO_ROADMAP = [", 1)[1].split("];", 1)[0]
        items = re.findall(r'"([^"]+)"', roadmap_block)
        assert "HAIR" not in items
        assert "CLOTHING RENDER" not in items
        assert items == ["FACE SHAPE VARIANTS", "FACIAL EXPRESSION", "ALTERNATE BODY PACKS"]


# ---------------------------------------------------------------------------
# 5. Generalized architecture - no hardcoded Alfred/Arthur anywhere in
#    the new renderer code.
# ---------------------------------------------------------------------------


class TestNoHardcodedAgentsInRenderer:
    def test_no_agent_name_in_the_hair_glasses_or_clothing_code(self):
        body = _builder_body()
        new_region = body.split("const hairStyleKey = ", 1)[1]
        for name in ("Alfred", "Arthur"):
            assert name not in new_region, name

    def test_no_agent_name_in_the_curated_option_lists(self):
        shell = _shell()
        options_block = shell.split("const DIGITAL_HUMAN_FIELD_OPTIONS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        for name in ("Alfred", "Arthur"):
            assert name not in options_block, name

    def test_a_future_specialist_gets_the_same_hair_glasses_and_clothing_mechanism(self):
        """Proven functionally: an unregistered agent name can still
        genuinely configure appearance/clothing through the identical
        governed path - nothing about the mechanism requires a known
        name."""
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "FutureSpecialist", {"appearance": "glasses", "clothing": "fitted"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("FutureSpecialist", recall=memory.recall)
        assert loaded["appearance"] == "glasses"
        assert loaded["clothing"] == "fitted"


# ---------------------------------------------------------------------------
# 6. Per-agent isolation, persistence, LLM/Brain/Memory/Voice
#    independence - re-proven for this phase's own new surface.
# ---------------------------------------------------------------------------


class TestIsolationPersistenceIndependence:
    def test_alfred_and_arthur_get_independent_appearance_and_clothing(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "glasses", "clothing": "fitted"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"appearance": "none", "clothing": "casual"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        alfred = digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)
        arthur = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert alfred["appearance"] == "glasses" and alfred["clothing"] == "fitted"
        assert arthur["appearance"] == "none" and arthur["clothing"] == "casual"

    def test_changing_alfreds_appearance_never_touches_arthurs(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            "Arthur", {"appearance": "none", "clothing": "casual"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        before = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "glasses", "clothing": "fitted"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        after = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert before == after

    def test_appearance_and_clothing_survive_a_fresh_recall(self):
        memory = EphemeralMemory()
        set_digital_human_style(
            "Alfred", {"appearance": "glasses", "clothing": "fitted"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        reloaded = digital_human_style_for("Alfred", recall=memory.recall)
        assert reloaded["appearance"] == "glasses"
        assert reloaded["clothing"] == "fitted"

    def test_swapping_the_brain_does_not_change_appearance_or_clothing(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "glasses", "clothing": "fitted"},
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
        assert after.appearance == "glasses"
        assert after.clothing == "fitted"

    def test_changing_appearance_never_touches_the_brain(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        brain_before = manager.brain
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "glasses"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert manager.brain is brain_before

    def test_voiceprofile_is_unaffected(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        voice_before = voice_for(manager.own_skills)
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "glasses", "clothing": "casual"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert voice_for(manager.own_skills) == voice_before

    def test_survives_a_full_llm_provider_replacement(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        provider = DefaultAvatarProvider()
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "glasses", "clothing": "fitted"},
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
# 7. Existing animation preserved; honesty boundary re-affirmed.
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

    def test_no_lip_sync_or_facial_expression_claim_was_added(self):
        """Scoped to this phase's own genuinely new lines (hair through
        the end of the clothing block) - not the whole builder function,
        which legitimately *discusses* the absence of lip-sync data in
        its own pre-existing mouth-primitive comment (see Stage 17.7's
        own correctly-scoped test for that check)."""
        body = _builder_body()
        hair_block = body.split("const hairStyleKey = ", 1)[1].split(
            "const eyeGeo = ", 1
        )[0]
        glasses_block = _glasses_block(body)
        clothing_block = _clothing_block(body)
        for region in (hair_block, glasses_block, clothing_block):
            lowered = region.lower()
            for word in (
                "viseme", "lip-sync", "lip sync", "lipsync", "phoneme",
                "expression_style", "facialexpression",
            ):
                assert word not in lowered

    def test_only_already_proven_geometry_constructors_are_used(self):
        body = _builder_body()
        for forbidden in ("TextureLoader", "GLTFLoader", "ShapeGeometry", "CapsuleGeometry", "ExtrudeGeometry"):
            assert forbidden not in body
        for used in ("IcosahedronGeometry", "SphereGeometry", "CylinderGeometry", "BoxGeometry"):
            assert used in body

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
        new_identifiers = ("hairMat", "glassesMat", "shirtMat", "trimMat", "legWidthMul", "appearanceKey")
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
