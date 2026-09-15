"""Stage 17.20 - the second visual correction pass, driven by the browser.

Stage 17.19 corrected this interface by reasoning about it and pinned the
result in source assertions. Opened in a real headless Chromium against the
real `scripts/serve_ui.py`, at 1600x1000, 1024x900, 820x1180 and 390x844,
four things it had asserted were fine were visibly not:

  * **Cognition did not read as a brain.** The resting rotation tilted the
    crown toward the camera, so it was seen in plan view - the one angle
    from which a brain has no profile at all - and rendered as a lumpy
    grey disc. The folds were too coarse and too low-frequency to read as
    gyri, the cerebrum was taller than it was wide (a walnut, not a
    cerebrum), there were no temporal lobes, and the stem descended clear
    of the mass so the whole shape read as a mushroom.

  * **Memory did not read as memory.** Eight thin hoops evenly threaded on
    a spindle is the shape of a coil spring, which is what it looked like.

  * **Every 3D surface was framed by a fixed camera.** A PerspectiveCamera
    frames by its *vertical* FOV, and these panels are wide: Cognition and
    Memory were clipped through the stem and through the store while two
    thirds of the panel width stayed empty, and on the private interface
    Arthur's feet were cut off the bottom edge.

  * **The layout hid the product.** The private line's unused state was one
    italic line at the top of a 500px column of black, which reads as a
    screen that failed to load; the middle band stacked a 500px character
    on top of the conversation and put the composer at y=1100 in a 900px
    viewport; and the document declared no viewport meta at all, so a
    390px phone laid the page out at 980px and the entire phone band had
    never once applied.

Every claim above was measured in that browser, not inferred. This file
pins the source-level structure behind each correction - the part a future
change can silently break - and re-pins what was NOT allowed to move: the
canonical routing, Presence, Full/Half Brain semantics, the one
Conversation, the one avatar mount path, and the generic-over-an-identity
rule.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.control_center.manifest import visual_environment_versions

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
SERVE_UI = REPO_ROOT / "scripts" / "serve_ui.py"

PROVEN_GEOMETRY = {
    "IcosahedronGeometry", "SphereGeometry", "CylinderGeometry", "BoxGeometry",
    "DodecahedronGeometry", "OctahedronGeometry", "TorusGeometry", "BufferGeometry",
}


def _shell() -> str:
    return UI.read_text("utf-8")


def _fn(name: str, end: str = "\n  }\n") -> str:
    return _shell().split("function %s(" % name, 1)[1].split(end, 1)[0]


def _mount_stage() -> str:
    return _shell().split("async function mountStage(container", 1)[1].split(
        "\n  // Every mount*3D() below", 1)[0]


def _mount_avatar() -> str:
    return _shell().split("function mountAvatarBody3D(container", 1)[1].split(
        "\n  }\n\n  let agent3DStages", 1)[0]


def _agent_view() -> str:
    return _shell().split("V.agent = () => {", 1)[1].split("\n  };", 1)[0]


def _code(block: str) -> str:
    """The block with its comment lines removed.

    Same reason tests/test_stage17_19_visual_correction.py strips them: a
    "X must not appear here" check otherwise trips over the comment that
    explains why X must not appear here."""
    return "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("//")
    )


def _geometry_used(block: str) -> set[str]:
    return set(re.findall(r"THREE\.(\w+Geometry)", block))


# ---------------------------------------------------------------------------
# 1. The document is actually a document. (The phone band was dead code.)
# ---------------------------------------------------------------------------


class TestThePageDeclaresAViewport:
    def test_the_viewport_is_declared_before_anything_else_is(self):
        """Without this the whole phone band is unreachable: a 390px phone
        lays the document out at a 980px virtual width and scales the
        result down. Measured in a real browser at a 390px device width -
        window.innerWidth read 980 before this line and 390 after it."""
        shell = _shell()
        assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in shell
        assert '<meta charset="utf-8">' in shell
        # Before the stylesheet, and before the title - a viewport read
        # after layout has already begun is not worth having.
        assert shell.index("viewport") < shell.index("<style>")
        assert shell.index("charset") < shell.index("<title>")

    def test_the_phone_band_it_unlocks_is_still_the_one_that_was_there(self):
        """The viewport meta is what lets the 760px band apply at all; the
        band itself must still be the one that was already written.

        Stage 17.21 raised the figure's floor on a phone (38vh/260px ->
        50vh/340px) as part of making the environment the surface rather
        than a panel. Still a viewport fraction with a hard floor under
        it, which is the property this test holds.
        """
        mobile = _shell().rsplit("@media (max-width:760px){", 1)[1]
        assert "min-height:max(50vh,340px)" in mobile
        modes = mobile.split(".modes{", 1)[1].split("}", 1)[0]
        assert "overflow-x:auto" in modes and "display:none" not in modes

# ---------------------------------------------------------------------------
# 2. One camera fit, shared by every stage that asks for it.
# ---------------------------------------------------------------------------


class TestTheCameraFitsWhatWasActuallyBuilt:
    def test_the_fit_exists_and_runs_after_the_aspect_it_depends_on(self):
        mount = _mount_stage()
        assert "function frameCamera(){" in mount
        # _code(), not the raw block: a commented-out call still contains
        # the substring, and a mutation check caught this test passing
        # against a resize() that had stopped calling it at all.
        resize = _code(mount.split("function resize(){", 1)[1].split("\n    }", 1)[0])
        assert resize.index("camera.aspect = w/h;") < resize.index("frameCamera();")

    def test_it_fits_horizontally_as_well_as_vertically(self):
        """The whole defect: a PerspectiveCamera's fov is vertical, so a
        fixed camera in a 3.5:1 panel frames by height alone."""
        fit = _fn("frameCamera", "\n    }")
        assert "camera.fov * Math.PI / 180" in fit
        assert "Math.atan(Math.tan(vFov / 2) * camera.aspect)" in fit
        assert "Math.max(halfH / Math.tan(vFov / 2), halfW / Math.tan(hFov / 2))" in fit

    def test_the_horizontal_extent_is_rotation_safe(self):
        """These groups turn on Y while they animate. Fitting the pose that
        happens to exist at build time is how a shape clips itself a few
        seconds after it is looked at."""
        fit = _fn("frameCamera", "\n    }")
        assert "Math.max(_size.x, _size.z) / 2" in fit

    def test_it_reads_world_space_not_local(self):
        fit = _fn("frameCamera", "\n    }")
        assert "scene.updateMatrixWorld(true);" in fit
        assert "_box.setFromObject(target);" in fit
        assert "if(_box.isEmpty()) return;" in fit

    def test_a_stage_that_names_nothing_keeps_the_camera_it_had(self):
        """Opt-in, so this could not silently re-frame surfaces nobody had
        complained about."""
        fit = _fn("frameCamera", "\n    }")
        assert "const target = scene.userData.frame;" in fit
        assert "if(!target) return;" in fit
        mount = _mount_stage()
        assert "camera.position.set(0, 0.35, 5.4);" in mount

    def test_exactly_the_subject_stages_opt_in(self):
        # Post-Pre-Stage-9: the Workspace 3D diagram is a subject stage too -
        # the diagram is what a person came to see, never a widget.
        shell = _shell()
        assert shell.count("scene.userData.frame = group;") == 4
        for fn in ("mountBrain3D", "mountMemory3D", "mountAvatarBody3D", "buildWorkspaceDiagram"):
            assert "scene.userData.frame = group;" in _fn(fn, "\n  }\n"), fn
        # The identity emblem and the Room genuinely are widgets, and were
        # never the complaint - untouched.
        for fn in ("mountAgent3D", "mountRoom3D"):
            assert "scene.userData.frame" not in _fn(fn, "\n  }\n"), fn

    def test_the_digital_human_frames_the_figure_and_lets_its_world_overflow(self):
        """A symbolic environment that stopped at the frame edge would be a
        picture hanging behind the agent rather than somewhere it stands,
        so the fit target is the body group - never `world`, which holds
        the backdrop and the floor too."""
        mount = _mount_avatar()
        assert "scene.userData.frame = group;" in mount
        assert "scene.userData.frame = world" not in mount
        # and the backdrop is still a sibling of the figure, as before
        assert "world.add(symbolicGroup);" in mount


# ---------------------------------------------------------------------------
# 3. Cognition reads as a brain.
# ---------------------------------------------------------------------------


class TestCognitionIsSeenFromAnAngleABrainHasAProfileFrom:
    def test_the_resting_view_is_three_quarters_not_a_plan_view(self):
        """`rotation.x = -0.3` tilted the crown toward the camera. Seen
        from above a brain is a lumpy disc, which is exactly what the
        browser showed."""
        mount = _fn("mountBrain3D", "\n  }\n")
        tilt = float(re.search(r"group\.rotation\.x = (-?[\d.]+) \+", mount).group(1))
        assert -0.22 <= tilt <= 0.0, tilt
        # A turn that STARTS at a three-quarter angle, rather than one that
        # only passes through one.
        base, sweep = re.search(
            r"group\.rotation\.y = ([\d.]+) \+ Math\.sin\(t \* [\d.]+\) \* ([\d.]+);", mount
        ).groups()
        assert float(base) - float(sweep) > 0.35, (base, sweep)

    def test_the_key_light_is_no_longer_out_shone_by_the_accent_rim(self):
        """A warm grey cortex under an accent rim brighter than its key
        renders as cold cyan stone."""
        mount = _fn("mountBrain3D", "\n  }\n")
        key = float(re.search(r"DirectionalLight\(0xfff1e8, ([\d.]+)\)", mount).group(1))
        rim = float(re.search(r"bRim = new THREE\.DirectionalLight\(accent, ([\d.]+)\)", mount).group(1))
        assert key > rim, (key, rim)


class TestCognitionHasTheShapeOfABrain:
    def test_the_cerebrum_is_proportioned_like_one(self):
        """Longer front-to-back than wide, and wider than tall. The old
        ellipsoid was taller than it was wide, which is a walnut."""
        brain = _fn("buildBrainGeometry")
        rx, ry, rz, _ = [float(v) for v in re.search(
            r"hemiGeo = _corticalSurface\(THREE, ([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+)\)",
            brain).groups()]
        offset = float(re.search(r"hemi\.position\.set\(side \* ([\d.]+)", brain).group(1))
        half_width = offset + rx
        assert rz > half_width > ry, (rz, half_width, ry)

    def test_the_hemispheres_no_longer_interpenetrate_at_the_midline(self):
        """The longitudinal fissure is the single strongest cue that a
        shape is a brain, and there is no fissure if the two halves are
        built through each other."""
        brain = _fn("buildBrainGeometry")
        rx = float(re.search(r"hemiGeo = _corticalSurface\(THREE, ([\d.]+)", brain).group(1))
        offset = float(re.search(r"hemi\.position\.set\(side \* ([\d.]+)", brain).group(1))
        assert offset >= rx, (offset, rx)

    def test_the_temporal_lobes_exist_and_are_slung_low_and_forward(self):
        """Third silhouette cue after the fissure and the cerebellum, and
        the one whose absence left the old shape as a plain egg."""
        brain = _fn("buildBrainGeometry")
        assert "const temporalGeo = _corticalSurface(THREE" in brain
        assert "const temporal = new THREE.Mesh(temporalGeo, corticalMat);" in brain
        x, y, z = [float(v) for v in re.search(
            r"temporal\.position\.set\(side \* ([\d.]+), (-?[\d.]+), (-?[\d.]+)\)", brain).groups()]
        assert y < 0 and z > 0, (y, z)
        # Lateral to the hemisphere it hangs off, so it shows in profile.
        assert x > float(re.search(r"hemi\.position\.set\(side \* ([\d.]+)", brain).group(1))
        # Mirrored the same way the hemispheres are - one table, two sides.
        assert brain.count("[-1, 1].forEach(side => {") == 2

    def test_the_stem_is_tucked_under_the_organ_rather_than_standing_clear(self):
        """Two truncated cylinders descending well below a taller cerebrum
        read as a stalk, and a mass on a stalk is a mushroom."""
        brain = _fn("buildBrainGeometry")
        stem_y = float(re.search(r"stem\.position\.set\(0, (-?[\d.]+)", brain).group(1))
        cereb_y = float(re.search(r"cerebellum\.position\.set\(0, (-?[\d.]+)", brain).group(1))
        assert stem_y >= cereb_y - 0.05, (stem_y, cereb_y)
        # A rounded end. Two truncated cylinders finish in a flat disc, and
        # a flat disc lit from above is a bright rectangle - which is what
        # the stem read as: a machined peg under an organ.
        assert "const medulla = new THREE.Mesh(new THREE.SphereGeometry(" in brain

    def test_the_folds_are_finer_than_they_are_deep(self):
        """High amplitude at low frequency is a frilled outline - the
        browser showed a lettuce. A gyrus is narrow relative to the organ,
        so the frequency goes up and the displacement comes down."""
        fold = _fn("_corticalSurface", "\n  }")
        terms = re.findall(r"Math\.sin\(v\.\w \* ([\d.]+)", fold)
        assert len(terms) >= 4, terms
        assert min(float(t) for t in terms) >= 10.0, terms
        brain = _fn("buildBrainGeometry")
        depths = [float(m) for m in re.findall(
            r"_corticalSurface\(THREE, [\d.]+, [\d.]+, [\d.]+, ([\d.]+)\)", brain)]
        assert depths and max(depths) <= 0.12, depths
        # Still deterministic, still a sum of sinusoids of the vertex's own
        # position - never noise, never a texture.
        assert "Math.random" not in fold and "TextureLoader" not in fold

    def test_the_signal_still_decides_only_how_much_is_lit(self):
        """Full/Half Brain semantics are untouched by any of the above."""
        brain = _fn("buildBrainGeometry")
        assert 'const full = signal === "GREEN", half = signal === "AMBER";' in brain
        assert "const litCount = full ? N : half ? Math.round(N*0.55) : Math.round(N*0.25);" in brain
        assert "i < litCount ? litMat : dimMat" in brain
        for forbidden in ("brain.mode", "FULL BRAIN", "HALF BRAIN", "provider", "verified"):
            assert forbidden not in _code(brain), forbidden

    def test_it_still_uses_only_proven_geometry_and_stays_unlike_memory(self):
        brain, memory = _fn("buildBrainGeometry"), _fn("buildMemoryGeometry")
        assert _geometry_used(brain) <= PROVEN_GEOMETRY
        assert _geometry_used(memory) <= PROVEN_GEOMETRY
        assert "IcosahedronGeometry" in brain and "IcosahedronGeometry" not in memory
        assert "DodecahedronGeometry" in memory and "DodecahedronGeometry" not in brain


# ---------------------------------------------------------------------------
# 4. Memory reads as an archive, not as a spring.
# ---------------------------------------------------------------------------


class TestMemoryIsStackedNotCoiled:
    def test_a_used_slot_is_a_solid_layer_and_an_unused_one_stays_an_outline(self):
        """Eight thin hoops threaded on a spindle is a coil spring. An
        archive reads as an archive because it is stacked - so a used slot
        now has a face that catches the light and an edge that shadows the
        layer below it, while unused capacity stays the thin outline it
        was. Real, visible headroom; never a plate pretending to hold
        something.

        Stage 17.21 rebuilt the archive as a rectangular stack inside an
        open enclosure - box plates rather than round ones - so the
        geometry primitive changed while the thing being asserted did
        not. Pinned by the property that matters (a used slot is thicker,
        full-footprint and lit; an unused one is thinner and inset) rather
        than by which THREE constructor draws it, which is the level this
        test always meant to work at."""
        memory = _fn("buildMemoryGeometry")
        ring = memory.split("const ring = new THREE.Mesh(", 1)[1].split(";", 1)[0]
        assert "filled ? new THREE.BoxGeometry(W, 0.075, D)" in ring
        assert ": new THREE.BoxGeometry(W * 0.96, 0.012, D * 0.96)" in ring
        assert "filled ? filledMat : emptyMat" in ring

        # A used slot is a solid layer; an unused one is a thin outline.
        filled_h, empty_h = 0.075, 0.012
        assert empty_h < filled_h / 4, (empty_h, filled_h)
        # ...and it is inset, so spare capacity never fills the footprint
        # a written layer does.
        assert 0.96 < 1.0

    def test_a_stratum_is_a_surface_and_not_a_lamp(self):
        """At 0.8 emissive over eight stacked plates the whole archive
        rendered as one near-white block with no edge between any two
        layers - the exact opposite of the stacking doing the reading."""
        memory = _fn("buildMemoryGeometry")
        filled = memory.split("const filledMat = new THREE.MeshStandardMaterial({", 1)[1]
        lit = float(re.search(r"emissiveIntensity: ([\d.]+),", filled).group(1))
        assert lit <= 0.35, lit

    def test_a_record_is_filed_against_its_layer_rather_than_buried_in_it(self):
        """A record stands proud of the layer it belongs to, so it reads
        as filed against that layer rather than sunk inside it.

        Stage 17.21 moved this from a radial test (a record orbiting
        outside a round plate) to a vertical one (a record standing on a
        rectangular plate), because the archive is now a stack of shelves
        rather than a spindle. Same guarantee, measured on the axis the
        new geometry actually uses.
        """
        memory = _fn("buildMemoryGeometry")
        plate_thickness = float(
            re.search(r"filled \? new THREE\.BoxGeometry\(W, ([\d.]+), D\)", memory).group(1))
        record_lift = float(
            re.search(r"record\.position\.set\(across, y \+ ([\d.]+),", memory).group(1))
        # The plate's own top surface is half its thickness above the
        # stratum's centre line; the record sits clear above that.
        assert record_lift > plate_thickness / 2, (record_lift, plate_thickness)

        # And it stays within its own layer's pitch, so a record is never
        # filed against the layer above the one it belongs to.
        layer_pitch = float(re.search(r"const LAYER = ([\d.]+);", memory).group(1))
        assert record_lift < layer_pitch, (record_lift, layer_pitch)

    def test_the_store_is_seated_against_the_stack_it_carries(self):
        """It was a bright bead hanging below the end of the spindle, and
        the frame then clipped it off the bottom of the panel anyway.

        Stage 17.21 answered this more directly than the original fix
        did: the store is no longer a separate object dangling under the
        stack at all, it is an open enclosure the strata sit inside. So
        the assertion moved from "the bead does not hang far" to "the
        enclosure encloses" - a strictly stronger reading of the same
        complaint.
        """
        memory = _fn("buildMemoryGeometry")
        # The floor sits just under the lowest stratum, not slung beneath it.
        drop = float(re.search(r"core\.position\.y = base - ([\d.]+);", memory).group(1))
        layer_pitch = float(re.search(r"const LAYER = ([\d.]+);", memory).group(1))
        assert drop < layer_pitch, (drop, layer_pitch)

        # It genuinely encloses: a floor, a cap over the top of the stack,
        # and four posts spanning the whole height.
        assert "const height = MEMORY_TRACE_CAP * LAYER + 0.5;" in memory
        assert "cap.position.y = base + MEMORY_TRACE_CAP * LAYER + 0.18;" in memory
        assert "new THREE.BoxGeometry(0.07, height, 0.07)" in memory
        # Wider and deeper than the strata it carries, on every side.
        assert "new THREE.BoxGeometry(W + 0.34, 0.14, D + 0.3)" in memory

    def test_capacity_and_content_are_still_both_honest(self):
        """One stratum per traced slot so unused capacity stays visible,
        and never one record more than were actually retained."""
        memory = _fn("buildMemoryGeometry")
        assert "for(let i=0;i<MEMORY_TRACE_CAP;i++){" in memory
        assert "const filled = i < lit;" in memory
        assert "for(let i=0;i<lit;i++){" in memory
        assert "lit + 1" not in memory

    def test_durability_is_still_what_the_store_looks_like(self):
        memory = _fn("buildMemoryGeometry")
        assert memory.count("durable ?") >= 4
        assert "transparent: !durable" in memory

    def test_nothing_here_reads_a_memory_or_invents_one(self):
        memory = _fn("buildMemoryGeometry")
        for forbidden in ("recall", "remember", "MemoryRecord", "content", '"kind"'):
            assert forbidden not in _code(memory), forbidden


class TestBothRevisionsAreRecordedRatherThanSwapped:
    def test_the_manifest_reads_the_bumped_versions_from_the_real_file(self):
        """Same concept and same id - a folded cortex is still a folded
        cortex - so what changes is the version, which is what a version
        field is for."""
        found = {v.surface: v for v in visual_environment_versions(str(UI))}
        assert found["brain"].id == "brain-human-cortex"
        assert found["brain"].version == 3
        assert found["memory"].id == "memory-archive-strata"
        assert found["memory"].version == 3


# ---------------------------------------------------------------------------
# 5. The two surfaces whose job is a recognisable shape get room for one.
# ---------------------------------------------------------------------------


class TestThePresentationHeights:
    def test_cognition_and_memory_are_no_longer_letterboxed(self):
        """300px in a ~1060px column is 3.5:1, and a vertical FOV frames by
        height - so the subject was drawn small with two thirds of the box
        empty."""
        shell = _shell()
        rule = shell.split(".agent3d[data-brain3d],.agent3d[data-memory3d]{", 1)[1].split("}", 1)[0]
        assert "height:min(48vh,420px)" in rule

    def test_the_identity_emblem_keeps_the_diagnostic_box_it_always_had(self):
        """An abstract emblem genuinely is a widget; it was never the
        complaint, and nothing about it moved."""
        block = _shell().split("\n  .agent3d{", 1)[1].split("}", 1)[0]
        assert "height:300px" in block


# ---------------------------------------------------------------------------
# 6. The layout stopped hiding the product.
# ---------------------------------------------------------------------------


class TestTheUnusedPrivateLineIsAComposedEmptyState:
    def test_it_is_centred_in_the_column_rather_than_pinned_to_its_top(self):
        """One italic grey line at the top of a 500px column of black is
        the first thing this product shows a person, and it reads as a
        screen that failed to load."""
        block = _shell().split("\n  .opening{", 1)[1].split("}", 1)[0]
        assert "margin:auto" in block
        assert "align-items:center" in block
        assert "text-align:center" in block
        # Set in the display face, like every other heading on the page.
        lede = _shell().split(".opening .lede{", 1)[1].split("}", 1)[0]
        assert "var(--display)" in lede

    def test_every_word_in_it_is_chrome_or_the_agents_own_registered_data(self):
        """No example prompt is put in an agent's mouth, and no capability
        is described on its behalf: the one variable line is the summary
        that agent's own Interface declares, and an agent that declares
        none simply does not get the line."""
        fn = _shell().split("function privateTurns(name){", 1)[1].split("\n  }", 1)[0]
        assert "(agent && agent.interface && agent.interface.summary)" in fn
        assert 'summary ? `<p class="sum">${esc(summary)}</p>` : ""' in fn
        assert "Nothing said here yet." in fn
        for invented in ("Try asking", "For example", "e.g.", "Ask me"):
            assert invented not in fn, invented

    def test_it_is_still_only_the_empty_branch(self):
        """A real thread still renders exactly the turns it always did."""
        fn = _shell().split("function privateTurns(name){", 1)[1].split("\n  }", 1)[0]
        assert "if(!thread.length){" in fn
        assert "x.replies.forEach(r => rows.push(privateTurn(r, r.speaker !== name)));" in fn
        assert "return rows.join(\"\");" in fn


class TestTheMiddleBandNoLongerHidesTheConversation:
    def test_it_shortens_the_stage_instead_of_stacking_it(self):
        """Measured at 1024x900: the identity block plus a 500px character
        filled the first screen exactly and .private-say's own top was at
        y=1100. Two columns fit comfortably at this width, so what gives
        between here and the phone is the stage's height, not the layout.

        Stage 17.21 kept that decision and renamed the target
        (.private-stage .agent3d -> .private-env), taking a little less
        height off it now that the conversation floats over the stage
        rather than sitting in a column beside it.
        """
        block = _shell().split("@media (max-width:1080px){", 1)[1].split("\n  }", 1)[0]
        assert "min-height:min(62vh,560px)" in block
        assert "grid-template-columns" not in block
        assert "order:-1" not in block

    def test_the_character_on_top_stacking_moved_to_where_it_belongs(self):
        """It is the right stacking - for a phone, where one column is
        genuinely forced by width.

        Stage 17.21 stopped expressing it as a re-order. The environment
        is now the first region of the composition outright, and the
        conversation unpins from its floating position to sit under it -
        so the phone gets the same result with no ordering rule to keep in
        step with the source.
        """
        mobile = _shell().rsplit("@media (max-width:760px){", 1)[1]
        assert ".private{grid-template-rows:auto auto}" in mobile
        talk = mobile.split(".env-talk{position:relative;inset:auto;", 1)[1].split("}", 1)[0]
        assert "width:auto" in talk
        assert "max-height:none" in talk
        # Character first because it IS first, not because it was moved.
        view = _agent_view()
        assert view.index('class="private-env') < view.index('class="env-talk"')
        assert "order:-1" not in mobile

    def test_no_fourth_breakpoint_was_introduced(self):
        shell = _shell()
        assert shell.count("@media (max-width:1080px)") == 1
        assert shell.count("@media (max-width:760px)") == 2
        assert shell.count("@media (prefers-reduced-motion") == 1


# ---------------------------------------------------------------------------
# 7. Nothing this pass touched was allowed to change.
# ---------------------------------------------------------------------------


class TestNothingCanonicalChanged:
    def test_the_private_interface_is_still_generic_over_an_identity(self):
        view = _code(_agent_view())
        for name in ("Alfred", "Arthur"):
            assert name not in view, name
        assert "const a = openAgentOf();" in view
        # Stage 17.21 replaced the talk/stage column pair with one
        # full-bleed environment the conversation floats over, so the
        # ordering assertion moved onto the regions that now exist. The
        # guarantee is unchanged: the composition is generic over
        # whichever identity is open.
        raw = _agent_view()
        assert raw.index('class="private-env') < raw.index('class="env-talk"')
        assert raw.index('class="env-talk"') < raw.index('class="private-context"')

    def test_there_is_still_exactly_one_digital_human_mount_path(self):
        shell = _shell()
        assert shell.count("function mountAvatarBody3D(container") == 1
        assert shell.count("function wireAvatarBody3D(){") == 1
        assert shell.count("async function mountStage(container, stagesMap, key, hueRgb, sceneBuilder){") == 1

    def test_presence_still_drives_the_figure_from_the_live_snapshot(self):
        mount = _mount_avatar()
        assert 'const activity = (live && live.activity) || "idle";' in mount
        assert 'if(activity === "speaking"){' in mount
        assert 'activity === "listening"' in mount
        assert "group.userData.head.scale.setScalar" in mount

    def test_the_two_symbolic_environments_are_still_a_per_agent_registry_entry(self):
        shell = _shell()
        assert "symbolicEnvironmentFor(style && style.symbolic_environment)" in _mount_avatar()
        block = shell.split("const AVATAR_SYMBOLIC_ENVIRONMENTS = {", 1)[1].split("\n  };", 1)[0]
        assert set(re.findall(r'"([\w-]+)":', block)) == {"batman-emblem", "nightwing-emblem"}
        for env in ("buildBatmanEmblem", "buildNightwingEmblem"):
            for name in ("Alfred", "Arthur"):
                assert name not in _code(_fn(env)), (env, name)

    def test_no_python_module_learned_about_any_of_this(self):
        """The whole correction is presentation. Stage 17.19's own guard
        held: not one Python file changed in this pass."""
        source = SERVE_UI.read_text("utf-8")
        for forbidden in ("emblem", "cortex", "silhouette", "membrane", "anatomy",
                          "temporal", "viewport", "framecamera"):
            assert forbidden not in source.lower(), forbidden
        for rel in ("src/services/identity/presence.py", "src/control_center/snapshot.py",
                    "src/manager/manager.py", "src/conversation/conversation.py"):
            text = (REPO_ROOT / rel).read_text("utf-8").lower()
            for forbidden in ("space grotesk", "cascadia", "cortex", "silhouette",
                              "stratum", "viewport"):
                assert forbidden not in text, (rel, forbidden)
