"""Stage 17.13 - Agent Visual Identity + 3D Symbolic Environment.

Inspection finding this phase starts from: Stage 17.12 already delivered
a genuine, per-agent, independently-configured Digital Human system
(avatar/body/height/posture/idle/gesture, instant preview, governed
persistence, LLM/Brain/Voice independence, an honest not-yet-available
boundary). The one genuinely new concept this phase's brief asks for -
a per-agent 3D "symbolic environment" backdrop, distinct from the human
body mesh - did not exist in any form.

This phase adds it as the smallest coherent extension of what already
exists, never a second architecture:

    DigitalHumanProfile.symbolic_environment
                               a tenth open string field (Python), the
                               same "curated menu on an open field"
                               contract the ninth (Stage 17.13's own
                               sibling addition, gesture_style) already
                               has - never a closed enum, never a new
                               profile type
    AVATAR_SYMBOLIC_ENVIRONMENTS
    symbolicEnvironmentFor()  the same registry-plus-fallback shape
                               environmentFor()/avatarBodyEnvironmentFor()
                               already establish - except the fallback
                               is null (no symbol), never a default
                               shape, because a backdrop is optional
                               decoration, unlike a body
    buildBatmanEmblem()
    buildNightwingEmblem()    two original, hand-authored primitive-
                               geometry symbols - no copied asset, no
                               texture, no new geometry constructor
                               beyond what buildHumanoidBody already
                               proves available

Wired into the exact existing mountAvatarBody3D()/mountStage() lifecycle
- no new mount surface, no new stages Map, no new wire function, no
second rendering engine. Because Room and Workspace already share that
one function (proven since Stage 17.7/17.9), the symbol reaches both for
free - re-verified here, not just inferred.

The registry is looked up purely by the profile's own string value -
nowhere does mountAvatarBody3D, buildBatmanEmblem, buildNightwingEmblem
or symbolicEnvironmentFor ever read or branch on an agent's name. Which
symbol Alfred or Arthur actually has is a fact stored in their own
governed Memory (this phase applied the user's own explicit design brief
through the identical route_avatar/route_profile path a real POST
already uses - see the accompanying report), never a hardcoded rendering
branch.

Also added: a fourth real AvatarProvider catalog entry
(``sharp_technical``, a genuine cyan accent_color) - nothing in the
first three named cyan, and this phase's own brief explicitly asks for
cyan edge lighting, which mountStage's own accent-driven DirectionalLight
already provides for free once a genuinely cyan accent exists.

Verified with the same methods every Stage 17.x file since 17.5 uses,
plus Stage 17.11's own `node --check` syntax-execution proof.
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


def _fn(name: str, end: str = "\n  }\n") -> str:
    return _shell().split(f"function {name}(", 1)[1].split(end, 1)[0]


def _mount_body() -> str:
    shell = _shell()
    return shell.split("function mountAvatarBody3D(container", 1)[1].split(
        "\n  }\n\n  let agent3DStages", 1
    )[0]


class _AltProvider(AvatarProvider):
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
# 1. The field is real, open, and reaches the exact existing pipeline.
# ---------------------------------------------------------------------------


class TestSymbolicEnvironmentFieldIsReal:
    def test_it_is_present_among_the_digital_human_style_fields(self):
        """Was the tenth (and last) field when this stage shipped; Stage
        17.15 added an eleventh (hair_style) after it - this test now
        checks presence and position relative to its own neighbours,
        not a total count owned by a later stage."""
        assert "symbolic_environment" in DIGITAL_HUMAN_STYLE_FIELDS
        assert DIGITAL_HUMAN_STYLE_FIELDS.index("symbolic_environment") == (
            DIGITAL_HUMAN_STYLE_FIELDS.index("gesture_style") + 1
        )

    def test_it_persists_through_the_existing_governed_path(self):
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "Alfred", {"symbolic_environment": "batman-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("Alfred", recall=memory.recall)
        assert loaded["symbolic_environment"] == "batman-emblem"

    def test_an_arbitrary_uncurated_value_is_still_accepted_never_a_closed_enum(self):
        """The open-field philosophy holds for the tenth field exactly
        as it does for the other nine - a curated JS menu is a
        suggestion, never a server-side restriction."""
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "Alfred", {"symbolic_environment": "some-future-symbol-nobody-registered-yet"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True

    def test_it_is_never_special_cased_python_side(self):
        """DigitalHumanProfile.__post_init__/to_dict() already loop
        DIGITAL_HUMAN_STYLE_FIELDS generically - proven, not assumed, by
        checking no bespoke "symbolic_environment" branch exists
        anywhere in presence.py outside the field-list declarations."""
        text = (REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8")
        occurrences = text.count("symbolic_environment")
        # Exactly the tuple entry, the docstring diagram line, and the
        # docstring's own extra explanatory sentence - no executable
        # branch reads or special-cases it.
        assert occurrences <= 4, occurrences
        assert "if self.symbolic_environment" not in text
        assert "self.symbolic_environment ==" not in text


# ---------------------------------------------------------------------------
# 2. Per-agent isolation.
# ---------------------------------------------------------------------------


class TestPerAgentIsolation:
    def test_alfred_and_arthur_get_independent_symbolic_environments(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            manager.own_skills.name, {"symbolic_environment": "batman-emblem"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_digital_human_style(
            "Arthur", {"symbolic_environment": "nightwing-emblem"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        alfred = digital_human_style_for(manager.own_skills.name, recall=alfred_memory.recall)
        arthur = digital_human_style_for("Arthur", recall=arthur_memory.recall)
        assert alfred["symbolic_environment"] == "batman-emblem"
        assert arthur["symbolic_environment"] == "nightwing-emblem"
        assert alfred["symbolic_environment"] != arthur["symbolic_environment"]

    def test_changing_alfreds_symbol_never_touches_arthurs(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        set_digital_human_style(
            "Arthur", {"symbolic_environment": "nightwing-emblem"},
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        before = digital_human_style_for("Arthur", recall=arthur_memory.recall)["symbolic_environment"]
        set_digital_human_style(
            manager.own_skills.name, {"symbolic_environment": "batman-emblem"},
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        after = digital_human_style_for("Arthur", recall=arthur_memory.recall)["symbolic_environment"]
        assert before == after == "nightwing-emblem"

    def test_a_future_unregistered_specialist_gets_its_own_independent_symbol(self):
        memory = EphemeralMemory()
        ok = set_digital_human_style(
            "FutureSpecialist", {"symbolic_environment": "batman-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok is True
        loaded = digital_human_style_for("FutureSpecialist", recall=memory.recall)
        assert loaded["symbolic_environment"] == "batman-emblem"


# ---------------------------------------------------------------------------
# 3. Renderer/environment registry lookup.
# ---------------------------------------------------------------------------


class TestRendererRegistryLookup:
    def test_the_registry_has_exactly_the_two_real_hand_authored_symbols(self):
        shell = _shell()
        block = shell.split("const AVATAR_SYMBOLIC_ENVIRONMENTS = {", 1)[1].split(
            "\n  };", 1
        )[0]
        keys = set(re.findall(r'"([\w-]+)":', block))
        assert keys == {"batman-emblem", "nightwing-emblem"}

    def test_lookup_falls_back_to_null_never_a_default_shape(self):
        """Unlike avatarBodyEnvironmentFor (which must always resolve to
        a body), an unset or unrecognised symbolic_environment honestly
        renders nothing - the fallback is null, not a shape nobody
        chose."""
        shell = _shell()
        assert (
            'const symbolicEnvironmentFor = key =>\n'
            '    AVATAR_SYMBOLIC_ENVIRONMENTS[String(key || "").toLowerCase()] || null;'
        ) in shell

    def test_mountavatarbody3d_only_adds_a_symbol_group_when_one_resolves(self):
        mount = _mount_body()
        assert "const symbolicEnv = symbolicEnvironmentFor(style && style.symbolic_environment);" in mount
        assert "let symbolicGroup = null;" in mount
        assert "if(symbolicEnv){" in mount

    def test_the_symbol_is_a_sibling_group_never_a_child_of_the_body(self):
        """The brief's own explicit distinction: "not part of the human
        body mesh." Proven structurally - the symbol group is added to
        `scene`, never to `group` (the humanoid body's own Group)."""
        mount = _mount_body()
        block = mount.split("const symbolicEnv = symbolicEnvironmentFor", 1)[1].split(
            "const eyes = group.userData.eyes;", 1
        )[0]
        # Stage 17.18 put the body, its floor and its backdrop into one
        # `world` group so they scale together (mountStage owns the camera
        # and its signature is fixed, so "move the camera in" has to be
        # expressed as "scale the world"). The symbol is still a SIBLING of
        # the body - both are children of `world` - which is the whole
        # invariant here; the symbol being added to the body group itself
        # is what must never appear, and that is asserted just below.
        assert "world.add(symbolicGroup);" in block
        assert "world.add(group);" in _mount_body()
        assert "group.add(symbolicGroup)" not in block

    def test_both_symbol_builders_use_only_already_proven_geometry_constructors(self):
        shell = _shell()
        batman = shell.split("function buildBatmanEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        nightwing = shell.split("function buildNightwingEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        for fn in (batman, nightwing):
            for forbidden in ("TextureLoader", "GLTFLoader", "ImageBitmap", "CapsuleGeometry", "ShapeGeometry"):
                assert forbidden not in fn
        # Every geometry constructor either builder names must be one
        # this file's own pre-existing 3D code already proved the vendored
        # build ships - written as a set check rather than a list of
        # "must contain" strings, so a redesign of either shape (Stage
        # 17.18 rebuilt both) cannot quietly smuggle in an unavailable
        # constructor and cannot fail merely for using a different mix of
        # available ones.
        import re as _re
        proven = {
            "IcosahedronGeometry", "SphereGeometry", "CylinderGeometry",
            "BoxGeometry", "DodecahedronGeometry", "OctahedronGeometry",
            "TorusGeometry", "BufferGeometry",
        }
        for fn in (batman, nightwing):
            used = set(_re.findall(r"THREE\.(\w+Geometry)", fn))
            assert used, "each symbol must actually build geometry"
            assert used <= proven, used - proven

    def test_no_symbol_geometry_is_a_child_of_the_humanoid_bodys_own_userdata(self):
        """The symbol must never be attached via buildHumanoidBody's own
        group.userData (eyes/head/arms/mouth/torso) - a separate concept
        entirely."""
        body = _shell().split("function buildHumanoidBody(THREE", 1)[1].split(
            "\n  const AVATAR_BODY_VISUAL_ENVIRONMENTS", 1
        )[0]
        assert "symbolic" not in body.lower()


# ---------------------------------------------------------------------------
# 4. No hardcoded Alfred/Arthur anywhere in the renderer or registry.
# ---------------------------------------------------------------------------


class TestGenericAcrossAllAgentsNoHardcoding:
    def test_no_agent_name_in_the_registry_or_lookup_function(self):
        shell = _shell()
        block = shell.split("const AVATAR_SYMBOLIC_ENVIRONMENTS = {", 1)[1].split(
            "const symbolicEnvironmentFor", 1
        )[0]
        lookup = shell.split("const symbolicEnvironmentFor = key =>", 1)[1].split("\n\n", 1)[0]
        for name in ("Alfred", "Arthur"):
            assert name not in block, name
            assert name not in lookup, name

    def test_no_agent_name_in_either_symbol_builder(self):
        shell = _shell()
        batman = shell.split("function buildBatmanEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        nightwing = shell.split("function buildNightwingEmblem(THREE", 1)[1].split("\n  }\n", 1)[0]
        for fn in (batman, nightwing):
            for name in ("Alfred", "Arthur"):
                assert name not in fn, name

    def test_no_agent_name_in_the_mountavatarbody3d_symbolic_wiring(self):
        mount = _mount_body()
        block = mount.split("const symbolicEnv = symbolicEnvironmentFor", 1)[1].split(
            "const eyes = group.userData.eyes;", 1
        )[0]
        for name in ("Alfred", "Arthur"):
            assert name not in block, name

    def test_the_registry_which_symbol_an_agent_gets_lives_only_in_memory_not_code(self):
        """The real, current fact - which agent has which symbol - is a
        governed Memory record (set via the same route_profile a POST
        already uses), never a name check in the renderer. Proven by
        showing a bare, unregistered agent key resolves through the
        identical function with no special handling."""
        memory = EphemeralMemory()
        set_digital_human_style(
            "ZzzUnregistered", {"symbolic_environment": "batman-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        loaded = digital_human_style_for("ZzzUnregistered", recall=memory.recall)
        assert loaded["symbolic_environment"] == "batman-emblem"


# ---------------------------------------------------------------------------
# 5. LLM / Brain / Memory / Voice independence.
# ---------------------------------------------------------------------------


class TestLLMBrainMemoryVoiceIndependence:
    def test_swapping_the_brain_does_not_change_the_symbolic_environment(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"symbolic_environment": "batman-emblem"},
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
        assert after.symbolic_environment == "batman-emblem"

    def test_changing_the_symbol_never_touches_the_brain(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        brain_before = manager.brain
        set_digital_human_style(
            manager.own_skills.name, {"symbolic_environment": "nightwing-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert manager.brain is brain_before

    def test_voiceprofile_is_unaffected(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        voice_before = voice_for(manager.own_skills)
        set_digital_human_style(
            manager.own_skills.name, {"symbolic_environment": "batman-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert voice_for(manager.own_skills) == voice_before

    def test_survives_a_full_llm_provider_replacement(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        provider = DefaultAvatarProvider()
        set_avatar_preference(
            manager.own_skills.name, "sharp_technical", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_digital_human_style(
            manager.own_skills.name, {"symbolic_environment": "batman-emblem"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        before = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = presence_for(manager.own_skills.name, provider=provider, recall=memory.recall)
        assert after.profile == before.profile
        assert after.avatar == before.avatar

    def test_presence_module_still_imports_nothing_from_any_other_provider(self):
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
# 6. The fourth catalog entry - real, cyan, generic.
# ---------------------------------------------------------------------------


class TestFourthCatalogEntryIsReal:
    def test_sharp_technical_is_a_real_cyan_entry(self):
        catalog = DefaultAvatarProvider().catalog()
        entry = next(p for p in catalog if p.avatar_id == "sharp_technical")
        assert entry.accent_color == "cyan"

    def test_it_is_not_tied_to_any_one_agent_name(self):
        """Scoped to this phase's own new catalog entry - not the whole
        module, whose pre-existing docstring legitimately *names*
        Alfred as an illustrative example of what the catalog explicitly
        refuses to hardcode ("not one hardcoded, permanent Alfred
        appearance")."""
        text = (REPO_ROOT / "src" / "providers" / "default_avatar_provider.py").read_text("utf-8")
        entry = text.split('avatar_id="sharp_technical",', 1)[1].split(")", 1)[0]
        for name in ("Alfred", "Arthur"):
            assert name not in entry, name

    def test_the_js_accent_map_has_a_matching_real_cyan_value(self):
        shell = _shell()
        assert "cyan: \"56,224,232\"" in shell

    def test_a_second_independent_avatar_provider_still_works_through_the_existing_seam(self):
        alt = _AltProvider()
        p = presence_for("FutureSpecialist", provider=alt)
        assert p.avatar.avatar_id == "alt_pack"


# ---------------------------------------------------------------------------
# 7. Evolution / Extensions / Upgrade decoupling.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_upgrade_file_references_this_phases_additions(self):
        new_identifiers = (
            "AVATAR_SYMBOLIC_ENVIRONMENTS", "symbolicEnvironmentFor",
            "buildBatmanEmblem", "buildNightwingEmblem", "sharp_technical",
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

    def test_no_new_class_was_added_to_presence_py(self):
        import ast

        tree = ast.parse((REPO_ROOT / "src" / "services" / "identity" / "presence.py").read_text("utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert classes == {
            "PresenceState", "PresenceRegistry", "DigitalHumanProfile",
            "AgentPresence", "PresentationIntent",
        }


# ---------------------------------------------------------------------------
# 8. Reduced motion, no randomness, existing behavior preserved.
# ---------------------------------------------------------------------------


class TestReducedMotionAndExistingBehaviorPreserved:
    def test_no_new_media_query_was_added(self):
        shell = _shell()
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_rotation_is_deterministic_never_random(self):
        mount = _mount_body()
        rotor_line = [ln for ln in mount.splitlines() if "symbolicGroup.rotation.y" in ln][0]
        assert "Math.random" not in rotor_line
        # Driven only by the elapsed clock the animation loop already
        # passes in, and by a rate the environment itself declares - never
        # by a drawn value, and never by anything else. Stage 17.18 moved
        # the rate out of this line and into each environment's own
        # registry entry (a fixed 0.18 for every backdrop meant two agents'
        # environments turned in lockstep), so the invariant is pinned as
        # "elapsed time and a declared rate", not as one hardcoded number.
        assert "Math.sin(t * rate)" in rotor_line
        assert "userData.spinRate" in mount
        for env in ("buildBatmanEmblem", "buildNightwingEmblem"):
            block = _shell().split(f"function {env}(THREE", 1)[1].split("\n  }\n", 1)[0]
            rate_line = [ln for ln in block.splitlines() if "spinRate" in ln][0]
            assert "Math.random" not in rate_line

    def test_rotation_only_applies_to_symbols_that_ask_for_it(self):
        mount = _mount_body()
        assert "symbolicGroup.userData.rotor" in mount

    def test_all_named_animation_behaviors_still_present(self):
        mount = _mount_body()
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

    def test_lip_sync_still_honestly_not_implemented(self):
        for fn_text in (
            _shell().split("function buildBatmanEmblem(THREE", 1)[1].split("\n  }\n", 1)[0],
            _shell().split("function buildNightwingEmblem(THREE", 1)[1].split("\n  }\n", 1)[0],
        ):
            lowered = fn_text.lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
                assert word not in lowered

    def test_no_new_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text


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
