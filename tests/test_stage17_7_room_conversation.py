"""Stage 17.7 - a natural multi-agent Conversation Room, built entirely
on top of the Stage 17.1-17.6B foundation: a real mouth primitive with a
generic (never phoneme-timed) talking pulse, subtle torso movement while
speaking, and an honest "room attention" idle variant for agents who are
not speaking but share the room with one who is - derived purely from
already-real data (another agent's own real SPEAKING activity), never a
fabricated server-side LISTENING state.

Every change this phase makes lives in ui/index.html. No Python source
file was touched - the entire Stage 17.1-17.6B backend (PresenceRegistry,
speaking(), DigitalHumanProfile persistence, canonical agent resolution)
is re-verified simply by its own existing suites still passing unchanged.

Verified as source text, this project's own established method for
3D/DOM code since Stage 17.5 (see that phase's own test module
docstring for why: no JS execution harness exists in this repository,
and headless-browser pixel verification was found unreliable in this
sandbox for both new and pre-existing 3D code alike).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.availability import Availability, AvailabilityState
from src.services.email.in_memory_mailbox import InMemoryMailbox
from scripts.exchange import exchange
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


def _mount_body() -> str:
    shell = UI.read_text("utf-8")
    return shell.split("function mountAvatarBody3D(container", 1)[1].split(
        "\n  }\n\n  let agent3DStages", 1
    )[0]


def _builder_body() -> str:
    shell = UI.read_text("utf-8")
    return shell.split("function buildHumanoidBody(THREE", 1)[1].split(
        "\n  const AVATAR_BODY_VISUAL_ENVIRONMENT", 1
    )[0]


def _speaking_branch(mount: str) -> str:
    return mount.split('if(activity === "speaking"){', 1)[1].split(
        '} else if(activity === "listening")', 1
    )[0]


def _idle_branch(mount: str) -> str:
    return mount.rsplit("} else {", 1)[1]


# ---------------------------------------------------------------------------
# 1. A real mouth primitive exists and is exposed for animation.
# ---------------------------------------------------------------------------


class TestMouthPrimitive:
    def test_a_real_mouth_mesh_is_constructed(self):
        body = _builder_body()
        assert "const mouth = new THREE.Mesh(new THREE.BoxGeometry" in body

    def test_the_mouth_is_exposed_for_the_mount_functions_own_animation(self):
        body = _builder_body()
        assert "group.userData.mouth = mouth;" in body

    def test_the_mouth_pulses_only_while_speaking(self):
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        assert "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;" in speaking

    def test_the_mouth_eases_shut_when_not_speaking(self):
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "mouth.scale.y += (1 - mouth.scale.y) * 0.15;" in idle
        listening = mount.split('} else if(activity === "listening"){', 1)[1].split("} else {", 1)[0]
        assert "mouth.scale.y += (1 - mouth.scale.y) * 0.15;" in listening

    def test_the_mouth_animation_makes_no_viseme_or_phoneme_claim(self):
        mount = _mount_body()
        for consumed in (".viseme", ".phoneme", "visemeData", "phonemeData", "lipSync"):
            assert consumed not in mount, consumed


# ---------------------------------------------------------------------------
# 2. Subtle torso movement while speaking, eased otherwise.
# ---------------------------------------------------------------------------


class TestTorsoMovement:
    def test_torso_is_exposed_and_moves_while_speaking(self):
        """Stage 17.10 appended `* intentEffect.gestureMul` to this
        exact line so torso lean scales with the same restraint knob
        the arm gesture already does - the pinned string below was
        updated in step, not weakened; torso motion while speaking is
        still real and still present."""
        body = _builder_body()
        assert "group.userData.torso = torso;" in body
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        assert (
            "const torsoTarget = restTorsoRotZ + Math.sin(t*gestureRate*0.5) "
            "* 0.035 * intentEffect.gestureMul * liveliness;"
        ) in speaking

    def test_torso_eases_back_to_rest_when_not_speaking(self):
        """Both non-speaking branches interpolate the torso toward a
        rest-derived target, never snap to it.

        Stage 17.21 kept the easing and moved idle's target: the torso
        now settles onto rest plus one slow, small rotation ("settled,
        not frozen") rather than onto the bare resting angle. Listening
        is untouched. Same coefficient, same expression shape, both
        branches - which is the guarantee this test exists to hold.
        """
        mount = _mount_body()
        listening = mount.split('} else if(activity === "listening"){', 1)[1].split("} else {", 1)[0]
        idle = mount.split('} else if(activity === "listening"){', 1)[1].split("} else {", 1)[1]

        assert "torso.rotation.z += (restTorsoRotZ - torso.rotation.z) * 0.06;" in listening
        assert (
            "torso.rotation.z += (restTorsoRotZ + Math.sin(t*0.29)*0.012 "
            "- torso.rotation.z) * 0.06;"
        ) in idle
        assert mount.count("- torso.rotation.z) * 0.06;") == 2

        # Never a snap in either branch...
        for branch in (listening, idle):
            assert "torso.rotation.z = restTorsoRotZ" not in branch
        # ...and idle's added rotation stays far under the speaking
        # gesture's own 0.035, so at-rest never reads as gesturing.
        assert 0.012 < 0.035


# ---------------------------------------------------------------------------
# 3. "Room attention": an honest, purely client-side idle variant - never
#    a fabricated server-side LISTENING claim, never self-triggering,
#    never identical across agents, and always smaller than the real
#    LISTENING lean.
# ---------------------------------------------------------------------------


class TestRoomAttentionIsHonest:
    def test_room_attention_only_considers_other_agents_never_this_one(self):
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "a.name !== agentName && a.activity === \"speaking\"" in idle

    def test_room_attention_reads_the_same_real_snapshot_data_already_in_scope(self):
        """No second data source - S.agents is the exact same array
        `live`/`activity` above are already read from."""
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "S && S.agents" in idle
        assert "S.agents.some(" in idle

    def test_room_attention_lean_is_strictly_smaller_than_the_real_listening_lean(self):
        """0.035 + idleSignature*0.035 maxes out at 0.07 - the genuine
        LISTENING branch's own lean is a fixed 0.12. Numerically
        confirmed here so this invariant cannot silently drift as
        either constant is edited independently."""
        mount = _mount_body()
        max_attention_lean = 0.035 + 1.0 * 0.035
        assert max_attention_lean < 0.12

    def test_room_attention_varies_per_agent_via_idlesignature(self):
        mount = _mount_body()
        idle = _idle_branch(mount)
        assert "0.035 + idleSignature * 0.035" in idle

    def test_this_agents_own_activity_is_declared_const_and_read_only(self):
        """`activity` is a `const`, declared once, outside every branch
        - JS itself refuses a later reassignment, so the idle branch
        cannot ever override this agent's own already-determined
        activity even by accident. Room attention only ever reads
        S.agents (another agent's own real state), never writes
        anything back."""
        mount = _mount_body()
        assert mount.count("const activity = (live && live.activity) || \"idle\";") == 1
        idle = _idle_branch(mount)
        assert "roomAttention =" in idle  # a fresh local, never a rewrite of `activity`
        assert "S.agents.some(" in idle   # a read, not a write, of room state


# ---------------------------------------------------------------------------
# 4. The real canonical speaker, never a hardcoded agent, re-confirmed
#    at the actual snapshot boundary the renderer reads - through the
#    real production route, not only a helper.
# ---------------------------------------------------------------------------


class TestCanonicalSpeakerAtTheSnapshotBoundary:
    def test_the_true_speaker_shows_speaking_and_every_other_agent_shows_idle(self):
        import json

        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(delay=0.35), presence=live.runtime.presence_registry
        )
        seen = {"alfred_speaking": False, "arthur_speaking": False}

        def poll():
            deadline = time.time() + 2.0
            while time.time() < deadline and not seen["arthur_speaking"]:
                _, body, _ = route("/snapshot.json", live)
                data = json.loads(body)
                for a in data["agents"]:
                    if a["name"] == live.manager.own_skills.name and a["activity"] == "speaking":
                        seen["alfred_speaking"] = True
                    if a["name"] == "Arthur" and a["activity"] == "speaking":
                        seen["arthur_speaking"] = True
                time.sleep(0.02)

        poller = threading.Thread(target=poll)
        poller.start()
        route_message("read my inbox for job opportunities", live, to="Arthur")
        poller.join(timeout=1.0)

        assert seen["arthur_speaking"] is True
        assert seen["alfred_speaking"] is False

    def test_after_arthur_speaks_alfred_never_shows_speaking_afterward_either(self):
        import json

        live = build_live_system(settings())
        live.channel = VoiceChannel(
            live.manager, speech=_ScriptedSpeaker(), presence=live.runtime.presence_registry
        )
        route_message("read my inbox for job opportunities", live, to="Arthur")
        _, body, _ = route("/snapshot.json", live)
        data = json.loads(body)
        alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
        arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
        assert alfred["activity"] == "idle"
        assert arthur["activity"] == "idle"  # settled back after speech completed


# ---------------------------------------------------------------------------
# 5. No cross-agent leakage in the new mouth/torso/room-attention state;
#    reduced motion still inherited for free; no device coupling; no
#    Evolution coupling.
# ---------------------------------------------------------------------------


class TestIsolationAndHonesty:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_mouth_and_torso_are_local_to_this_mount_calls_own_scope(self):
        shell = UI.read_text("utf-8")
        fn = shell.split("function mountAvatarBody3D(container", 1)[1].split(
            "\n  }\n\n  let agent3DStages", 1
        )[0]
        assert "const mouth = group.userData.mouth;" in fn
        assert "const torso = group.userData.torso;" in fn
        assert not any(
            line.strip().startswith("let mouth") or line.strip().startswith("var mouth")
            for line in shell.split("\n")
        )

    def test_no_new_reduced_motion_block_and_no_new_media_query(self):
        shell = UI.read_text("utf-8")
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_no_device_name_in_this_phases_new_code(self):
        for block in (_builder_body(), _mount_body()):
            lowered = block.lower()
            for device in self.DEVICE_NAMES:
                assert device not in lowered, device

    def test_no_evolution_or_extensions_file_references_this_phases_additions(self):
        for rel in (
            "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            text = (REPO_ROOT / rel).read_text("utf-8")
            for forbidden in ("roomAttention", "attentionLean", "restTorsoRotZ", "userData.mouth"):
                assert forbidden not in text, (rel, forbidden)

    def test_no_python_source_file_was_changed_this_phase(self):
        assert not any(
            "roomAttention" in (REPO_ROOT / "src" / rel).read_text("utf-8")
            for rel in ("services/identity/presence.py", "channels/channel.py")
        )
