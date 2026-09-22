"""Stage 17.19 - the visual correction pass.

Stage 17.18 built the private agent interface's *architecture* correctly and
its *presentation* badly. Looked at in a real browser rather than asserted
about in source, it was: a Digital Human that read as a stack of primitives
with no neck and no shoulders on a roughly three-heads-tall body; a symbolic
environment that was a bright flat plate directly behind the figure's head,
obscuring the human it was supposed to stand behind; a Cognition surface
drawing a node lattice, which is a picture of a network and not of a brain;
a Memory surface drawing one small polyhedron with a few specks around it;
and a typography "system" that shipped one monospace face and set the entire
product in it, which is what a terminal looks like.

This file guards the corrections. Every claim below was checked by rendering
the real served page in a real headless Chromium and looking at it - the
assertions here pin the source-level structure that produced what was seen,
which is the part a future change can silently break.

What did NOT change, and is re-pinned here rather than taken on trust: the
canonical routing, the Presence states, Full/Half Brain semantics, the one
Conversation, the one avatar mount path, and the generic-over-an-identity
rule that lets a third agent get all of this without a line changing.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.control_center.manifest import visual_environment_versions

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
VENDOR = REPO_ROOT / "ui" / "vendor"
SERVE_UI = REPO_ROOT / "scripts" / "serve_ui.py"

PROVEN_GEOMETRY = {
    "IcosahedronGeometry", "SphereGeometry", "CylinderGeometry", "BoxGeometry",
    "DodecahedronGeometry", "OctahedronGeometry", "TorusGeometry", "BufferGeometry",
}


def _shell() -> str:
    return UI.read_text("utf-8")


def _fn(name: str, end: str = "\n  }\n") -> str:
    return _shell().split(f"function {name}(", 1)[1].split(end, 1)[0]


def _body_builder() -> str:
    return _shell().split("function buildHumanoidBody(THREE", 1)[1].split(
        "\n  const AVATAR_BODY_VISUAL_ENVIRONMENTS", 1)[0]


def _mount_avatar() -> str:
    return _shell().split("function mountAvatarBody3D(container", 1)[1].split(
        "\n  }\n\n  let agent3DStages", 1)[0]


def _agent_view() -> str:
    return _shell().split("V.agent = () => {", 1)[1].split("\n  };", 1)[0]


def _geometry_used(block: str) -> set[str]:
    return set(re.findall(r"THREE\.(\w+Geometry)", block))


def _code(block: str) -> str:
    """The block with its comment lines removed.

    This file is asserting on what the renderer *does*, and this codebase
    comments heavily enough that an "X must not appear here" check will
    otherwise trip over the comment that explains why X must not appear
    here. Scoped the same way tests/test_stage17_15_composer_bugfix.py
    already scopes its own wording check to the template expression rather
    than to the whole file."""
    return "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("//")
    )


# ---------------------------------------------------------------------------
# 1. Composition: conversation left, a large presentation stage right.
# ---------------------------------------------------------------------------


class TestTheComposition:
    def test_the_conversation_is_a_real_region_of_the_source_not_an_afterthought(self):
        """Source order is the composition on a phone and for a screen
        reader, so it has to be right there too - not only in the grid.

        Stage 17.19 put the conversation first and the character second.
        Stage 17.21 deliberately reversed that: the environment became the
        surface rather than a panel beside one, so on a phone the figure
        is what a person meets first and the conversation follows it as
        its own block ("never demoted to a thumbnail above a form", in the
        stylesheet's own words). That is a design reversal, recorded here
        rather than quietly dropped.

        What did NOT change, and is what this test now holds: the
        conversation is a real, first-class region of the rendered source
        - present, in order, before the surrounding context - never
        injected later by script.
        """
        view = _agent_view()
        assert 'class="env-talk"' in view
        assert 'class="private-transcript"' in view
        # The environment is met first, the conversation is a layer on it,
        # and the context row follows both.
        assert view.index('class="private-env') < view.index('class="env-talk"')
        assert view.index('class="env-talk"') < view.index('class="private-context"')

    def test_the_conversation_gets_a_deliberate_share_never_an_intrinsic_one(self):
        """Intrinsic sizing cannot say "this panel is exactly this big",
        and the composition needs exactly that.

        Stage 17.19 said it with two unequal grid columns; Stage 17.21
        replaced the pair with one full-bleed environment the conversation
        floats over, so the guarantee moved onto the panel: a fixed
        fraction of the width with a hard ceiling, pinned to one side, and
        never able to cover the character it sits over.
        """
        block = _shell().split(".env-talk{position:absolute", 1)[1].split("}", 1)[0]
        assert "width:min(46%,540px)" in block
        assert "max-height:66%" in block
        assert "auto-fit" not in block
        # The environment keeps the larger share, as the character did.
        share = float(re.search(r"width:min\((\d+)%", block).group(1))
        assert share < 100 - share, share

    def test_the_stage_is_sized_as_a_presentation_area_not_a_widget(self):
        """The generic diagnostic box is a fixed 300px. This one takes a
        share of the viewport and all the height its track has left.

        Stage 17.21 renamed it (.private-stage .agent3d -> .private-env,
        with the canvas filling it absolutely) and made it larger still.
        """
        env = _shell().split(".private-env{position:relative;", 1)[1].split("}", 1)[0]
        assert "min-height:min(72vh,720px)" in env
        # The canvas fills the environment rather than carrying its own
        # fixed box.
        canvas = _shell().split(".private-env .agent3d{", 1)[1].split("}", 1)[0]
        assert "position:absolute" in canvas
        assert "inset:0" in canvas
        assert "height:auto" in canvas
        # And it is a share of the viewport, never a fixed pixel widget.
        assert "300px" not in env

    def test_the_context_row_no_longer_prints_every_router_keyword(self):
        """Fifty-seven capability chips under the conversation is a
        dashboard, not a product surface. The real count is still shown,
        and Control Center still lists every one."""
        shell = _shell()
        assert "const CONTEXT_CHIP_LIMIT = 12;" in shell
        view = _agent_view()
        assert "a.capabilities.slice(0, CONTEXT_CHIP_LIMIT)" in view
        assert "a.capabilities.length - CONTEXT_CHIP_LIMIT" in view


# ---------------------------------------------------------------------------
# 2. The Digital Human reads as human.
# ---------------------------------------------------------------------------


class TestTheDigitalHumanIsAnatomical:
    def test_the_parts_a_body_needs_all_exist(self):
        """The previous body had a head, a barrel, two straight arms and
        two straight legs. Everything between those - the parts that make a
        figure read as a person rather than as an assembly - was missing."""
        body = _body_builder()
        for part in (
            "const neck =", "const clavicle =", "const deltoidGeo =",
            "const torso =", "const waist =", "const pelvis =",
            "const upperArmGeo =", "const elbow = new THREE.Group();",
            "const foreArmGeo =", "const handGeo =",
            "const thighGeo =", "const kneePivot = new THREE.Group();",
            "const shinGeo =", "const footGeo =",
            "const brow =", "const cheekGeo =", "const nose =", "const jaw =",
        ):
            assert part in body, part

    def test_the_proportions_are_a_persons_not_a_dolls(self):
        """One landmark table, and a head that is about a sixth of the
        figure rather than a third of it. Read off ANATOMY itself, so a
        future adjustment that breaks the proportions fails here."""
        shell = _shell()
        block = shell.split("const ANATOMY = {", 1)[1].split("};", 1)[0]
        marks = dict(re.findall(r"(\w+): (-?[\d.]+)", block))
        marks = {k: float(v) for k, v in marks.items()}
        total = marks["crown"] - marks["footBottom"]
        head_height = marks["crown"] - marks["chin"]
        assert 5.5 <= total / head_height <= 7.5, total / head_height
        # Feet at the bottom, crown at the top, and every joint in order.
        order = ["footBottom", "ankle", "knee", "hip", "pelvis", "waist",
                 "chest", "shoulder", "neck", "chin", "head", "crown"]
        values = [marks[k] for k in order]
        assert values == sorted(values), list(zip(order, values))
        # The figure is centred near the origin rather than floating in the
        # upper half of the frame, which is how the camera sees all of it.
        assert abs((marks["crown"] + marks["footBottom"]) / 2) < 0.25

    def test_every_landmark_is_read_from_the_one_table(self):
        """No part may place itself at a number written independently of
        the skeleton - that is how the old body's features drifted."""
        body = _body_builder()
        assert "const A = ANATOMY;" in body
        assert body.count("A.") >= 12

    def test_the_head_is_a_real_cranium_not_a_faceted_ball(self):
        body = _body_builder()
        assert "const head = new THREE.Mesh(ellipsoid(" in body
        # Baked into the geometry, never mesh.scale - mountAvatarBody3D's
        # speaking animation calls head.scale.setScalar(), which would
        # destroy a non-uniform mesh scale.
        ell = _shell().split("const ellipsoid = (rx, ry, rz, seg) => {", 1)[1].split("};", 1)[0]
        assert "g.scale(rx, ry, rz);" in ell
        assert "head.scale" not in _code(body)

    def test_the_stage_lights_the_figure_for_a_face_to_read(self):
        """One accent-coloured key washes a face into a single flat tone
        with no shadow under the brow or beside the nose. These three are
        added to this scene only - mountStage is untouched."""
        mount = _mount_avatar()
        for light in ("const keyNeutral =", "const rim =", "const bounce ="):
            assert light in mount, light
        assert "const floor =" in mount and "const floorRing =" in mount

    def test_only_proven_geometry_constructors_are_used(self):
        used = _geometry_used(_body_builder())
        assert used
        assert used <= PROVEN_GEOMETRY, used - PROVEN_GEOMETRY

    def test_the_existing_presence_behaviour_is_untouched(self):
        """LISTENING/SPEAKING/idle still drive the same animation, read
        fresh from the same live snapshot, and the arms still ease home to
        the resting angle the builder actually built."""
        mount = _mount_avatar()
        assert 'const activity = (live && live.activity) || "idle";' in mount
        assert 'if(activity === "speaking"){' in mount
        assert 'activity === "listening"' in mount
        assert "const restZ = arms.map(p => p.rotation.z);" in mount
        assert "group.userData.head.scale.setScalar" in mount


# ---------------------------------------------------------------------------
# 3. Two large, independent symbolic environments - behind the human.
# ---------------------------------------------------------------------------


class TestTheSymbolicEnvironments:
    def test_each_environment_brings_its_own_backdrop_to_read_against(self):
        """A dark shape floating in front of a black background has no
        outline at all. Each environment builds an unlit graded dome, and
        the silhouette is read against it."""
        shell = _shell()
        assert "function _symbolicDome(THREE, accent, radius){" in shell
        for env in ("buildBatmanEmblem", "buildNightwingEmblem"):
            block = _fn(env)
            assert "_symbolicDome(THREE, accent" in block, env
        dome = _fn("_symbolicDome")
        assert "side: THREE.BackSide" in dome
        assert "vertexColors: true" in dome
        # The grade is computed from each vertex's own height - no image,
        # no texture, no external asset.
        assert "pos.getY(i)" in dome
        assert "TextureLoader" not in dome and "Math.random" not in dome

    def test_the_silhouette_material_cannot_be_washed_out_by_the_figures_lights(self):
        """Fully metallic and fully rough: no diffuse response at all, so
        the key and rim lights that exist for the human cannot light this
        into a pale cut-out in front of them."""
        mat = _fn("_silhouetteMaterial", "\n  }")
        assert "color: 0x000000" in mat
        assert "metalness: 1" in mat
        assert re.search(r"roughness: 0\.9\d", mat)

    def test_both_are_large_enough_to_be_an_environment(self):
        """A badge sits behind a head. An environment runs past the edges
        of the frame. Read off each outline's own far end."""
        for env in ("buildBatmanEmblem", "buildNightwingEmblem"):
            block = _fn(env)
            edge = block.split("const edge = u => {", 1)[1].split("};", 1)[0]
            # x at the wing tip
            span = float(re.search(r"const x = [\d.]+ \+ u \* ([\d.]+);", edge).group(1))
            assert span >= 6.0, (env, span)

    def test_the_two_are_genuinely_different_forms_not_a_recolour(self):
        batman, nightwing = _fn("buildBatmanEmblem"), _fn("buildNightwingEmblem")
        b_used, n_used = _geometry_used(batman), _geometry_used(nightwing)
        assert b_used <= PROVEN_GEOMETRY and n_used <= PROVEN_GEOMETRY
        assert b_used - n_used, "the bat form uses no constructor the other does not"
        assert n_used - b_used, "the other form uses no constructor the bat does not"
        # Different outlines, and opposite gestures: one wing falls away
        # from its root, the other rises.
        b_edge = batman.split("const edge = u => {", 1)[1].split("};", 1)[0]
        n_edge = nightwing.split("const edge = u => {", 1)[1].split("};", 1)[0]
        assert b_edge != n_edge
        assert "- u*u*" in b_edge, "the bat wing must fall away toward its tip"
        assert "Math.pow(u, 0.75) * 3.6" in n_edge, "the other wing must rise"
        # Only one of them has a cowl and horns; only the other has a ring.
        assert "horn" in batman and "horn" not in nightwing
        assert "TorusGeometry" in nightwing and "TorusGeometry" not in batman
        # And each turns at its own declared rate, so two agents' backdrops
        # never move in lockstep.
        b_rate = re.search(r"spinRate = ([\d.]+)", batman).group(1)
        n_rate = re.search(r"spinRate = ([\d.]+)", nightwing).group(1)
        assert b_rate != n_rate

    def test_the_membrane_is_real_vertices_not_a_stack_of_boxes(self):
        """A box can only be a rectangle, so a strip of them leaves a stair
        on every edge - which is exactly how the first attempt at these
        wings rendered."""
        mem = _fn("_membrane", "\n  }")
        assert "new THREE.BufferGeometry()" in mem
        assert 'geo.setAttribute("position"' in mem
        assert "computeVertexNormals" in mem

    def test_neither_names_an_agent(self):
        for env in ("buildBatmanEmblem", "buildNightwingEmblem", "_symbolicDome"):
            block = _code(_fn(env))
            for name in ("Alfred", "Arthur"):
                assert name not in block, (env, name)

    def test_which_agent_gets_which_is_the_existing_per_agent_seam(self):
        """No new architecture: an agent's symbolic environment is still
        one value in its own DigitalHumanProfile, resolved through the same
        registry lookup, written through the same governed POST /profile."""
        shell = _shell()
        assert "symbolicEnvironmentFor(style && style.symbolic_environment)" in _mount_avatar()
        block = shell.split("const AVATAR_SYMBOLIC_ENVIRONMENTS = {", 1)[1].split("\n  };", 1)[0]
        assert set(re.findall(r'"([\w-]+)":', block)) == {"batman-emblem", "nightwing-emblem"}
        # A third agent's own environment is one new registry entry, never
        # a change at the call site.
        lookup = shell.split("const symbolicEnvironmentFor = key =>", 1)[1].split("\n\n", 1)[0]
        assert "|| null" in lookup


class TestTheLayeringPutsTheHumanInFront:
    def test_the_symbol_is_a_sibling_of_the_body_never_a_child(self):
        mount = _mount_avatar()
        assert "world.add(group);" in mount
        assert "world.add(symbolicGroup);" in mount
        assert "group.add(symbolicGroup)" not in mount
        assert "symbolic" not in _code(_body_builder()).lower()

    def test_the_symbol_sits_behind_the_figure_at_real_depth(self):
        """Not a plate at z=-1.8 directly behind the head. Every part of
        both environments is built at least three units back."""
        mount = _mount_avatar()
        depth = float(re.search(r"symbolicEnv\.build\(THREE, accent, ([\d.]+)\)", mount).group(1))
        assert depth >= 3.0
        for env in ("buildBatmanEmblem", "buildNightwingEmblem"):
            block = _fn(env)
            assert "const back = depth || " in block
            # nothing is placed in front of the figure
            assert "-back" in block

    def test_one_world_group_keeps_figure_floor_and_backdrop_in_register(self):
        mount = _mount_avatar()
        assert "const world = new THREE.Group();" in mount
        assert "world.scale.setScalar(" in mount
        for member in ("world.add(group);", "world.add(floor);", "world.add(symbolicGroup);"):
            assert member in mount, member


# ---------------------------------------------------------------------------
# 4. Cognition reads as a brain; Memory reads as memory. Both still
#    driven by the data they always were.
# ---------------------------------------------------------------------------


class TestCognitionReadsAsABrain:
    def test_it_is_a_folded_cortex_with_the_features_a_brain_has(self):
        brain = _fn("buildBrainGeometry")
        assert "_corticalSurface(THREE" in brain
        assert "const cerebellum =" in brain
        assert "const stem =" in brain
        # Two hemispheres, genuinely mirrored, with a gap between them.
        assert "[-1, 1].forEach(side => {" in brain
        assert "hemi.scale.x = side;" in brain
        offset = float(re.search(r"hemi\.position\.set\(side \* ([\d.]+)", brain).group(1))
        assert offset > 0.2, "the longitudinal fissure needs a real gap"

    def test_the_folds_are_real_displaced_vertices_and_deterministic(self):
        fold = _fn("_corticalSurface", "\n  }")
        assert "geo.attributes.position" in fold
        assert "pos.setXYZ" in fold
        assert "computeVertexNormals" in fold
        assert "Math.random" not in fold
        # A sum of sinusoids of the vertex's own position - ridges and the
        # grooves between them, not noise.
        assert fold.count("Math.sin(") >= 3

    def test_the_only_thing_the_signal_decides_is_how_much_is_lit(self):
        """Full/Half Brain semantics are untouched: this surface still
        reads the indicator assess_cognition produced and lights nodes by
        it, exactly as the node lattice did."""
        brain = _fn("buildBrainGeometry")
        assert 'const full = signal === "GREEN", half = signal === "AMBER";' in brain
        assert "const litCount = full ? N : half ? Math.round(N*0.55) : Math.round(N*0.25);" in brain
        assert "i < litCount ? litMat : dimMat" in brain
        # It never re-derives the mode, and never sees the agent.
        for forbidden in ("brain.mode", "FULL BRAIN", "HALF BRAIN", "provider", "verified"):
            assert forbidden not in _code(brain), forbidden

    def test_it_stays_visually_distinct_from_memory(self):
        brain = _fn("buildBrainGeometry")
        memory = _fn("buildMemoryGeometry")
        assert "DodecahedronGeometry" in memory
        assert "DodecahedronGeometry" not in brain
        assert "IcosahedronGeometry" in brain
        assert "IcosahedronGeometry" not in memory
        assert _geometry_used(brain) <= PROVEN_GEOMETRY
        assert _geometry_used(memory) <= PROVEN_GEOMETRY


class TestMemoryReadsAsAnArchive:
    def test_it_shows_strata_records_traces_and_a_store(self):
        memory = _fn("buildMemoryGeometry")
        assert "const spine =" in memory          # what records are filed against
        assert "const core =" in memory           # the store itself
        assert "const ring = new THREE.Mesh(" in memory   # one stratum per slot
        assert "const record = new THREE.Mesh(recordGeo, recordMat);" in memory
        assert "const trace = new THREE.Mesh(" in memory  # record -> spine

    def test_capacity_and_content_are_both_honest(self):
        """One stratum per traced slot so unused capacity is visible, and
        never one record more than were actually retained."""
        memory = _fn("buildMemoryGeometry")
        assert "for(let i=0;i<MEMORY_TRACE_CAP;i++){" in memory
        assert "const filled = i < lit;" in memory
        assert "for(let i=0;i<lit;i++){" in memory
        assert "lit + 1" not in memory and "MEMORY_TRACE_CAP;i++" in memory

    def test_durability_is_what_the_store_looks_like(self):
        memory = _fn("buildMemoryGeometry")
        assert memory.count("durable ?") >= 4
        assert "transparent: !durable" in memory

    def test_nothing_here_reads_a_memory_or_invents_one(self):
        memory = _fn("buildMemoryGeometry")
        for forbidden in ("recall", "remember", "MemoryRecord", "content", "\"kind\""):
            assert forbidden not in _code(memory), forbidden


class TestBothReplacementsAreRecordedInTheRegistry:
    def test_the_manifest_reads_the_new_ids_and_versions_from_the_real_file(self):
        found = {v.surface: v for v in visual_environment_versions(str(UI))}
        # Stage 17.20 re-rendered both surfaces (the cortex was seen from
        # above and read as a lumpy disc; the archive read as a coil
        # spring) - same concept, same id, a bumped version. See
        # tests/test_stage17_20_live_visual_correction.py.
        assert found["brain"].id == "brain-human-cortex"
        assert found["brain"].version == 3
        assert found["memory"].id == "memory-archive-strata"
        assert found["memory"].version == 3


# ---------------------------------------------------------------------------
# 5. Typography: three shipped faces, three jobs.
# ---------------------------------------------------------------------------


class TestTheTypographySystem:
    FACES = {
        "Cascadia Mono": "CascadiaMono.ttf",
        "Space Grotesk": "SpaceGrotesk.woff2",
        "Inter": "Inter.woff2",
    }

    def test_every_declared_face_is_vendored_and_declared_and_served(self):
        shell = _shell()
        source = SERVE_UI.read_text("utf-8")
        for family, filename in self.FACES.items():
            assert (VENDOR / filename).exists(), filename
            assert (VENDOR / filename).stat().st_size > 10_000, filename
            assert f'font-family:"{family}";' in shell, family
            assert f'url("vendor/{filename}")' in shell, filename
            assert f'"/vendor/{filename}"' in source, filename

    def test_the_licences_ship_with_the_fonts(self):
        for licence in ("INTER_LICENSE", "SPACE_GROTESK_LICENSE"):
            text = (VENDOR / licence).read_text("utf-8", errors="replace")
            assert "SIL Open Font License" in text, licence

    def test_the_three_faces_have_three_different_jobs(self):
        """The failure this corrects is not "the wrong font" - it is one
        font doing every job, which is what makes an interface look like a
        terminal. Identity and headings are set in the display face, prose
        and controls in the text face, and the mono is scoped to metadata."""
        shell = _shell()
        assert '--display:"Space Grotesk"' in shell
        assert '--font:"Inter"' in shell
        assert '--mono:"Cascadia Mono"' in shell
        # The agent's own name, the section headings and the roster names
        # are all set in the display face.
        # .private-id .nm -> .env-top .nm in Stage 17.21's rename; the
        # agent's own name is still set in the display face.
        for rule in (".env-top .nm{", ".slab h2{", ".band .head .t{", ".mode .lbl{"):
            block = shell.split(rule, 1)[1].split("}", 1)[0]
            assert "var(--display)" in block, rule
        # Conversation text is not set in mono.
        turn = shell.split(".turn p{", 1)[1].split("}", 1)[0]
        assert "var(--mono)" not in turn

    def test_the_scale_has_a_step_per_level_of_the_hierarchy(self):
        shell = _shell()
        block = shell.split(":root{", 1)[1].split("}", 1)[0]
        steps = re.findall(r"--t-(\w+):([^;]+);", block)
        assert [name for name, _ in steps] == ["agent", "head", "body", "control", "meta"]
        # Strictly descending, so the hierarchy is real rather than five
        # names for one size.
        def smallest(expr):
            return float(re.findall(r"([\d.]+)px", expr)[0])
        sizes = [smallest(v) for _, v in steps]
        assert sizes == sorted(sizes, reverse=True), steps


# ---------------------------------------------------------------------------
# 6. Responsive, and the defects a real browser found.
# ---------------------------------------------------------------------------


class TestResponsive:
    def test_three_bands_desktop_tablet_phone(self):
        shell = _shell()
        assert shell.count("@media (max-width:1080px)") == 1
        assert shell.count("@media (max-width:760px)") == 2
        assert shell.count("@media (prefers-reduced-motion") == 1

    def test_the_middle_band_shortens_the_stage_rather_than_stacking_it(self):
        """Stage 17.20 measured this band in a real browser at 1024x900
        and found the whole conversation - transcript AND composer - below
        the fold, because the band was stacking a 500px character on top
        of it. It no longer stacks; what it does is take height off the
        stage.

        Stage 17.21 kept that behaviour exactly and renamed the target
        (.private-stage .agent3d -> .private-env), widening the floating
        conversation panel to match the narrower width.
        """
        block = _shell().split("@media (max-width:1080px){", 1)[1].split("\n  }", 1)[0]
        assert "min-height:min(62vh,560px)" in block
        assert "grid-template-columns" not in block
        assert "order:-1" not in block
        # The panel widens because there is less width to hold it clear
        # of the figure.
        assert "width:min(56%,460px)" in block

    def test_the_phone_puts_the_character_first_and_the_conversation_under_it(self):
        """One column is genuinely forced by width at 390px.

        Stage 17.19 achieved this by ordering the stage above the talk
        column; Stage 17.21 achieves it structurally instead - the
        environment IS the first region, and the conversation unpins from
        its floating position to become its own block underneath. Same
        outcome on the phone, one less rule to keep in step.
        """
        mobile = _shell().rsplit("@media (max-width:760px){", 1)[1]
        assert ".private{grid-template-rows:auto auto}" in mobile
        talk = mobile.split(".env-talk{position:relative;inset:auto;", 1)[1].split("}", 1)[0]
        assert "width:auto" in talk
        assert "max-height:none" in talk
        # The character is first because it is the first region, not
        # because a rule reordered it.
        view = _agent_view()
        assert view.index('class="private-env') < view.index('class="private-context"')

    def test_the_phone_keeps_the_digital_human_substantial(self):
        """A viewport fraction with a floor under it, so a short landscape
        phone cannot shrink the figure into the 200px-widget failure this
        correction pass exists to remove. Stage 17.21 raised it."""
        mobile = _shell().rsplit("@media (max-width:760px){", 1)[1]
        rule = [ln for ln in mobile.splitlines() if ".private-env{min-height:" in ln][0]
        assert "min-height:max(50vh,340px)" in rule

    def test_the_phone_navigation_scrolls_instead_of_overflowing_the_page(self):
        """Eight surfaces cannot share 390px of width. Forcing them into
        one flex row pushed the whole document wider than the viewport -
        found by measuring document.scrollWidth in a real browser at
        390x844, not by looking at the CSS."""
        mobile = _shell().rsplit("@media (max-width:760px){", 1)[1]
        modes = mobile.split(".modes{", 1)[1].split("}", 1)[0]
        assert "overflow-x:auto" in modes
        assert "flex-wrap:nowrap" in modes
        mode = mobile.split("\n    .mode{", 1)[1].split("}", 1)[0]
        assert "flex:0 0 auto" in mode
        # Nothing was removed to make it fit.
        assert "display:none" not in modes


class TestTheControlCenterSatelliteRegression:
    def test_a_satellite_resets_the_user_agents_own_button_styling(self):
        """Every specialist card rendered as a pale grey box with its own
        labels unreadable, because .sat is a <button> and this rule never
        reset the UA's background and border. Confirmed by reading the
        computed style in a real browser - background was rgb(240,240,240)
        under the glass panel - not inferred from the screenshot."""
        block = _shell().split("\n  .sat{", 1)[1].split("}", 1)[0]
        for prop in ("appearance:none", "background:none", "border:none",
                     "color:inherit", "font:inherit"):
            assert prop in block, prop

    def test_the_same_reset_the_other_real_buttons_already_have(self):
        shell = _shell()
        mode = shell.split("\n  .mode{", 1)[1].split("}", 1)[0]
        assert "appearance:none" in mode and "background:none" in mode


# ---------------------------------------------------------------------------
# 7. Nothing this pass touched was allowed to change.
# ---------------------------------------------------------------------------


class TestNothingCanonicalChanged:
    def test_the_private_interface_is_still_generic_over_an_identity(self):
        view = _code(_agent_view())
        for name in ("Alfred", "Arthur"):
            assert name not in view, name
        assert "const a = openAgentOf();" in view

    def test_the_conversation_room_is_still_its_own_surface(self):
        shell = _shell()
        assert shell.count("V.room = () => {") == 1
        assert shell.count("V.agent = () => {") == 1
        room = shell.split("V.room = () => {", 1)[1].split("\n  };", 1)[0]
        assert 'class="who-picker"' in room and 'class="presence"' in room
        assert 'class="private"' not in room

    def test_there_is_still_exactly_one_digital_human_mount_path(self):
        shell = _shell()
        assert shell.count("function mountAvatarBody3D(container") == 1
        assert shell.count("function wireAvatarBody3D(){") == 1
        assert shell.count("async function mountStage(container, stagesMap, key, hueRgb, sceneBuilder, opts){") == 1

    def test_no_python_module_learned_about_any_of_this(self):
        """The whole correction is presentation. The only Python file this
        pass touched is the static-asset allowlist, and all it gained is
        two more filenames."""
        source = SERVE_UI.read_text("utf-8")
        for filename in ("SpaceGrotesk.woff2", "Inter.woff2"):
            assert filename in source, filename
        for forbidden in ("emblem", "cortex", "silhouette", "membrane", "anatomy"):
            assert forbidden not in source.lower(), forbidden
        for rel in ("src/services/identity/presence.py", "src/control_center/snapshot.py",
                    "src/manager/manager.py", "src/conversation/conversation.py"):
            text = (REPO_ROOT / rel).read_text("utf-8").lower()
            for forbidden in ("space grotesk", "cascadia", "cortex", "silhouette"):
                assert forbidden not in text, (rel, forbidden)
