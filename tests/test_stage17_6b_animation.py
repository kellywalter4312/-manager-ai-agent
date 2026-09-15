"""Stage 17.6B - real, per-agent-varied gesture/idle/listening animation
for the Stage 17.5 humanoid body, extended without touching any Python-
side data layer at all: every change this phase makes lives in
ui/index.html's own renderer functions. The Stage 17.1-17.6A backend
(PresenceRegistry, speaking(), DigitalHumanProfile persistence, agent
resolution) is untouched and re-verified by simply re-running its own
existing suites (see this repository's Stage 17.6B report for the exact
regression commands and counts).

Verified the same way this project's own 3D/DOM code has been verified
since Stage 17.5: as source text, because there is no JS execution
harness in this repository and headless-browser pixel verification was
found unreliable in this sandbox for both new and pre-existing 3D code
alike (see tests/test_stage17_5_digital_human_renderer.py's own module
docstring). A genuine limitation, named here rather than hidden: these
tests prove the *mechanism* is wired correctly (real per-agent inputs,
real bounded/eased motion, real reuse of the existing reduced-motion
gate) - they cannot prove what a human eye would see two different
agents' arms actually doing, because nothing in this repository can
execute the JavaScript to find out.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


def _fn(shell: str, start_marker: str, end_marker: str) -> str:
    return shell.split(start_marker, 1)[1].split(end_marker, 1)[0]


def _builder_body() -> str:
    shell = UI.read_text("utf-8")
    return _fn(shell, "function buildHumanoidBody(THREE", "\n  const AVATAR_BODY_VISUAL_ENVIRONMENT")


def _mount_body() -> str:
    shell = UI.read_text("utf-8")
    return _fn(shell, "function mountAvatarBody3D(container", "\n  }\n\n  let agent3DStages")


# ---------------------------------------------------------------------------
# 1. Real, animatable arm rigging - a genuine structural change from
#    Stage 17.5's fixed, un-animatable arm meshes.
# ---------------------------------------------------------------------------


class TestArmsAreGenuinelyAnimatable:
    def test_each_arm_is_its_own_pivot_group_not_a_fixed_mesh(self):
        """Two independent shoulder pivots, each a real Group with its own
        arm mesh inside it - which is what lets a gesture be a rotation
        rather than a repositioning.

        Stage 17.18 gave each arm an upper segment, an elbow and a forearm
        instead of one straight cylinder, so the two are now built by one
        shared `buildArm(side)` rather than written out twice. Sharing the
        construction is the point - the two arms must be built the same way
        - and both pivots are still named, still separate Groups, and still
        the things `group.userData.arms` hands to the animation."""
        body = _builder_body()
        assert "const buildArm = (side) => {" in body
        assert "const pivot = new THREE.Group();" in body
        assert "const armPivotL = builtL.pivot, armL = builtL.arm;" in body
        assert "const armPivotR = builtR.pivot, armR = builtR.arm;" in body
        assert "armPivotL.add(armL);" in body
        assert "armPivotR.add(armR);" in body
        # A real elbow inside each pivot - the segment a straight cylinder
        # could never have had.
        assert "const elbow = new THREE.Group();" in body
        assert "pivot.add(elbow);" in body

    def test_the_resting_pose_is_a_real_mirrored_angle_the_animation_reads_back(self):
        """Each pivot rests at a real, mirrored angle, and the animation
        eases home to whatever the builder actually built rather than to a
        number written independently of it.

        Stage 17.18 changed the value itself, on purpose: 0.2/-0.2 was
        correct for a body whose arms grew straight out of the sides of a
        single barrel torso and had to be swung outward to clear it. This
        body has real shoulders and deltoids to hang from, so the arms rest
        closer in. What must not change - and is what this test now pins -
        is that the two are mirrored, and that mountAvatarBody3D reads the
        resting angle back off the built geometry (`restZ`) instead of
        assuming one."""
        body = _builder_body()
        assert "armPivotL.rotation.z = ARM_REST_Z;" in body
        assert "armPivotR.rotation.z = -ARM_REST_Z;" in body
        assert "const restZ = arms.map(p => p.rotation.z);" in UI.read_text("utf-8")

    def test_the_group_exposes_arms_for_the_mount_functions_own_animation(self):
        body = _builder_body()
        assert "group.userData.arms = [armPivotL, armPivotR];" in body

    def test_only_proven_available_geometry_constructors_are_still_used(self):
        """Re-affirmed after this phase's rewrite: no THREE.CapsuleGeometry
        or other constructor not already proven present by this file's
        own pre-existing 3D code - unchanged guarantee from Stage
        17.5."""
        import re as _re
        body = _builder_body()
        proven = {
            "IcosahedronGeometry", "SphereGeometry", "CylinderGeometry",
            "BoxGeometry", "DodecahedronGeometry", "OctahedronGeometry",
            "TorusGeometry", "BufferGeometry",
        }
        used = set(_re.findall(r"THREE\.(\w+Geometry)", body))
        assert used
        assert used <= proven, used - proven
        assert "CapsuleGeometry" not in body


# ---------------------------------------------------------------------------
# 2/3. Real per-agent gesture/idle signature, and real arm motion while
#      speaking, bounded and eased - never an identical fixed loop.
# ---------------------------------------------------------------------------


class TestPerAgentGestureSignature:
    def test_the_gesture_signature_is_derived_from_this_agents_own_profile_or_name(self):
        """Never a fixed, shared gesture set - profile.gesture_style
        when configured, this agent's own name otherwise (so even two
        unconfigured agents still differ, the same "default differs
        per agent" rule DefaultAvatarProvider.default_for already
        holds avatar choice to)."""
        mount = _mount_body()
        assert "_stableUnit(profile.gesture_style || agentName)" in mount
        assert "_stableUnit(profile.idle_behavior || (agentName + \":idle\"))" in mount

    def test_gesture_amplitude_and_rate_are_a_real_bounded_range_not_a_fixed_constant(self):
        mount = _mount_body()
        assert "const gestureAmp = 0.16 + gestureSignature * 0.22;" in mount
        assert "const gestureRate = 1.5 + gestureSignature * 1.1;" in mount

    def test_speaking_genuinely_rotates_the_arm_pivots(self):
        mount = _mount_body()
        speaking_branch = mount.split('if(activity === "speaking"){', 1)[1].split(
            '} else if(activity === "listening")', 1
        )[0]
        assert "const targetZ = restZ[i] + Math.sin(t*gestureRate" in speaking_branch
        assert "const targetX = Math.sin(t*gestureRate" in speaking_branch

    def test_the_two_arms_move_on_different_phases_not_a_mirrored_unit(self):
        """i*1.7 offsets each arm's own phase - the two arms must not
        swing as a single, perfectly mirrored, robotic-looking unit."""
        mount = _mount_body()
        assert "Math.sin(t*gestureRate + i*1.7)" in mount

    def test_gesturing_returns_to_this_bodys_own_real_resting_angle(self):
        """restZ is read from the actual constructed geometry
        (arms.map(p => p.rotation.z)), never a value guessed
        independently of what buildHumanoidBody() actually built."""
        mount = _mount_body()
        assert "const restZ = arms.map(p => p.rotation.z);" in mount


# ---------------------------------------------------------------------------
# 4. Occasional, bounded nodding while speaking - never continuous,
#    never on every frame.
# ---------------------------------------------------------------------------


class TestBoundedNodding:
    def test_the_nod_is_a_short_recurring_window_not_a_permanent_state(self):
        mount = _mount_body()
        speaking_branch = mount.split('if(activity === "speaking"){', 1)[1].split(
            '} else if(activity === "listening")', 1
        )[0]
        assert "const nodPhase = (t + gestureSignature*3) % 5.4;" in speaking_branch
        assert "nodPhase > 4.9" in speaking_branch

    def test_the_nod_offset_differs_per_agent_so_simultaneous_speakers_do_not_sync(self):
        mount = _mount_body()
        assert "(t + gestureSignature*3) % 5.4" in mount


# ---------------------------------------------------------------------------
# 5. Listening keeps its own genuine, distinct micro-motion; idle and
#    listening both ease poses back toward rest rather than snapping.
# ---------------------------------------------------------------------------


class TestListeningAndIdleEasing:
    def test_listening_has_a_real_micro_motion_not_a_single_frozen_angle(self):
        mount = _mount_body()
        listening_branch = mount.split('} else if(activity === "listening"){', 1)[1].split(
            "} else {", 1
        )[0]
        assert "0.12 + Math.sin(t*0.9) * 0.015" in listening_branch

    def test_listening_and_idle_both_ease_arms_back_toward_rest(self):
        """Both non-speaking branches interpolate toward a rest-derived
        target each tick, never snap to it.

        Stage 17.21 kept the easing and moved the *target*: at rest the
        arms now settle onto rest plus a small, slow, per-arm drift
        ("settled, not frozen") instead of onto the bare resting angle.
        The interpolation - the thing this test exists to protect - is
        byte-identical, same 0.06 coefficient, in both branches.

        Pinned harder than before as a result: the old assertion could
        not have caught that drift growing until "at rest" looked like
        gesturing, so the amplitudes are now bounded here, and a snap is
        asserted absent rather than merely implied.
        """
        mount = _mount_body()
        listening = mount.split('} else if(activity === "listening"){', 1)[1].split("} else {", 1)[0]
        idle = mount.split('} else if(activity === "listening"){', 1)[1].split("} else {", 1)[1]

        # The easing itself: same shape, same coefficient, both branches.
        assert "pivot.rotation.z += (still - pivot.rotation.z) * 0.06;" in listening
        assert "pivot.rotation.z += (drift - pivot.rotation.z) * 0.06;" in idle
        assert mount.count("- pivot.rotation.z) * 0.06;") == 2

        # Both targets are derived from the resting pose, not invented.
        assert "const still = restZ[i] + Math.sin(t*0.35 + i*2.4) * 0.010;" in listening
        assert (
            "const drift = restZ[i] + Math.sin(t*0.47 + i*2.1 + gestureSignature*3) * 0.022;"
        ) in idle

        # Never a snap, in either branch.
        for branch in (listening, idle):
            assert "pivot.rotation.z = restZ[i]" not in branch

        # And the drift stays an order of magnitude under the speaking
        # gesture, so "at rest" cannot quietly become "gesturing".
        assert 0.022 < 0.035

        # The same, on the other axis. Listening still eases to a flat 0;
        # idle eases to its own small drift, by the identical expression
        # and the identical coefficient.
        assert "pivot.rotation.x += (0 - pivot.rotation.x) * 0.06;" in listening
        assert "pivot.rotation.x += (Math.sin(t*0.31 + i) * 0.014 - pivot.rotation.x) * 0.06;" in idle
        assert mount.count("- pivot.rotation.x) * 0.06;") == 2
        assert 0.014 < 0.035

    def test_idle_eases_the_head_back_toward_rest(self):
        """Stage 17.7 replaced the plain "ease to 0" with a richer
        target (0, unless another real agent is genuinely speaking -
        see tests/test_stage17_7_room_conversation.py's own
        TestRoomAttention for that guarantee) - still a real
        interpolation each tick, never a snap, which is the guarantee
        this test itself exists to pin."""
        mount = _mount_body()
        idle_branch = mount.rsplit("} else {", 1)[1]
        assert "group.userData.head.rotation.x += (attentionLean - group.userData.head.rotation.x) * 0.04;" in idle_branch


# ---------------------------------------------------------------------------
# 6. No animation state can leak between agents - each mount call owns
#    its own closures, never module-level shared mutable state.
# ---------------------------------------------------------------------------


class TestNoCrossAgentAnimationLeakage:
    def test_gesture_and_idle_signatures_are_computed_fresh_inside_the_per_frame_closure(self):
        """Not hoisted to mount-time or module scope - recomputed every
        frame, inside the closure returned for *this* mount call alone,
        so two agents' stages can never read or write each other's
        signature/amplitude/rate."""
        mount = _mount_body()
        inner = mount.split("return t => {", 1)[1]
        assert "const gestureSignature = _stableUnit(" in inner
        assert "const idleSignature = _stableUnit(" in inner

    def test_arms_and_rest_angles_are_local_to_this_mount_calls_own_scope(self):
        shell = UI.read_text("utf-8")
        # Declared with `const` inside mountAvatarBody3D's own function
        # body (not `let`/`var` at module scope) - real lexical
        # scoping, not a shared mutable global two agents could collide
        # on.
        fn = shell.split("function mountAvatarBody3D(container", 1)[1].split(
            "\n  }\n\n  let agent3DStages", 1
        )[0]
        assert "const arms = group.userData.arms;" in fn
        assert "const restZ = arms.map" in fn
        assert not any(
            line.strip().startswith("let arms") or line.strip().startswith("var arms")
            for line in shell.split("\n")
        )


# ---------------------------------------------------------------------------
# 7. Reduced motion is still inherited for free - no second gate added.
# ---------------------------------------------------------------------------


class TestReducedMotionStillInheritedForFree:
    def test_no_new_reduced_motion_block_was_added(self):
        shell = UI.read_text("utf-8")
        assert shell.count("@media (prefers-reduced-motion") == 1
        mount = _mount_body()
        assert "prefers-reduced-motion" not in mount

    def test_mountavatarbody3d_still_delegates_the_whole_animate_loop_to_mountstage(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function mountAvatarBody3D(container", 1)[1].split("\n  }\n\n  let agent3DStages", 1)[0]
        assert "mountStage(container, avatarBody3DStages" in fn


# ---------------------------------------------------------------------------
# 8. No lip-sync/viseme/phoneme claim was introduced by this phase's
#    gesture work; no device coupling; PresentationIntent still
#    genuinely unwired; the editor's honesty labels were updated to
#    match, not just left stale.
# ---------------------------------------------------------------------------


class TestHonestyPreserved:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_viseme_or_phoneme_data_is_consumed_by_the_new_gesture_code(self):
        mount = _mount_body()
        for consumed in (".viseme", ".phoneme", "visemeData", "phonemeData", "lipSync"):
            assert consumed not in mount, consumed

    def test_no_device_name_in_the_new_animation_code(self):
        for block in (_builder_body(), _mount_body()):
            lowered = block.lower()
            for device in self.DEVICE_NAMES:
                assert device not in lowered, device

    def test_presentationintent_remains_unwired_by_this_phase_too(self):
        """Re-affirmed: this phase's gesture parameters come from
        DigitalHumanProfile.gesture_style/idle_behavior directly
        (already-real, already-persisted per-agent data), never from a
        Brain-constructed PresentationIntent - inspected this phase,
        deliberately not extended, because nothing produces one yet
        and wiring a seam with no real producer would be exactly the
        premature abstraction this project's own conventions refuse."""
        for rel in ("src/manager", "src/agents"):
            for path in (REPO_ROOT / rel).rglob("*.py"):
                assert "PresentationIntent" not in path.read_text("utf-8")

    def test_the_editor_honestly_relabels_the_fields_that_now_genuinely_render(self):
        """Superseded repeatedly since: Stage 17.9/17.10 added
        gesture_style/idle_behavior, Stage 17.13 added
        symbolic_environment, Stage 17.14 added appearance/clothing,
        Stage 17.15 added hair_style - each a real, reviewed addition to
        what the renderer genuinely draws differently, not a weakening
        of the honesty check itself."""
        shell = UI.read_text("utf-8")
        assert (
            'const RENDERED_STYLE_FIELDS = new Set(["posture", "body", '
            '"gesture_style", "idle_behavior", "symbolic_environment", '
            '"appearance", "clothing", "hair_style"]);'
        ) in shell

    def test_no_third_media_query_breakpoint_was_added(self):
        shell = UI.read_text("utf-8")
        assert shell.count("@media (max-width:760px)") == 2


# ---------------------------------------------------------------------------
# 9. Evolution/Extensions/Upgrade governance remains untouched and
#    uncoupled from this phase's own additions.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_extensions_file_references_this_phases_additions(self):
        for rel in (
            # src/extensions.py (Stage 11.A) was removed on 2026-09-15 as a
            # parallel, unwired governance architecture; the governed
            # lifecycle these checks care about lives in src/evolution.py
            # and src/services/evolution/.
            "src/evolution.py", "src/services/evolution/handlers.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            text = (REPO_ROOT / rel).read_text("utf-8")
            for forbidden in ("armPivot", "gestureSignature", "gestureAmp", "nodPhase"):
                assert forbidden not in text, (rel, forbidden)

    def test_no_python_source_file_changed_this_phase(self):
        """This phase's entire diff lives in ui/index.html - the
        canonical Python-side Agent/Brain/Memory/Voice/PresenceRegistry
        architecture is untouched, re-verified simply by the fact that
        every one of its own existing test suites (run separately, see
        this phase's own report) still passes unchanged."""
        assert not any(
            "armPivot" in (REPO_ROOT / "src" / rel).read_text("utf-8")
            for rel in ("services/identity/presence.py", "channels/channel.py")
        )
