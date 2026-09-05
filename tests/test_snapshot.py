"""The interface reads the system. It does not become a second one.

A screen is the most dangerous place to invent a value, because a person
cannot tell a fabricated number from a real one and will believe the screen
over the system. So the boundary this file protects is narrow and absolute:

    ControlCenter (reads)  ->  snapshot (data)  ->  any interface (renders)

The snapshot may only report what the observer already knows. It holds no
brain, no memory, no registry and no agent; it computes no mode, decides
nothing, and where the system cannot answer it says so with the reason the
system gave.

The second group proves the thing the product direction cares most about:
presenting an agent did not create another one. There is no workspace brain
and no conversation-room memory, because the surfaces are views of the same
objects the agents use everywhere else.
"""

import ast
from pathlib import Path

import pytest

from src.composition import (
    build_channel,
    build_conversation,
    build_manager,
    build_runtime,
)
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import SNAPSHOT_VERSION, snapshot
from src.services.email.sample_inbox import sample_inbox
from src.services.profile.loader import parse_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui"


def system():
    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
    runtime = build_runtime(settings)
    manager = build_manager(
        runtime,
        mailbox=sample_inbox(),
        profile=parse_profile("## Skills\n### Strong\npython\n"),
    )
    return (
        ControlCenter(runtime, manager),
        build_channel(runtime, manager),
        build_conversation(manager),
    )


def taken():
    centre, channel, room = system()
    return snapshot(centre, conversation=room, channel=channel), centre


# ---------------------------------------------------------------------------
# Everything shown traces to a real read
# ---------------------------------------------------------------------------


class TestTheSnapshotInventsNothing:
    def test_the_brain_mode_is_the_one_the_system_assessed(self):
        """Not recomputed here - asked of the component that decides it."""
        from src.manager.cognition import assess_manager

        data, centre = taken()
        alfred = data["agents"][0]

        assert alfred["brain"]["mode"] == assess_manager(centre.manager).mode.label
        assert alfred["brain"]["verified"] is assess_manager(centre.manager).verified

    def test_the_system_version_is_the_real_constant_not_a_subsystem_schema(self):
        """system_version comes from src/core/versioning.py's SYSTEM_VERSION
        - a different fact from this payload's own "version" key
        (SNAPSHOT_VERSION, this export's shape) and from any store's own
        SCHEMA_VERSION. All three happen to be distinguishable by type
        alone today (SYSTEM_VERSION is a string; the others are ints), and
        this test pins that rather than assuming it stays true by luck."""
        from src.core.versioning import SYSTEM_VERSION as REAL_SYSTEM_VERSION

        data, _ = taken()
        assert data["system"]["system_version"] == REAL_SYSTEM_VERSION
        assert isinstance(data["system"]["system_version"], str)
        assert isinstance(data["version"], int)

    def test_the_upgrade_manifest_is_embedded_and_matches_the_real_functions(self):
        """The Control Center visibility this milestone asks for: the same
        snapshot() entry point every interface already reads, carrying the
        real, deterministic upgrade_manifest() output - not a second
        summary of it that could drift."""
        from src.control_center.manifest import upgrade_manifest

        data, _ = taken()
        expected = upgrade_manifest(snapshot_version=data["version"]).to_dict()
        assert data["upgrade_manifest"] == expected

    def test_an_unproven_model_is_not_shown_as_full_brain(self):
        data, _ = taken()
        for agent in data["agents"]:
            if not agent["brain"]["verified"]:
                assert agent["brain"]["mode"] != "FULL BRAIN"

    def test_speech_reports_the_composed_adapter(self):
        data, _ = taken()
        assert data["speech"]["engine"] == "none"
        assert data["speech"]["usable"] is False
        assert "no speech engine is installed" in data["speech"]["describes"]

    def test_an_absent_conversation_says_so_rather_than_appearing_empty(self):
        """An empty room and no room are different facts. Showing the first
        for the second would claim a session that never started."""
        centre, _, _ = system()
        data = snapshot(centre)
        assert data["conversation"]["available"] is False
        assert data["conversation"]["reason"]

    def test_activity_and_avatar_are_genuinely_derived_not_fabricated(self):
        """Stage 17 gives both a real source: AgentPresence. A freshly
        taken snapshot - nothing has been said, no turn has happened -
        must read as IDLE, honestly, never a guessed richer state, and
        the avatar payload must trace to a real AvatarProfile rather
        than a placeholder."""
        data, _ = taken()
        for agent in data["agents"]:
            assert agent["activity"] == "idle"
            avatar = agent["avatar"]
            assert avatar["agent_key"] == agent["name"]
            assert avatar["state"] == "idle"
            profile = avatar["avatar"]
            assert profile["avatar_id"]
            assert profile["label"]

    def test_a_missing_timestamp_stays_missing(self):
        data, _ = taken()
        beat = data["system"]["heartbeat"]
        assert beat["last_wake"] is None or isinstance(beat["last_wake"], str)

    def test_the_gmail_boundary_is_reported_truthfully(self):
        data, _ = taken()
        assert data["system"]["can_send"] is False
        assert data["system"]["gmail_scope"].endswith("gmail.readonly")

    def test_it_carries_a_version_so_an_interface_knows_the_shape(self):
        data, _ = taken()
        assert data["version"] == SNAPSHOT_VERSION


# ---------------------------------------------------------------------------
# Presenting an agent did not create another one
# ---------------------------------------------------------------------------


