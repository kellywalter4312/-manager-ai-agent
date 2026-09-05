"""Stage 17.5 - the real Digital Human rendering foundation.

Two genuinely different kinds of proof live in this file, and neither
substitutes for the other:

    DATA LAYER (Python, executed)   DigitalHumanProfile really reaches
                                     the renderer boundary (the
                                     snapshot JSON) with every field
                                     intact, per-agent, independent of
                                     Brain/Memory/Voice/Personality/
                                     Capabilities/Connections/LLM, and
                                     provider-replaceable - proven the
                                     same way every prior Stage 17
                                     phase proved its own backend
                                     guarantees: real construction,
                                     real assertions, real mutation
                                     testing.

    RENDERER (JavaScript, static)   the actual humanoid-body builder
                                     added to ui/index.html - proven
                                     the same way this project's own
                                     pre-existing 3D code already is
                                     (see TestAgent3DStaysHonest in
                                     tests/test_snapshot.py): reading
                                     the real source text, because this
                                     project has no JS execution
                                     harness and headless-browser
                                     pixel verification was found, by
                                     hand, to be unreliable in this
                                     sandboxed environment (WebGL
                                     context creation hung even with
                                     software-rendering flags forced,
                                     for both this new code and the
                                     pre-existing, already-shipped 3D
                                     emblems alike - an environment
                                     limitation, not a defect in either).
                                     Structural tests are this
                                     project's own established standard
                                     for 3D code, not a downgrade
                                     invented for this phase.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.providers.avatar import AvatarProfile, AvatarProvider
from src.providers.availability import Availability, AvailabilityState
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.personality import personality_for
from src.services.identity.presence import (
    PresenceState,
    digital_human_profile_for,
    presence_for,
    set_avatar_preference,
    set_digital_human_style,
)
from src.services.identity.voice import voice_for
from scripts.exchange import exchange, memory_for
from scripts.serve_ui import build_live_system, route, route_message

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


class _ScriptedSpeaker(SpeechAdapter):
    def __init__(self, delay: float = 0.0):
        self._delay = delay

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout=None):
        raise NotImplementedError

    def speak(self, text, voice=None):
        if self._delay:
            time.sleep(self._delay)
        return True


# ---------------------------------------------------------------------------
# 1-5. DigitalHumanProfile genuinely reaches the renderer boundary - the
#      snapshot JSON both the static export and the live server hand a
#      renderer - with every configured dimension intact.
# ---------------------------------------------------------------------------


class TestProfileReachesTheRendererBoundary:
    def test_a_bare_profile_reaches_the_snapshot(self):
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert alfred["avatar"]["profile"]["avatar_id"]

    def test_avatar_selection_changes_the_renderer_configuration(self):
        """Different avatar_id -> different accent_color reaching the
        snapshot - the one dimension with a real catalog behind it."""
        runtime, manager, channel, conversation, centre = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            manager.own_skills.name, "calm_composed", provider=runtime.avatar_provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        data = snapshot(centre, conversation=conversation, channel=channel)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert alfred["avatar"]["avatar"]["avatar_id"] == "calm_composed"
        assert alfred["avatar"]["avatar"]["accent_color"] == "teal"

    def test_body_configuration_reaches_the_snapshot(self):
        _, manager, channel, conversation, centre = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"body": "broad-shouldered"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        data = snapshot(centre, conversation=conversation, channel=channel)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert alfred["avatar"]["profile"]["body"] == "broad-shouldered"

    def test_posture_configuration_reaches_the_snapshot(self):
        _, manager, channel, conversation, centre = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        data = snapshot(centre, conversation=conversation, channel=channel)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert alfred["avatar"]["profile"]["posture"] == "seated"

    def test_appearance_and_clothing_reach_the_snapshot(self):
        _, manager, channel, conversation, centre = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"appearance": "sharp", "clothing": "formal suit"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        data = snapshot(centre, conversation=conversation, channel=channel)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert alfred["avatar"]["profile"]["appearance"] == "sharp"
        assert alfred["avatar"]["profile"]["clothing"] == "formal suit"


# ---------------------------------------------------------------------------
# 6. Alfred and Arthur remain independent through the full renderer path.
# ---------------------------------------------------------------------------


class TestAgentsRemainIndependentThroughTheRenderer:
    def test_alfred_and_arthur_reach_the_snapshot_with_different_profiles(self):
        _, manager, channel, conversation, centre = system()
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
        data = snapshot(centre, conversation=conversation, channel=channel)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
        assert alfred["avatar"]["profile"]["posture"] == "standing"
        assert arthur["avatar"]["profile"]["posture"] == "seated"

    def test_24_alfreds_avatar_cannot_accidentally_become_arthurs(self):
        _, manager, _, _, _ = system()
        alfred_memory = memory_for(manager, manager.own_skills.name)
        arthur_memory = memory_for(manager, "Arthur")
        runtime, *_ = system()
        set_avatar_preference(
            manager.own_skills.name, "warm_formal", provider=runtime.avatar_provider,
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        from src.services.identity.presence import avatar_preference_for

        assert avatar_preference_for("Arthur", recall=arthur_memory.recall) is None

    def test_25_arthurs_avatar_cannot_accidentally_become_alfreds(self):
        runtime, manager, _, _, _ = system()
        arthur_memory = memory_for(manager, "Arthur")
        alfred_memory = memory_for(manager, manager.own_skills.name)
        set_avatar_preference(
            "Arthur", "bright_approachable", provider=runtime.avatar_provider,
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        from src.services.identity.presence import avatar_preference_for

        assert avatar_preference_for(manager.own_skills.name, recall=alfred_memory.recall) is None


# ---------------------------------------------------------------------------
# 7-13. Renderer/provider replacement preserves every canonical identity.
# ---------------------------------------------------------------------------


class _OneAvatarProvider(AvatarProvider):
    def __init__(self):
        self._only = AvatarProfile(avatar_id="only", label="Only", accent_color="amber")

    @property
    def name(self) -> str:
        return "one"

    def catalog(self):
        return (self._only,)

    def default_for(self, agent_key: str):
        return self._only


class TestProviderReplacementPreservesCanonicalIdentity:
    def test_provider_replacement_preserves_agent_brain_memory_registry(self):
        runtime, manager, _, _, centre = system()
        registry_size_before = len(manager.registry.all_agents())
        brain_before = manager.brain
        memory_before = manager.memory
        skills_before = manager.own_skills

        presence = presence_for(
            manager.own_skills.name, provider=_OneAvatarProvider(), recall=manager.memory.recall
        )

        assert presence.avatar.avatar_id == "only"
        assert presence.profile.avatar_id == "only"
        assert len(manager.registry.all_agents()) == registry_size_before
        assert manager.brain is brain_before
        assert manager.memory is memory_before
        assert manager.own_skills is skills_before

    def test_provider_replacement_preserves_voiceprofile_and_personality(self):
        _, manager, _, _, _ = system()
        skills = manager.own_skills
        voice_before = voice_for(skills)
        personality_before = personality_for(skills)

        presence_for(skills.name, provider=_OneAvatarProvider(), recall=manager.memory.recall)

        assert voice_for(skills) == voice_before
        assert personality_for(skills) == personality_before

    def test_provider_replacement_preserves_capabilities_and_connections(self):
        runtime, manager, _, _, centre = system()
        caps_before = tuple(
            c.name for c in centre.overview().agent(manager.own_skills.name).capabilities
        )
        connection_before = runtime.connection

        presence_for(manager.own_skills.name, provider=_OneAvatarProvider(), recall=manager.memory.recall)

        caps_after = tuple(
            c.name for c in centre.overview().agent(manager.own_skills.name).capabilities
        )
        assert caps_after == caps_before
        assert runtime.connection is connection_before


# ---------------------------------------------------------------------------
# 14/15. LLM independence, both directions.
# ---------------------------------------------------------------------------


class TestLLMIndependence:
    def test_llm_provider_changes_preserve_digitalhumanprofile(self):
        runtime, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        avatar = runtime.avatar_provider.default_for(manager.own_skills.name)
        before = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)
        assert after == before
        assert after.posture == "standing"

    def test_digitalhumanprofile_changes_preserve_brain_identity(self):
        runtime, manager, _, _, _ = system()
        brain_before = manager.brain
        memory = memory_for(manager, manager.own_skills.name)

        set_digital_human_style(
            manager.own_skills.name, {"expression_style": "warm"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )

        assert manager.brain is brain_before


# ---------------------------------------------------------------------------
# 16-19. Speaking/listening/idle - the real presence lifecycle, not a
#        fake timer, driving what the renderer will read.
# ---------------------------------------------------------------------------


class TestSpeakingListeningIdleDriveTheRenderer:
    def test_speaking_presence_is_genuinely_observable_mid_call(self):
        """The exact proof the renderer's own animate() loop depends on
        being real: a concurrent poll of the same snapshot boundary the
        renderer reads sees SPEAKING while a real, slow speak() call is
        still blocking - not a client-side guess, not a fixed timer."""
        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(delay=0.4), presence=live.runtime.presence_registry
        )
        seen_speaking = threading.Event()

        def poll():
            import json
            deadline = time.time() + 2.0
            while time.time() < deadline and not seen_speaking.is_set():
                _, body, _ = route("/snapshot.json", live)
                data = json.loads(body)
                alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
                if alfred["activity"] == "speaking":
                    seen_speaking.set()
                time.sleep(0.02)

        poller = threading.Thread(target=poll)
        poller.start()
        route_message("what can you do?", live)
        poller.join(timeout=1.0)

        assert seen_speaking.is_set()

    def test_speaking_clears_after_speech_completes(self):
        import json

        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(), presence=live.runtime.presence_registry
        )
        route_message("what can you do?", live)
        _, body, _ = route("/snapshot.json", live)
        data = json.loads(body)
        alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
        assert alfred["activity"] == "idle"

    def test_idle_is_the_correct_fallback_with_no_evidence(self):
        p = presence_for("Alfred", provider=DefaultAvatarProvider())
        assert p.state == PresenceState.IDLE


# ---------------------------------------------------------------------------
# 20-22, 27. Reduced motion, no duplicate Agent, no second store, device
#            independence - re-affirmed for this phase's own additions.
# ---------------------------------------------------------------------------


class TestArchitecturalHonesty:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_second_memory_store_was_introduced(self):
        for rel in ("src/services/identity/presence.py",):
            lowered = (REPO_ROOT / rel).read_text("utf-8").lower()
            for forbidden in ("avatar.db", "digital_human.db", "presentation.db", "sqlite", ".json'"):
                assert forbidden not in lowered

    def test_no_device_name_in_this_phases_new_javascript(self):
        shell = UI.read_text("utf-8")
        block = shell.split("function buildHumanoidBody", 1)[1].split(
            "let agent3DStages", 1
        )[0].lower()
        for device in self.DEVICE_NAMES:
            assert device not in block, device

    def test_no_new_reduced_motion_block_was_added(self):
        """mountAvatarBody3D reuses mountStage() - the one place
        prefers-reduced-motion is already read and already gates the
        animate() call - inherited for free, never reimplemented."""
        shell = UI.read_text("utf-8")
        assert shell.count("@media (prefers-reduced-motion") == 1
        fn = shell.split("function mountAvatarBody3D(container", 1)[1].split(
            "\n  }\n", 1
        )[0]
        assert "prefers-reduced-motion" not in fn
        assert "mountStage(container, avatarBody3DStages" in fn

    def test_no_hardcoded_agent_name_in_the_new_renderer_code(self):
        shell = UI.read_text("utf-8")
        block = shell.split("function buildHumanoidBody", 1)[1].split(
            "function wireAvatarBody3D", 1
        )[0]
        assert '"Alfred"' not in block and '"Arthur"' not in block


# ---------------------------------------------------------------------------
# 23, 28, 29. Conversation Room / agent selection / avatar-vs-agent
#             separation.
# ---------------------------------------------------------------------------


class TestRoomAndSelectionSeparation:
    def test_room_participants_each_resolve_their_own_avatar(self):
        _, manager, channel, conversation, centre = system()
        exchange(channel, conversation, "read my inbox for job opportunities", addressed="Arthur")

        overview = centre.overview()
        alfred_view = overview.agent(manager.own_skills.name)
        arthur_view = overview.agent("Arthur")
        assert alfred_view.presence.agent_key == manager.own_skills.name
        assert arthur_view.presence.agent_key == "Arthur"
        assert alfred_view.presence.avatar != arthur_view.presence.avatar

    def test_agent_selection_still_resolves_through_the_canonical_registry(self):
        """Neutral text isolates the explicit `to`/`addressed` mechanism
        from natural capability routing - the lesson Stage 17.3/17.4's
        own equivalent tests needed, applied here from the start."""
        _, manager, channel, conversation, _ = system()
        turn = exchange(channel, conversation, "what can you do?", addressed="Arthur")
        assert turn.handled_by == "Arthur"

    def test_the_avatar_picker_and_who_picker_stay_two_separate_controls(self):
        """Re-affirmed for Stage 17.5: the new avatar-body 3D surface
        must not introduce a third, conflicting selection mechanism -
        it is wired purely as a *rendering* consumer of S.agents, never
        as a click target that changes selectedAgent or posts
        anywhere."""
        shell = UI.read_text("utf-8")
        fn = shell.split("function wireAvatarBody3D(){", 1)[1].split("\n  }", 1)[0]
        assert "selectedAgent" not in fn
        assert "fetch(" not in fn
        assert "addEventListener" not in fn


# ---------------------------------------------------------------------------
# Mutation-adjacent structural guarantees for the new humanoid builder.
# ---------------------------------------------------------------------------


class TestHumanoidBuilderStructure:
    def test_only_proven_available_geometry_constructors_are_used(self):
        """No THREE.CapsuleGeometry or other constructor not already
        proven present by this file's own pre-existing 3D code - an
        unsupported constructor would throw inside sceneBuilder(),
        uncaught by mountStage(), and take the whole page down with it
        rather than degrading to the honest fallback."""
        shell = UI.read_text("utf-8")
        fn = shell.split("function buildHumanoidBody(THREE", 1)[1].split(
            "\n  const AVATAR_BODY_VISUAL_ENVIRONMENT", 1
        )[0]
        # Stage 17.18 rebuilt the body's anatomy and with it which
        # constructors it reaches for (the faceted IcosahedronGeometry head
        # became a real scaled sphere, for one). The guard that matters is
        # the negative one - nothing outside the proven set - so it is now
        # written that way round: every THREE.*Geometry the builder names
        # must be one this file's own pre-existing 3D code already proved
        # the vendored build ships. That catches a CapsuleGeometry exactly
        # as before, and keeps catching it when the anatomy changes again.
        import re as _re
        proven = {
            "IcosahedronGeometry", "SphereGeometry", "CylinderGeometry",
            "BoxGeometry", "DodecahedronGeometry", "OctahedronGeometry",
            "TorusGeometry", "BufferGeometry",
        }
        used = set(_re.findall(r"THREE\.(\w+Geometry)", fn))
        assert used, "the builder must actually build geometry"
        assert used <= proven, used - proven
        assert "CapsuleGeometry" not in fn

    def test_avatar_body_environment_has_the_established_registry_shape(self):
        """Superseded by Stage 17.11: the single descriptor became a
        body_type-keyed registry (the same extension seam
        environmentFor()/AGENT_VISUAL_ENVIRONMENTS already established),
        with exactly one real entry today - "humanoid" - still pointing
        at this exact build function. See avatarBodyEnvironmentFor()'s
        own comment for why."""
        shell = UI.read_text("utf-8")
        assert (
            "const AVATAR_BODY_VISUAL_ENVIRONMENTS = {\n    humanoid: "
            "{ id: \"avatar-humanoid-body\", version: 1, build: buildHumanoidBody },\n  };" in shell
        )

    def test_posture_seated_produces_different_leg_geometry_than_default(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function buildHumanoidBody(THREE", 1)[1].split(
            "\n  const AVATAR_BODY_VISUAL_ENVIRONMENT", 1
        )[0]
        assert 'seated ? 0.34 : 0.46' in fn

    def test_body_dimension_uses_a_deterministic_never_random_variation(self):
        shell = UI.read_text("utf-8")
        assert "function _stableUnit(text){" in shell
        fn = shell.split("function _stableUnit(text){", 1)[1].split("\n  }", 1)[0]
        assert "Math.random" not in fn

    def test_speaking_uses_no_viseme_or_phoneme_data_it_does_not_have(self):
        """The renderer's own speaking animation must not *consume* a
        viseme/phoneme value - only the real `activity` string. This
        checks for the absence of a claim (a value read and used), not
        for the absence of the word: this file's own honest comment
        explaining why lip-sync is not implemented legitimately
        mentions "viseme"/"phoneme"/"lip-sync" in English prose, and a
        bare substring check would flag that correct disclaimer as if
        it were the violation - see Stage 17.2's own test suite for the
        identical false-positive lesson learned the first time."""
        shell = UI.read_text("utf-8")
        fn = shell.split("function mountAvatarBody3D(container", 1)[1].split("\n  }\n", 1)[0]
        # Code-identifier shapes only (camelCase/dotted access) - never
        # the bare English words, which this function's own honest
        # disclaimer comment legitimately contains.
        for consumed in (".viseme", ".phoneme", "visemeData", "phonemeData", "lipSync"):
            assert consumed not in fn, consumed

    def test_speaking_and_listening_and_idle_are_all_genuinely_distinct_branches(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function mountAvatarBody3D(container", 1)[1].split("\n  }\n", 1)[0]
        assert 'activity === "speaking"' in fn
        assert 'activity === "listening"' in fn
        assert '(live && live.activity) || "idle"' in fn

    def test_the_manager_and_specialist_emblem_environments_are_unchanged(self):
        """Absolute preservation: Stage 17.5 adds a new, additive
        environment - it must not touch buildManagerEmblem/
        buildSpecialistEmblem/AGENT_VISUAL_ENVIRONMENTS/mountAgent3D/
        wireAgent3D, all already exact-string pinned by
        tests/test_snapshot.py. Re-checked here as this phase's own
        explicit "absolute preservation rule"."""
        shell = UI.read_text("utf-8")
        assert "function buildManagerEmblem(THREE, accent){" in shell
        assert "function buildSpecialistEmblem(THREE, accent){" in shell
        assert 'core:    { id: "alfred-tactical-core", version: 1, build: buildManagerEmblem },' in shell
        assert 'polygon: { id: "specialist-wing-core", version: 1, build: buildSpecialistEmblem },' in shell
        assert "environmentFor(identity.form)" in shell
        assert "wireAgent3D();" in shell
        assert "wireAvatarBody3D();" in shell