class TestTheInterfaceIsNotASecondSystem:
    def test_the_snapshot_module_holds_no_brain_memory_or_registry(self):
        """Structural. It reads the observer and nothing else."""
        tree = ast.parse(
            (REPO_ROOT / "src" / "control_center" / "snapshot.py").read_text(
                encoding="utf-8"
            )
        )
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported += [a.name for a in node.names]

        for banned in ("src.manager.manager", "src.services.memory", "src.registry",
                       "src.agents", "src.providers"):
            assert not any(m.startswith(banned) for m in imported), (banned, imported)

    def test_no_second_brain_or_memory_class_exists_anywhere(self):
        """The product rule, made structural: a surface must not spawn a
        parallel intelligence for itself."""
        forbidden = (
            "ConversationRoomBrain", "ConversationRoomMemory", "WorkspaceBrain",
            "WorkspaceMemory", "UIBrain", "UIMemory", "RoomMemory", "RoomBrain",
        )
        for path in sorted((REPO_ROOT / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert node.name not in forbidden, (path.name, node.name)

    def test_the_brain_shown_is_the_agents_own(self):
        """One source of truth. The snapshot reports Alfred's brain, and
        Alfred's brain is the object Alfred reasons with."""
        data, centre = taken()
        arthur = centre.manager.registry.get("Arthur")

        assert centre.manager.brain is not arthur.brain
        names = [a["name"] for a in data["agents"]]
        assert names == ["Alfred", "Arthur"]

    def test_each_agent_reports_its_own_memory_store(self):
        data, centre = taken()
        owners = {a["name"]: a["memory"]["owner"] for a in data["agents"]}
        assert len(set(owners.values())) == len(owners), "stores must not be shared"

    def test_each_agent_reports_its_own_voice(self):
        data, _ = taken()
        keys = [a["voice"]["key"] for a in data["agents"] if a["voice"]["declared"]]
        assert len(keys) == len(set(keys)), "two agents must not share a voice key"

    def test_each_agent_reports_its_own_activation_phrases(self):
        """Stage 8A: the snapshot is where a browser-side voice controller
        reads activation phrases from - never a hardcoded list in the page
        itself. See ui/index.html's voice controller."""
        data, _ = taken()
        by_name = {a["name"]: a["activation"] for a in data["agents"]}
        assert by_name["Alfred"]["declared"] is True
        assert "alfred" in by_name["Alfred"]["phrases"]
        assert "hey alfred" in by_name["Alfred"]["phrases"]
        assert by_name["Arthur"]["phrases"] != by_name["Alfred"]["phrases"]

    def test_no_agent_is_named_in_the_snapshot_module(self):
        """A specialist registered later appears without a line changing."""
        source = (
            (REPO_ROOT / "src" / "control_center" / "snapshot.py")
            .read_text(encoding="utf-8")
            .lower()
        )
        for name in ("arthur", "alfred"):
            assert name not in source, name

    def test_a_granted_integration_appears_without_becoming_a_capability(self):
        """Two axes, still. A tool is a means; a capability is a kind of work."""
        from src.agents.tools.tool import Tool, ToolSpec
        from src.agents.tools.toolset import ToolSet
        from src.services.memory.factory import EMAIL_AGENT

        class Probe(Tool):
            @property
            def spec(self):
                return ToolSpec(name="probe", description="a granted integration")

            def run(self, **arguments):
                return "ok"

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(
            runtime,
            mailbox=sample_inbox(),
            profile=None,
            specialist_tools={EMAIL_AGENT: ToolSet([Probe()])},
        )
        data = snapshot(ControlCenter(runtime, manager))

        arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
        assert [t["name"] for t in arthur["tools"]] == ["probe"]
        assert "probe" not in [c["name"] for c in arthur["capabilities"]]


# ---------------------------------------------------------------------------
# The shell renders the snapshot and nothing else
# ---------------------------------------------------------------------------


class TestTheInterfaceShell:
    def test_the_shell_exists_and_has_one_place_for_data(self):
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert shell.count("__SNAPSHOT__") == 1

    def test_rendering_embeds_the_snapshot(self):
        from scripts.export_ui import render

        data, _ = taken()
        page = render(data)

        assert "__SNAPSHOT__" not in page
        assert '"version": 1' in page or '"version":1' in page

    def test_a_transcript_cannot_rewrite_the_page(self):
        """A conversation carries whatever a person typed, so it must stay data.

        The text itself is *expected* to survive into the page - it is the
        transcript, and rendering it is the point. What must not survive is the
        sequence that would end the script tag early and turn the rest into
        markup. So the assertion is about `</script>`, not about the words.
        """
        from scripts.export_ui import render

        hostile = "</script><script>window.__owned=1</script>"
        data, _ = taken()
        data["conversation"]["utterances"].append(
            {"speaker": "you", "text": hostile, "when": None, "addressed": None}
        )

        page = render(data)
        payload = page.split('type="application/json">', 1)[1].split("</script>", 1)[0]

        # The tag was not closed early: everything up to the first real
        # </script> is one JSON document, and the hostile text is inside it.
        assert "</script" not in payload
        assert "<\\/script>" in payload
        assert "window.__owned" in payload  # carried as data, not as markup

    def test_the_shell_loads_nothing_from_the_network(self):
        """Self-contained, so it opens from a file on a laptop or a phone."""
        shell = (UI / "index.html").read_text(encoding="utf-8").lower()
        for remote in ("http://", "https://", "//cdn", "src=\"//"):
            assert remote not in shell, remote

    def test_the_shell_carries_every_surface(self):
        shell = (UI / "index.html").read_text(encoding="utf-8")
        for surface in (
            "control", "room", "workspace", "brain", "memory", "status", "history"
        ):
            assert f'"{surface}"' in shell or f"{surface}:" in shell, surface

    def test_no_server_dependency_was_added(self):
        """The guard that says the UI phase has not started still holds for
        src/: this phase added an exporter and a static page, not a server."""
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for absent in ("flask", "fastapi", "uvicorn", "django", "streamlit", "gradio"):
            assert absent not in requirements, absent


# ---------------------------------------------------------------------------
# The visual pass added color, instruments and a roadmap - none of it may
# blur the boundary the rest of this file protects.
# ---------------------------------------------------------------------------


class TestTheVisualPassStaysHonest:
    def test_the_roadmap_is_static_not_derived_from_the_snapshot(self):
        """The roadmap chips name what is documented as coming next, not
        what the registry reports. They must never be computed from S."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        line = next(line for line in shell.splitlines() if "const ROADMAP" in line)
        assert "S." not in line
        assert "agents" not in line

    def test_the_roadmap_chips_are_not_interactive(self):
        """A ghost is not a button. It must not carry the wiring a real
        agent's satellite card does."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        line = next(line for line in shell.splitlines() if "ROADMAP.map" in line)
        assert "data-goto" not in line
        assert "addEventListener" not in line

    def test_the_brain_instrument_has_a_filled_and_an_unfilled_rendering(self):
        """Proves the glyph is conditional on the real indicator, not a
        hardcoded picture of a full brain."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        glyph = shell.split("const brainGlyph", 1)[1].split("const MEMORY_TRACE_CAP", 1)[0]
        assert "var(--ok)" in glyph
        assert "stroke-dasharray" in glyph

    def test_the_memory_instrument_reads_the_real_sample_count(self):
        shell = (UI / "index.html").read_text(encoding="utf-8")
        glyph = shell.split("const memoryGlyph", 1)[1].split("const ROADMAP", 1)[0]
        assert "mem.sampled" in glyph

    def test_no_web_font_was_fetched_for_the_new_typography(self):
        """The palette/type pass must keep the shell self-contained."""
        shell = (UI / "index.html").read_text(encoding="utf-8").lower()
        assert "fonts.googleapis" not in shell
        assert "fonts.gstatic" not in shell


# ---------------------------------------------------------------------------
# Per-agent visual identity (color + form) stays a pure, deterministic
# presentation function of a name the snapshot already carries - never a
# second source of truth, never randomness, never a name comparison.
# ---------------------------------------------------------------------------


class TestAgentVisualIdentityStaysHonest:
    def test_identity_is_derived_from_a_hash_never_from_random(self):
        """The same agent must render the same color and form on every
        frame and every reload - a random choice would not hold still."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        identity = shell.split("const hashOf", 1)[1].split("const brainGlyph", 1)[0]
        assert "Math.random" not in identity

    def test_the_script_never_branches_on_a_literal_agent_name(self):
        """Manager vs. specialist must be decided by `is_manager` /
        `b.kind`, both real fields the snapshot/canvas already carry -
        never by comparing a name against "Alfred" or "Arthur"."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        script = shell.split("<script>", 1)[1].split("</script>", 1)[0]
        for literal in ('"Alfred"', "'Alfred'", '"Arthur"', "'Arthur'"):
            assert literal not in script, literal

    def test_the_manager_core_and_specialist_polygon_are_chosen_by_identity(self):
        """identityOf() decides form from `agent.is_manager` - a real,
        registry-derived fact, not a name - and the canvas draws a faceted
        core for form "core" and a polygon otherwise."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        fn = shell.split("function identityOf(agent){", 1)[1].split("\n  }", 1)[0]
        assert "agent.is_manager" in fn
        assert '"core"' in fn and '"polygon"' in fn
        assert 'id.form==="core"' in shell
        assert "facetedCore(" in shell
        assert "polygon(id.sides" in shell

    def test_room_presence_reuses_the_one_identity_function(self):
        """The room must not invent a second color mechanism - it colors a
        specialist's presence with the same identityOf() the graph uses.
        Checked against the actual call, not prose that merely mentions
        it - a comment saying so is not the same as code doing it."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        beings = shell.split("const beings = c.participants.map", 1)[1].split(
            "}).join", 1
        )[0]
        code_lines = [
            line for line in beings.splitlines() if not line.strip().startswith("//")
        ]
        assert "identityOf({name:p.name" in "\n".join(code_lines)

    def test_identity_of_reads_only_name_and_manager_status(self):
        """identityOf() must be unable to alter Brain, Memory, Voice,
        provider, capabilities or connections - it can't touch what it
        never reads. Only `.name` and `.is_manager` may appear in it."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        fn = shell.split("function identityOf(agent){", 1)[1].split("\n  }", 1)[0]
        for forbidden in (
            ".brain", ".memory", ".voice", ".tools", ".capabilities",
            ".provider", ".interface", "fetch(",
        ):
            assert forbidden not in fn, forbidden


# ---------------------------------------------------------------------------
# The workspace foundation: identityGlyph() is a presentation of
# identityOf(), never a second identity system, and V.workspace renders
# it generically for every agent the snapshot reports.
# ---------------------------------------------------------------------------


class TestAgentWorkspaceStaysHonest:
    def test_identity_glyph_only_ever_calls_identity_of(self):
        """identityGlyph() must draw exactly what identityOf() returned -
        no separate access to Brain/Memory/Voice/tools, and no second
        hash or color table of its own."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        fn = shell.split("function identityGlyph(agent", 1)[1].split("\n  }", 1)[0]
        assert "identityOf(agent)" in fn
        for forbidden in (
            ".brain", ".memory", ".voice", ".tools", ".capabilities",
            "SPECIALIST_HUES", "SPECIALIST_SIDES", "hashOf(",
        ):
            assert forbidden not in fn, forbidden

    def test_the_workspace_renders_one_glyph_per_agent_generically(self):
        """No `if a.name===...` - every band in the S.agents.map loop gets
        the same identityGlyph(a) call, so a third agent needs no new
        line here."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        band = shell.split('V.workspace = ()', 1)[1].split("S.agents.map(a=>`", 1)[1].split(
            "</div>`).join", 1
        )[0]
        assert "identityGlyph(a)" in band
        assert '"Alfred"' not in band and '"Arthur"' not in band

    def test_identity_glyph_motion_is_css_not_a_render_loop(self):
        """Ambient spin is a CSS animation, so it costs no extra
        requestAnimationFrame work and automatically respects this page's
        one global prefers-reduced-motion rule - no second motion system
        to keep honest."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert "@keyframes idspin" in shell
        fn = shell.split("function identityGlyph(agent", 1)[1].split("\n  }", 1)[0]
        assert "requestAnimationFrame" not in fn


# ---------------------------------------------------------------------------
# The camera can be pointed at a real agent and the Room can show its own
# decorative core - neither may fabricate a body, a color, or an activity.
# ---------------------------------------------------------------------------


class TestTheCameraAndRoomCoreStayHonest:
    def test_the_camera_only_ever_finds_an_existing_body(self):
        """Focusing the camera must look up a real body already in
        `bodies` (built from S.agents) - it must never construct one."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        fn = shell.split("function frame(){", 1)[1].split("\n  }", 1)[0]
        assert "bodies.find(b=>b.kind!==\"dust\" && b.name===focusBody)" in fn
        assert "bodies.push" not in fn

    def test_the_room_core_is_not_colored_by_any_agents_identity(self):
        """The room's own presence must read as the space, not as a
        participant - it must never use identityOf()/hue/col, only a
        fixed neutral tone."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        core = shell.split('if(current==="room"){', 1)[1].split("\n    }", 1)[0]
        for forbidden in ("identityOf(", ".hue", "col}"):
            assert forbidden not in core, forbidden
        assert "200,224,236" in core

    def test_the_room_core_is_gated_on_the_real_current_surface(self):
        """It must only ever be drawn while the Room is the active
        surface - never on a timer, never unconditionally."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert 'if(current==="room"){' in shell

    def test_focus_click_targets_are_built_from_the_agent_loop_not_a_name(self):
        """data-focus must come from `${esc(a.name)}` inside an
        S.agents.map loop in every surface that offers it - never a
        literal "Alfred"/"Arthur" attribute value."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert shell.count('data-focus="${esc(a.name)}"') >= 3

    def test_focus_body_is_declared_before_the_synchronous_initial_render(self):
        """Regression guard: `go(start)` runs synchronously at load and
        assigns `focusBody` - a `let` declared later in the script (as
        `focus` once was) would throw "Cannot access before
        initialization" on every single page load. Both must be declared
        together, at the top, before anything can run."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert "let S = null, focus = null, focusBody = null;" in shell


# ---------------------------------------------------------------------------
# Agent 3D environment: a genuine WebGL stage (Three.js, vendored locally),
# not Canvas 2D pretending to be one. It receives a hue, nothing else - it
# cannot read Brain, Memory, Voice, capabilities, connections or a provider
# because nothing here ever hands it an agent object at all.
# ---------------------------------------------------------------------------


class TestAgent3DStaysHonest:
    def _renderer_block(self):
        shell = (UI / "index.html").read_text(encoding="utf-8")
        return shell.split("function loadThree(){", 1)[1].split("function go(key", 1)[0]

    def test_the_renderer_receives_an_identity_descriptor_never_an_agent_object(self):
        """mountAgent3D's own signature is the guarantee: a container and
        the same {key,hue,form,sides} descriptor identityOf() already
        returns elsewhere on this page - not an agent - so it structurally
        cannot inspect what it was never given. It is a thin wrapper around
        the shared mountStage() lifecycle (Phase 3), not async itself."""
        block = self._renderer_block()
        assert "function mountAgent3D(container, identity){" in block

    def test_the_renderer_never_reads_brain_memory_voice_or_grants(self):
        block = self._renderer_block()
        for forbidden in (
            ".brain", ".memory", ".voice", ".tools", ".capabilities",
            ".interface", "a.connections",
        ):
            assert forbidden not in block, forbidden

    def test_the_renderer_names_no_llm_provider(self):
        """Changing Claude -> OpenAI -> a future provider must not be
        able to change what this draws, because it never looks."""
        block = self._renderer_block().lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey", "model"):
            assert forbidden not in block, forbidden

    def test_the_renderer_never_branches_on_a_literal_agent_name(self):
        """Belt and suspenders: the whole-script guard already covers
        this file, but the 3D renderer specifically gets its own check,
        since this is the surface the milestone is about."""
        block = self._renderer_block()
        for literal in ('"Alfred"', "'Alfred'", '"Arthur"', "'Arthur'"):
            assert literal not in block, literal

    def test_the_renderer_uses_no_randomness(self):
        """Motion is elapsed-time-based (THREE.Clock), so the same moment
        always looks the same - a random choice would not hold still
        across a re-render, and would blur the real/decorative line this
        project holds everywhere else."""
        block = self._renderer_block()
        assert "Math.random" not in block

    def test_three_js_is_vendored_locally_and_is_not_trivial(self):
        vendor = UI / "vendor" / "three.module.min.js"
        assert vendor.exists()
        assert vendor.stat().st_size > 100_000  # a real library, not a stub
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert 'import("./vendor/three.module.min.js")' in shell

    def test_three_core_is_also_vendored(self):
        """three.module.min.js imports this sibling module by relative
        path at the browser's own request time - discovered by an actual
        404 in a real browser, not assumed. Both files must ship
        together or the import silently fails after the first hop."""
        core = UI / "vendor" / "three.core.min.js"
        assert core.exists()
        assert core.stat().st_size > 100_000

    def test_the_vendored_asset_route_serves_the_same_allowlisted_files(self):
        """scripts/serve_ui.py's asset route must be a fixed allowlist,
        not an arbitrary-path file server."""
        source = (REPO_ROOT / "scripts" / "serve_ui.py").read_text(encoding="utf-8")
        assert 'VENDOR_ASSETS = {' in source
        assert '"/vendor/three.module.min.js"' in source
        assert '"/vendor/three.core.min.js"' in source

    def test_every_agent_receives_its_own_stage_chosen_by_identity_form(self):
        """Phase 2: every agent gets a stage now, manager and specialist
        alike - the workspace template no longer gates the `.agent3d`
        div on `is_manager` at all, and which geometry a stage draws is
        chosen only by identityOf()'s `form`, never by is_manager or a
        name, inside mountAgent3D itself. Phase 5 renamed the lookup from
        emblemFor() to environmentFor() (see TestVisualEnvironmentRegistry
        StaysHonest) without changing this guarantee."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        assert '<div class="agent3d" data-agent3d="${esc(a.name)}">' in shell
        assert "a.is_manager ? `<div class=\"agent3d\"" not in shell
        block = self._renderer_block()
        assert "environmentFor(identity.form)" in block

    def test_two_distinct_emblem_builders_exist_keyed_by_form_not_name(self):
        """The 'future replacement requirement': swapping an agent's
        visual later means pointing this one lookup table at a different
        builder, never touching mount/resize/dispose/reduced-motion.
        Phase 5 wrapped each builder in a small {id,version,build}
        descriptor (AGENT_VISUAL_ENVIRONMENTS) rather than referencing
        the builder function directly - see
        TestVisualEnvironmentRegistryStaysHonest for that layer."""
        block = self._renderer_block()
        assert "function buildManagerEmblem(THREE, accent){" in block
        assert "function buildSpecialistEmblem(THREE, accent){" in block
        assert "core:    { id: \"alfred-tactical-core\", version: 1, build: buildManagerEmblem }," in block
        assert "polygon: { id: \"specialist-wing-core\", version: 1, build: buildSpecialistEmblem }," in block

    def test_the_specialist_emblem_is_geometrically_distinct_from_the_managers(self):
        """Arthur must read as a different object, not a recolored copy
        of Alfred's core: different Three.js geometry constructors, not
        just a different material tint."""
        block = self._renderer_block()
        manager_body = block.split("function buildManagerEmblem(THREE, accent){", 1)[1].split(
            "\n  }", 1
        )[0]
        specialist_body = block.split("function buildSpecialistEmblem(THREE, accent){", 1)[1].split(
            "\n  }", 1
        )[0]
        assert "OctahedronGeometry" in manager_body
        assert "ExtrudeGeometry" in specialist_body
        assert "OctahedronGeometry" not in specialist_body
        assert "ExtrudeGeometry" not in manager_body

    def test_emblem_builders_receive_only_three_and_an_accent_color(self):
        """Neither builder can read Brain/Memory/Voice/Capabilities/
        Connections or a provider name, because neither is ever given
        the agent - the same boundary mountAgent3D itself holds, pushed
        one level down."""
        block = self._renderer_block()
        assert "function buildManagerEmblem(THREE, accent){" in block
        assert "function buildSpecialistEmblem(THREE, accent){" in block
        for forbidden in (
            ".brain", ".memory", ".voice", ".tools", ".capabilities",
            ".interface", "a.connections",
        ):
            assert forbidden not in block, forbidden

    def test_stages_are_tracked_per_agent_so_more_than_one_can_run_at_once(self):
        """Phase 1 held a single `agent3D` variable because only the
        manager had a stage. Now that every agent does, the running
        stages must be a map keyed by identity, not one shared slot -
        otherwise mounting a second agent's stage would silently stomp
        the first one's renderer reference. Phase 3 generalized the
        set()/dispose() calls themselves into the shared mountStage()/
        disposeStage() lifecycle (see test_the_stage_lifecycle_is_shared_
        not_duplicated_per_surface), so this test checks the Map itself
        and the shared functions that mutate it."""
        block = self._renderer_block()
        assert "let agent3DStages = new Map();" in block
        assert "function disposeStage(stagesMap, key){" in block
        assert "stagesMap.set(key, stageObj);" in block

    def test_a_stage_is_disposed_only_when_its_own_container_disappears(self):
        """wireStages() (shared by wireAgent3D/wireBrain3D/wireMemory3D,
        Phase 3) must dispose exactly the stages whose element is no
        longer present, not every stage whenever any one view rebuilds -
        two agents' stages, and an agent's identity/Brain/Memory stages,
        must all be able to coexist and be reused independently."""
        block = self._renderer_block()
        wire_body = block.split("function wireStages(attrKey, stagesMap, mountFn, tag){", 1)[1]
        assert "const present = new Set(els.map(el=>el.dataset[attrKey]));" in wire_body
        assert "if(!present.has(key)) disposeStage(stagesMap, key);" in wire_body

    def test_reusing_a_running_stage_also_clears_the_fallback_text(self):
        """Regression guard, found by actually watching a live poll fire
        in a browser: go() rebuilds every `[data-agent3d]` element from
        scratch on each render (a live poll, a mode switch back),
        including a brand new "INITIALIZING" fallback div - wireAgent3D()'s
        reuse branch (which keeps the already-running renderer instead of
        restarting it) must also remove that fresh fallback, or it sits
        visible on top of a 3D stage that has actually been working the
        whole time. Still true now that this branch runs once per agent
        inside a loop, not once for a single stage."""
        shell = (UI / "index.html").read_text(encoding="utf-8")
        reuse_branch = shell.split("if(running && running.renderer){", 1)[1].split(
            "running.ro.observe(el);", 1
        )[0]
        assert ".agent3d-fallback" in reuse_branch
        assert ".remove()" in reuse_branch

    def test_webgl_failure_falls_back_honestly_not_to_canvas_2d(self):
        """A failed context creation or a blocked module load must say
        so - never silently present Canvas 2D as if it were the intended
        3D renderer."""
        block = self._renderer_block()
        assert block.count('"3D ENVIRONMENT UNAVAILABLE"') >= 2

    def test_reduced_motion_is_read_before_the_animation_loop_exists(self):
        block = self._renderer_block()
        still_at = block.index('matchMedia("(prefers-reduced-motion: reduce)").matches')
        tick_at = block.index("function tick(){")
        assert still_at < tick_at


class TestBrainAndMemory3DStayHonest:
    """Phase 3: Brain and Memory get their own genuine 3D presentation,
    per agent, as separate navigable surfaces - never a chat side panel,
    never a second Brain/Memory owned by the Conversation Room, never a
    fabricated state or capacity. Reuses the shared mountStage()/
    wireStages() lifecycle TestAgent3DStaysHonest already protects rather
    than duplicating it."""

    def _renderer_block(self):
        shell = (UI / "index.html").read_text(encoding="utf-8")
        return shell.split("function loadThree(){", 1)[1].split("function go(key", 1)[0]

    def _shell(self):
        return (UI / "index.html").read_text(encoding="utf-8")

    def test_the_stage_lifecycle_is_shared_not_duplicated_per_surface(self):
        """ONE mount/resize/dispose/reduced-motion implementation behind
        identity, Brain and Memory - not three copies of the same ~40
        lines. Each surface supplies only its own scene builder."""
        shell = self._shell()
        assert shell.count("async function mountStage(container, stagesMap, key, hueRgb, sceneBuilder){") == 1
        assert shell.count("function tick(){") == 1
        assert shell.count("function disposeStage(stagesMap, key){") == 1
        assert shell.count('function wireStages(attrKey, stagesMap, mountFn, tag){') == 1
        block = self._renderer_block()
        assert "return mountStage(container, agent3DStages, identity.key, identity.hue" in block
        assert "return mountStage(container, brain3DStages, identity.key, identity.hue" in block
        assert "return mountStage(container, memory3DStages, identity.key, identity.hue" in block

    def test_brain_and_memory_builders_receive_only_narrow_presentational_values(self):
        """Never the agent, never `.brain`/`.memory` themselves - a
        container plus an identity descriptor plus the one small value
        (a GREEN/AMBER/RED signal; a lit count and a durable flag) the
        caller already derived from the real state elsewhere."""
        block = self._renderer_block()
        assert "function buildBrainGeometry(THREE, accent, signal){" in block
        assert "function buildMemoryGeometry(THREE, accent, lit, durable){" in block
        assert "function mountBrain3D(container, identity, signal){" in block
        assert "function mountMemory3D(container, identity, lit, durable){" in block

    def test_brain_and_memory_geometry_are_visually_distinct(self):
        """Brain = reasoning, Memory = retained information - the
        directive is explicit these must not be merged, so their
        geometry must actually differ, not just their material tint."""
        block = self._renderer_block()
        brain_body = block.split("function buildBrainGeometry(THREE, accent, signal){", 1)[1].split(
            "\n  }", 1
        )[0]
        memory_body = block.split("function buildMemoryGeometry(THREE, accent, lit, durable){", 1)[1].split(
            "\n  }", 1
        )[0]
        assert "IcosahedronGeometry" in brain_body
        assert "CylinderGeometry" in brain_body
        assert "DodecahedronGeometry" in memory_body
        assert "IcosahedronGeometry" not in memory_body
        assert "DodecahedronGeometry" not in brain_body

    def test_brain_and_memory_stages_live_in_their_own_maps_per_agent(self):
        """Alfred Brain != Arthur Brain, Alfred Memory != Arthur Memory:
        three independent Maps (identity/Brain/Memory), each keyed by
        agent name, so one agent's three stages and another agent's three
        stages never collide or overwrite each other."""
        block = self._renderer_block()
        assert "let agent3DStages = new Map();" in block
        assert "let brain3DStages  = new Map();" in block
        assert "let memory3DStages = new Map();" in block
        assert "function wireBrain3D(){" in block
        assert "function wireMemory3D(){" in block
        assert 'wireStages("brain3d", brain3DStages,' in block
        assert 'wireStages("memory3d", memory3DStages,' in block

    def test_go_wires_all_three_per_agent_surfaces_every_render(self):
        shell = self._shell()
        go_body = shell.split("function go(key, agentName){", 1)[1].split("\n  }", 1)[0]
        assert "wireAgent3D();" in go_body
        assert "wireBrain3D();" in go_body
        assert "wireMemory3D();" in go_body

    def test_brain_and_memory_are_navigable_surfaces_not_chat_side_panels(self):
        """V.brain and V.memory are their own top-level modes (Cognition,
        Memory), never rendered inside V.room's chat surface."""
        shell = self._shell()
        assert "V.brain = " in shell
        assert "V.memory = " in shell
        room_body = shell.split("V.room = () => {", 1)[1].split("\n  };", 1)[0]
        for forbidden in ("data-brain3d", "data-memory3d", "mountBrain3D", "mountMemory3D", "brain3DStages", "memory3DStages"):
            assert forbidden not in room_body, forbidden

    def test_the_conversation_room_creates_no_brain_or_memory_of_its_own(self):
        """The Room brings the agents that are already in it - it must
        never read, let alone construct, a Brain or Memory of its own; it
        is not an agent (§15 of the directive). Its template never
        touches `.brain`/`.memory` at all, not even to display them,
        because Brain and Memory are separate surfaces the Room does not
        own."""
        shell = self._shell()
        room_body = shell.split("V.room = () => {", 1)[1].split("\n  };", 1)[0]
        assert ".brain" not in room_body
        assert ".memory" not in room_body

    def test_full_brain_signal_comes_from_the_real_indicator_field(self):
        """The 3D Brain stage's signal is `a.brain.indicator` itself -
        the exact GREEN/AMBER/RED string the status dot already renders -
        never a value invented for the 3D view."""
        shell = self._shell()
        assert 'data-brain3d="${esc(a.name)}" data-signal="${esc(a.brain.indicator)}"' in shell

    def test_memory_gauge_reuses_the_one_honest_trace_cap_never_fabricates_capacity(self):
        """memoryGlyph() (the existing SVG ring) and the Memory 3D stage
        must derive `lit` from the exact same memoryTrace() helper - one
        cap (MEMORY_TRACE_CAP), one formula, never two competing ideas of
        how full memory looks. buildMemoryGeometry() never renders more
        than the real `lit` shards it was handed."""
        shell = self._shell()
        assert "const memoryTrace = mem => {" in shell
        assert "memoryTrace(mem)" in shell.split("const memoryGlyph = (mem,size=34) => {", 1)[1][:400]
        assert "memoryTrace(a.memory)" in shell
        block = self._renderer_block()
        memory_body = block.split("function buildMemoryGeometry(THREE, accent, lit, durable){", 1)[1].split(
            "\n  }", 1
        )[0]
        assert "for(let i=0;i<lit;i++){" in memory_body

    def test_memory_data_attributes_carry_only_the_real_sampled_and_durable_fields(self):
        shell = self._shell()
        assert 'data-memory3d="${esc(a.name)}" data-lit="${lit}" data-durable="${a.memory.durable?"true":"false"}"' in shell

    def test_brain_and_memory_stages_use_the_agents_own_hue_not_a_fixed_color(self):
        """Ties each Brain/Memory stage to the same real identity color
        every other presentation of that agent already uses - never a
        hardcoded palette entry."""
        block = self._renderer_block()
        assert "mountStage(container, brain3DStages, identity.key, identity.hue" in block
        assert "mountStage(container, memory3DStages, identity.key, identity.hue" in block

    def test_existing_agent_3d_tests_still_cover_the_shared_lifecycle(self):
        """Sanity check that Phase 3's refactor didn't quietly detach the
        identity-emblem surface from the same shared lifecycle Brain and
        Memory now also use - if this ever fails, TestAgent3DStaysHonest
        is no longer testing the code path Brain/Memory also run
        through."""
        block = self._renderer_block()
        assert "function mountAgent3D(container, identity){" in block
        assert "return mountStage(container, agent3DStages" in block


class TestRoomCore3DStaysHonest:
    """Phase 4: the Conversation Room gets its own genuine 3D
    environmental presence - an original mechanical energy core - without
    becoming an agent. It must own no Identity/Brain/Memory/Voice, read
    none of any agent's, and reuse the same shared mountStage()/
    wireStages() lifecycle rather than a fourth hand-rolled copy."""

    def _renderer_block(self):
        shell = (UI / "index.html").read_text(encoding="utf-8")
        return shell.split("function loadThree(){", 1)[1].split("function go(key", 1)[0]

    def _shell(self):
        return (UI / "index.html").read_text(encoding="utf-8")

    def test_the_room_core_reuses_the_shared_stage_lifecycle(self):
        shell = self._shell()
        assert shell.count("async function mountStage(container, stagesMap, key, hueRgb, sceneBuilder){") == 1
        assert shell.count("function tick(){") == 1
        block = self._renderer_block()
        assert "return mountStage(container, room3DStages, key, ROOM_HUE" in block

    def test_the_room_core_builder_receives_only_three_and_an_accent_color(self):
        """No identity descriptor, no agent, no `.brain`/`.memory`/
        `.voice`/`.tools`/`.capabilities`/`.connections` - the Room is not
        an agent and has none of these to read."""
        block = self._renderer_block()
        assert "function buildRoomCore(THREE, accent){" in block
        room_body = block.split("function buildRoomCore(THREE, accent){", 1)[1].split("\n  }", 1)[0]
        for forbidden in (
            ".brain", ".memory", ".voice", ".tools", ".capabilities",
            ".interface", "a.connections", "identityOf(", "S.agents",
        ):
            assert forbidden not in room_body, forbidden

    def test_the_room_core_uses_the_pre_existing_neutral_room_hue_not_an_agents(self):
        """The same fixed "200,224,236" tone the old Canvas 2D room-presence
        rings already used (TestTheCameraAndRoomCoreStayHonest) - never one
        participant's own identityOf() hue, so the core reads as the space
        itself, not as whichever agent happens to be present."""
        shell = self._shell()
        assert 'const ROOM_HUE = "200,224,236";' in shell
        block = self._renderer_block()
        assert "identityOf(" not in block.split("function buildRoomCore(THREE, accent){", 1)[1].split("\n  }", 1)[0]

    def test_the_room_core_geometry_is_original_and_layered(self):
        """Real depth and layered engineering: three torus rings, each
        with its own z-depth and rotation speed (data-driven from one
        ringSpecs array, not three copy-pasted constructors), a distinct
        inner rotor, and a deterministic (never Math.random()) radial
        array of outer segments - not a single flat disc."""
        block = self._renderer_block()
        room_body = block.split("function buildRoomCore(THREE, accent){", 1)[1].split("\n  }", 1)[0]
        assert "TorusGeometry" in room_body
        assert room_body.count("speed:") == 3
        assert room_body.count("z:") == 3
        assert "OctahedronGeometry" in room_body
        assert "BoxGeometry" in room_body
        assert "Math.random" not in room_body

    def test_the_room_is_not_an_agent_and_owns_no_brain_or_memory(self):
        """mountRoom3D()/wireRoom3D() take no identity descriptor at all -
        unlike mountAgent3D/mountBrain3D/mountMemory3D, which all receive
        `identity`. The Room's stage is keyed by a fixed DOM attribute
        value ("core"), never an agent name."""
        block = self._renderer_block()
        assert "function mountRoom3D(container, key){" in block
        assert "function wireRoom3D(){" in block
        wire_room = block.split("function wireRoom3D(){", 1)[1].split("\n  }", 1)[0]
        assert 'wireStages("room3d", room3DStages,' in wire_room
        assert "(el, name) => mountRoom3D(el, name)," in wire_room
        for forbidden in ("identity.key", "identity.hue", "identityOf(agent)"):
            assert forbidden not in wire_room

    def test_the_room_stage_lives_in_its_own_map_never_shared_with_agents(self):
        block = self._renderer_block()
        assert "let room3DStages   = new Map();" in block
        assert "mountStage(container, room3DStages, key, ROOM_HUE" in block

    def test_the_room_core_div_is_only_rendered_inside_the_real_room_view(self):
        """The stage must sit inside V.room's real, conversation-available
        branch, never inside the "THE ROOM IS QUIET" absent-state branch -
        no WebGL stage for a room the system itself reports as unavailable."""
        shell = self._shell()
        room_fn = shell.split("V.room = () => {", 1)[1].split("\n  };", 1)[0]
        absent_branch = room_fn.split("if(!c.available) return `", 1)[1].split("`;", 1)[0]
        assert "data-room3d" not in absent_branch
        assert '<div class="agent3d" data-room3d="core">' in room_fn

    def test_go_wires_the_room_stage_every_render(self):
        shell = self._shell()
        go_body = shell.split("function go(key, agentName){", 1)[1].split("\n  }", 1)[0]
        assert "wireRoom3D();" in go_body

    def test_provider_and_name_hardcoding_guards_still_cover_the_room_core(self):
        """Belt and suspenders: the room-core additions live inside the
        same span TestAgent3DStaysHonest's provider/name/randomness guards
        already scan in full, so this just documents that expectation
        explicitly for the surface this milestone is about."""
        block = self._renderer_block().lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey", "model"):
            assert forbidden not in block, forbidden


class TestVisualEnvironmentRegistryStaysHonest:
    """Phase 5: the visual environment is a replaceable presentation
    layer, expressed as {id, version, build} descriptors kept in one
    small registry - a seam a future visual upgrade goes through without
    touching Identity/Brain/Memory/Voice/Capabilities/Connections/
    Registry/LLMRouter/provider, and without a second copy of the shared
    mount/resize/dispose/reduced-motion lifecycle."""

    def _shell(self):
        return (UI / "index.html").read_text(encoding="utf-8")

    def _renderer_block(self):
        shell = self._shell()
        return shell.split("function loadThree(){", 1)[1].split("function go(key", 1)[0]

    def _registry_block(self):
        block = self._renderer_block()
        return block.split("const AGENT_VISUAL_ENVIRONMENTS = {", 1)[1].split(
            "// ---- SHARED 3D STAGE LIFECYCLE", 1
        )[0]

    def test_every_environment_is_exactly_an_id_version_and_build_function(self):
        """Presentation-only, by shape: an environment descriptor has
        exactly three fields. No Brain, no Memory, no provider, no
        credentials, no capability, no connection key can be smuggled in
        without this test's literal text failing to match."""
        registry = self._registry_block()
        for line in (
            'core:    { id: "alfred-tactical-core", version: 1, build: buildManagerEmblem },',
            'polygon: { id: "specialist-wing-core", version: 1, build: buildSpecialistEmblem },',
        ):
            assert line in registry
        shell = self._shell()
        # Stage 17.18 replaced both of these outright - the node lattice
        # became a folded human cortex, the archive core became a
        # stratified archive - so both carry a new id AND a bumped
        # version. That is exactly what this registry is for: the
        # manifest reads these strings straight out of this file (see
        # src/control_center/manifest.py), so a replaced environment is
        # recorded rather than swapped silently.
        assert 'const BRAIN_VISUAL_ENVIRONMENT  = { id: "brain-human-cortex",     version: 3, build: buildBrainGeometry };' in shell
        assert 'const MEMORY_VISUAL_ENVIRONMENT = { id: "memory-archive-strata", version: 3, build: buildMemoryGeometry };' in shell
        assert 'const ROOM_VISUAL_ENVIRONMENT   = { id: "room-energy-core",    version: 1, build: buildRoomCore };' in shell
        for forbidden in ("brain:", "memory:", "provider:", "apiKey:", "credentials:", "connections:"):
            assert forbidden not in registry, forbidden

    def test_environments_are_selected_through_one_lookup_not_scattered_conditionals(self):
        """environmentFor(form) is the only place agent -> environment
        selection happens; mountAgent3D calls it once and does not also
        branch on is_manager or a name."""
        block = self._renderer_block()
        assert 'const environmentFor = form => AGENT_VISUAL_ENVIRONMENTS[form] || AGENT_VISUAL_ENVIRONMENTS.polygon;' in block
        assert "environmentFor(identity.form)" in block
        mount_agent = block.split("function mountAgent3D(container, identity){", 1)[1].split("\n  }", 1)[0]
        assert "is_manager" not in mount_agent
        for literal in ('"Alfred"', "'Alfred'", '"Arthur"', "'Arthur'"):
            assert literal not in mount_agent

    def test_a_future_unrecognized_form_still_receives_an_environment(self):
        """future-agent compatibility: environmentFor() never returns
        undefined for a form it doesn't recognize - it falls back to the
        specialist environment rather than the renderer throwing or
        silently mounting nothing for an agent identityOf() hashed to a
        form this registry hasn't been extended for yet."""
        block = self._renderer_block()
        assert "AGENT_VISUAL_ENVIRONMENTS[form] || AGENT_VISUAL_ENVIRONMENTS.polygon" in block

    def test_environment_descriptors_are_independent_of_agent_identity(self):
        """identityOf() - the one thing that IS per-agent - returns
        {key,hue,form,sides} and nothing about version or environment
        state. Environment version lives only in the registry, so an
        agent's identity is untouched by a future environment upgrade."""
        # identityOf is defined earlier in the file, outside the renderer
        # block boundary used elsewhere in this file - read it directly.
        shell = self._shell()
        identity_body = shell.split("function identityOf(agent){", 1)[1].split("\n  }", 1)[0]
        for forbidden in ("version", "env", "build"):
            assert forbidden not in identity_body, forbidden

    def test_mounted_stages_are_tagged_with_their_environment_id_and_version(self):
        """A real, DOM-visible signal that the registry is actually wired
        to what gets drawn, not just declared and unused - every
        mount*3D() tags its own container before mountStage runs. Tagging
        is done by wireStages()'s own `tag` callback (called on every
        wire pass, both fresh mounts and reuse) rather than inside a
        mount*3D() function - a real bug, found by watching the tag
        vanish after a genuine live-refresh poll reused a stage into a
        freshly-rebuilt container without ever calling mountFn (and so
        never calling a mount-function-only tag) again. Fixed, and this
        test protects the fix, not just the original intent."""
        block = self._renderer_block()
        assert "function tagEnvironment(container, env){" in block
        assert "container.dataset.envId = env.id;" in block
        assert "container.dataset.envVersion = String(env.version);" in block
        assert "if(tag) tag(el, name);" in block
        # Ordering, not just presence: the tag must run before the reuse
        # check decides whether to skip mountFn (and so skip a mount-
        # function-only tag) - a mutation that moved the call to just
        # before mountFn(el, name) instead would still contain this exact
        # text, but would silently reintroduce the bug this test exists
        # to catch.
        wire_stages_body = block.split("function wireStages(attrKey, stagesMap, mountFn, tag){", 1)[1].split(
            "\n  }", 1
        )[0]
        assert wire_stages_body.index("if(tag) tag(el, name);") < wire_stages_body.index(
            'if(running && running.renderer){'
        )
        for call in (
            "tagEnvironment(el, environmentFor(identityOf(agent).form));",
            "tagEnvironment(el, BRAIN_VISUAL_ENVIRONMENT)",
            "tagEnvironment(el, MEMORY_VISUAL_ENVIRONMENT)",
            "tagEnvironment(el, ROOM_VISUAL_ENVIRONMENT)",
        ):
            assert call in block
        # And never redundantly inside a mount*3D() function itself -
        # that was the shape that let the tag go stale on reuse.
        for mount_fn in (
            "function mountAgent3D(container, identity){",
            "function mountBrain3D(container, identity, signal){",
            "function mountMemory3D(container, identity, lit, durable){",
            "function mountRoom3D(container, key){",
        ):
            body = block.split(mount_fn, 1)[1].split("\n  }", 1)[0]
            assert "tagEnvironment(" not in body, mount_fn

    def test_brain_memory_and_room_mount_through_their_own_environment_descriptor(self):
        block = self._renderer_block()
        assert "MEMORY_VISUAL_ENVIRONMENT.build(THREE, accent, lit, durable)" in block
        assert "BRAIN_VISUAL_ENVIRONMENT.build(THREE, accent, signal)" in block
        assert "ROOM_VISUAL_ENVIRONMENT.build(THREE, accent)" in block

    def test_the_room_core_environment_still_carries_no_identity_descriptor(self):
        """Phase 4's boundary, re-confirmed after wrapping the Room's
        builder in a descriptor: the Room still receives no {key,hue,
        form,sides}, no agent, nothing an agent-neutral space should
        never own."""
        block = self._renderer_block()
        mount_room = block.split("function mountRoom3D(container, key){", 1)[1].split("\n  }", 1)[0]
        for forbidden in ("identity.key", "identity.hue", "identityOf("):
            assert forbidden not in mount_room

    def test_the_shared_stage_lifecycle_still_has_no_second_copy(self):
        """The registry is a layer above mountStage/wireStages, not a
        reason to duplicate either - re-verified after Phase 5's
        refactor touched all four mount*3D() callers."""
        shell = self._shell()
        assert shell.count("async function mountStage(container, stagesMap, key, hueRgb, sceneBuilder){") == 1
        assert shell.count("function tick(){") == 1
        assert shell.count("function wireStages(attrKey, stagesMap, mountFn, tag){") == 1

    def test_reduced_motion_ordering_still_holds_after_the_registry_refactor(self):
        block = self._renderer_block()
        still_at = block.index('matchMedia("(prefers-reduced-motion: reduce)").matches')
        tick_at = block.index("function tick(){")
        assert still_at < tick_at
