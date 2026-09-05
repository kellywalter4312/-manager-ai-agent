"""Stage 17.10 - Brain-Driven Natural Presentation.

Stage 17.9 built the full seam (PresenceRegistry.set_intent/clear_intent/
get_intent, AgentPresence.intent, VoiceChannel.deliver()'s exception-safe
wiring, and the renderer's PRESENTATION_INTENT_EFFECTS consumption) but
its producer, presentation_intent_for_turn(), classified only one real
fact (celebration). Every other turn - including a perfectly ordinary,
successful one - produced None.

Stage 17.10 widens that one producer function - the only file this phase
touches on the Python side - to classify several more already-real,
already-structured facts a turn carries (success, memory recall, tool
use, delegation), still with zero text inspection and zero second call
to any LLM/Brain/reasoning system:

    celebration set          -> "acknowledging"  (unchanged since 17.9)
    not succeeded             -> None              (still conservative)
    memory_recalled > 0       -> "thinking"
    tool_used is not None     -> "emphasizing"
    delegated                 -> "explaining"
    otherwise (plain success) -> "concluding"

"listening" remains, by design, the renderer's own pre-existing
LISTENING-activity behaviour (Stage 17.2/17.3), never a PresentationIntent
value a turn produces.

On the renderer side, the only change is that the existing torso-lean
line now carries the same `intentEffect.gestureMul` the arm gesture
already did, so torso motion scales with the same restraint knob rather
than a second, independent one - "coordinate the existing systems," not
build a new one.

No second Brain/Memory/Agent/LLM was created; no registry beyond
PresenceRegistry; no renderer beyond mountAvatarBody3D. Verified with
the same two methods every Stage 17.x file since 17.5 uses: executed
Python tests for the real data/registry/producer layer, source-text
structural checks for the renderer.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from src.channels.channel import Response, VoiceChannel
from src.channels.speech import SpeechAdapter
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.views import ConversationTurn
from src.providers.availability import Availability, AvailabilityState
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.presence import (
    PRESENTATION_INTENT_VOCABULARY,
    PresenceRegistry,
    PresentationIntent,
    digital_human_profile_for,
    presentation_intent_for_turn,
    set_digital_human_style,
)
from src.services.identity.voice import voice_for
from scripts.exchange import memory_for

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
PRESENCE_SRC = REPO_ROOT / "src" / "services" / "identity" / "presence.py"


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
    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout=None):
        raise NotImplementedError

    def speak(self, text, voice=None) -> bool:
        if self._delay:
            time.sleep(self._delay)
        return True


class _RaisingSpeaker(SpeechAdapter):
    name = "raising"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout=None):
        raise NotImplementedError

    def speak(self, text, voice=None) -> bool:
        raise RuntimeError("synthesis exploded")


def _turn(handled_by: str, **overrides) -> ConversationTurn:
    fields = dict(
        request="req", handled_by=handled_by, delegated=False, steps=(),
        outcome="ok", succeeded=True,
    )
    fields.update(overrides)
    return ConversationTurn(**fields)


def _shell() -> str:
    return UI.read_text("utf-8")


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


# ---------------------------------------------------------------------------
# 1. Real turn -> presentation intent: each real structured fact
#    genuinely drives a distinct, correct classification.
# ---------------------------------------------------------------------------


class TestRealTurnToPresentationIntent:
    def test_celebration_produces_acknowledging(self):
        turn = _turn("Alfred", celebration={"kind": "milestone"})
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="acknowledging")

    def test_memory_recall_produces_thinking(self):
        turn = _turn("Alfred", memory_recalled=3)
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="thinking")

    def test_tool_use_produces_emphasizing(self):
        turn = _turn("Alfred", tool_used="calendar")
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="emphasizing")

    def test_delegation_produces_explaining(self):
        turn = _turn("Arthur", delegated=True)
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="explaining")

    def test_plain_success_produces_concluding(self):
        turn = _turn("Alfred")
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="concluding")

    def test_failure_produces_none(self):
        turn = _turn("Alfred", succeeded=False)
        assert presentation_intent_for_turn(turn) is None

    def test_priority_celebration_beats_everything_else(self):
        turn = _turn(
            "Alfred", celebration={"kind": "milestone"}, memory_recalled=5,
            tool_used="calendar", delegated=True,
        )
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="acknowledging")

    def test_priority_memory_recall_beats_tool_and_delegation(self):
        turn = _turn("Alfred", memory_recalled=1, tool_used="calendar", delegated=True)
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="thinking")

    def test_priority_tool_use_beats_delegation(self):
        turn = _turn("Alfred", tool_used="calendar", delegated=True)
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="emphasizing")

    def test_every_real_result_is_in_the_shared_vocabulary(self):
        cases = [
            _turn("Alfred", celebration={"kind": "milestone"}),
            _turn("Alfred", memory_recalled=1),
            _turn("Alfred", tool_used="calendar"),
            _turn("Alfred", delegated=True),
            _turn("Alfred"),
            _turn("Alfred", succeeded=False),
        ]
        for turn in cases:
            intent = presentation_intent_for_turn(turn)
            if intent is not None:
                assert intent.gesture in PRESENTATION_INTENT_VOCABULARY

    def test_classification_uses_no_text_reading(self):
        """Two turns, identical structured facts, opposite text tone -
        identical result."""
        plain = _turn("Alfred", tool_used="calendar", outcome="done", request="do it")
        loud = _turn(
            "Alfred", tool_used="calendar",
            outcome="AMAZING SPECTACULAR SUCCESS!!!", request="PLEASE DO THIS NOW",
        )
        assert presentation_intent_for_turn(plain) == presentation_intent_for_turn(loud)

    def test_no_llm_or_brain_call_is_made_by_the_classifier(self):
        """Structural proof, not just absence-of-text-reading: the
        function body itself contains no reference to a brain/LLM
        collaborator - it only ever reads getattr() facts."""
        import ast
        import inspect

        source = inspect.getsource(presentation_intent_for_turn)
        tree = ast.parse(source)
        calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        # Only ever getattr() (reading a fact) or PresentationIntent()
        # (constructing the result) - no third callable of any kind,
        # and in particular nothing brain/LLM-shaped.
        assert set(calls) <= {"getattr", "PresentationIntent"}
        assert "getattr" in calls
        body_after_docstring = source.split('"""', 2)[-1]
        assert "brain" not in body_after_docstring.lower()
        assert "llm" not in body_after_docstring.lower()


# ---------------------------------------------------------------------------
# 2. Intent reaches the correct canonical speaker - real channel wiring,
#    not just the pure function.
# ---------------------------------------------------------------------------


class TestIntentReachesCanonicalSpeaker:
    def test_a_plain_successful_turn_asserts_concluding_on_the_true_speaker(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(delay=0.3), presence=registry)
        turn = _turn("Arthur")
        response = Response(text="d", speech="d", spoken=False, turn=turn)

        arthur_seen: list[bool] = []
        alfred_seen: list[bool] = []

        def watch():
            for _ in range(15):
                arthur_seen.append(registry.get_intent("Arthur") == PresentationIntent(gesture="concluding"))
                alfred_seen.append(registry.get_intent(manager.own_skills.name) is not None)
                time.sleep(0.03)

        watcher = threading.Thread(target=watch)
        watcher.start()
        channel.deliver(response)
        watcher.join()

        assert True in arthur_seen
        assert all(v is False for v in alfred_seen)

    def test_an_unmatched_request_never_gets_a_concluding_intent(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        turn = ConversationTurn(
            request="xyzzy", handled_by="nobody", delegated=False, steps=(),
            outcome="nothing matched", succeeded=False,
        )
        response = Response(text="nothing matched", speech="nothing matched", spoken=False, turn=turn)

        marks: list[str] = []
        original = registry.set_intent
        registry.set_intent = lambda k, i: (marks.append(k), original(k, i))  # type: ignore[method-assign]

        channel.deliver(response)
        assert marks == []


# ---------------------------------------------------------------------------
# 3. Different intents produce different renderer behavior.
# ---------------------------------------------------------------------------


class TestDifferentIntentsProduceDifferentBehavior:
    def test_effects_table_still_gives_each_produced_gesture_a_distinct_multiplier_pair(self):
        shell = _shell()
        table = shell.split("const PRESENTATION_INTENT_EFFECTS = {", 1)[1].split("\n  };", 1)[0]
        import re

        rows = {
            k: (g, n)
            for k, g, n in re.findall(r"(\w+):\s*\{ gestureMul: ([0-9.]+),\s*nodMul: ([0-9.]+)", table)
        }
        produced = {"acknowledging", "thinking", "emphasizing", "explaining", "concluding"}
        assert produced.issubset(rows.keys())
        pairs = [rows[g] for g in produced]
        assert len(set(pairs)) == len(pairs), "two produced gestures share an identical multiplier pair"

    def test_torso_now_scales_with_the_same_gesturemul_the_arms_use(self):
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        assert (
            "const torsoTarget = restTorsoRotZ + Math.sin(t*gestureRate*0.5) "
            "* 0.035 * intentEffect.gestureMul * liveliness;"
        ) in speaking


# ---------------------------------------------------------------------------
# 4/5. Intent is temporary, and clears even when speak() raises.
# ---------------------------------------------------------------------------


class TestIntentIsTemporaryAndExceptionSafe:
    def test_intent_clears_the_instant_delivery_returns(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        channel.deliver(Response(text="d", speech="d", spoken=False, turn=_turn(manager.own_skills.name)))
        assert registry.get_intent(manager.own_skills.name) is None

    def test_intent_clears_even_when_speak_raises(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_RaisingSpeaker(), presence=registry)
        import pytest

        with pytest.raises(RuntimeError):
            channel.deliver(Response(text="d", speech="d", spoken=False, turn=_turn(manager.own_skills.name, tool_used="calendar")))
        assert registry.get_intent(manager.own_skills.name) is None

    def test_a_second_turn_never_sees_a_stale_intent_from_the_first(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        channel.deliver(Response(
            text="d", speech="d", spoken=False,
            turn=_turn(manager.own_skills.name, celebration={"kind": "milestone"}),
        ))
        assert registry.get_intent(manager.own_skills.name) is None
        channel.deliver(Response(text="d", speech="d", spoken=False, turn=_turn(manager.own_skills.name, succeeded=False)))
        assert registry.get_intent(manager.own_skills.name) is None


# ---------------------------------------------------------------------------
# 6. No cross-agent leakage.
# ---------------------------------------------------------------------------


class TestNoCrossAgentLeakage:
    def test_two_agents_speaking_in_sequence_never_confuse_intents(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)

        channel.deliver(Response(text="d", speech="d", spoken=False, turn=_turn(manager.own_skills.name, tool_used="mail")))
        assert registry.get_intent("Arthur") is None

        channel.deliver(Response(text="d", speech="d", spoken=False, turn=_turn("Arthur", memory_recalled=2)))
        assert registry.get_intent(manager.own_skills.name) is None
        assert registry.get_intent("Arthur") is None  # cleared after its own turn too

    def test_registry_intent_stores_are_keyed_independently(self):
        reg = PresenceRegistry()
        reg.set_intent("Alfred", PresentationIntent(gesture="explaining"))
        reg.set_intent("Arthur", PresentationIntent(gesture="thinking"))
        assert reg.get_intent("Alfred").gesture == "explaining"
        assert reg.get_intent("Arthur").gesture == "thinking"


# ---------------------------------------------------------------------------
# 7. No second Brain/Memory/Agent/renderer system was created.
# ---------------------------------------------------------------------------


class TestNoSecondReasoningOrRendererSystem:
    def test_presentation_intent_for_turn_is_still_the_only_producer_function(self):
        import re

        hits = []
        for path in (REPO_ROOT / "src").rglob("*.py"):
            if "def presentation_intent_for_turn" in path.read_text("utf-8"):
                hits.append(path)
        assert hits == [PRESENCE_SRC]

    def test_no_new_class_was_added_to_presence_py_this_phase(self):
        import ast

        tree = ast.parse(PRESENCE_SRC.read_text("utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert classes == {
            "PresenceState", "PresenceRegistry", "DigitalHumanProfile",
            "AgentPresence", "PresentationIntent",
        }

    def test_no_second_mountavatarbody3d_or_stage_map_was_added(self):
        import re

        shell = _shell()
        assert shell.count("function mountAvatarBody3D(container") == 1
        stage_maps = set(re.findall(r"let (\w+3DStages)\s*=\s*new Map\(\)", shell))
        assert stage_maps == {
            "agent3DStages", "brain3DStages", "memory3DStages",
            "room3DStages", "avatarBody3DStages",
        }

    def test_nothing_in_manager_or_agents_references_the_classifier(self):
        import re

        for rel in ("src/manager", "src/agents"):
            for path in (REPO_ROOT / rel).rglob("*.py"):
                text = path.read_text("utf-8")
                assert re.search(r"\bpresentation_intent_for_turn\b", text) is None, path


# ---------------------------------------------------------------------------
# 8/9. LLM-provider and VoiceProfile independence.
# ---------------------------------------------------------------------------


class TestLLMAndVoiceIndependence:
    def test_swapping_the_brain_does_not_change_the_stored_digital_human_profile(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing", "gesture_style": "measured"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        avatar = DefaultAvatarProvider().default_for(manager.own_skills.name)
        before = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        after = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)
        assert after == before
        assert after.posture == "standing"
        assert after.gesture_style == "measured"

    def test_swapping_the_brain_does_not_change_the_classifiers_output(self):
        _, manager, _, _, _ = system()

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]
        turn = _turn(manager.own_skills.name, tool_used="calendar")
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="emphasizing")

    def test_two_agents_with_different_voices_still_get_independently_classified_intents(self):
        _, manager, _, _, _ = system()
        arthur = manager.registry.get("Arthur")
        assert voice_for(arthur) != voice_for(manager.own_skills)

        alfred_turn = _turn(manager.own_skills.name, memory_recalled=1)
        arthur_turn = _turn("Arthur", tool_used="mail")
        assert presentation_intent_for_turn(alfred_turn) == PresentationIntent(gesture="thinking")
        assert presentation_intent_for_turn(arthur_turn) == PresentationIntent(gesture="emphasizing")


# ---------------------------------------------------------------------------
# 10/11. Future/unregistered agents never fabricated; no hardcoded names.
# ---------------------------------------------------------------------------


class TestGenericAcrossFutureAgentsNoHardcoding:
    def test_an_unregistered_agent_name_still_classifies_and_stores_correctly(self):
        turn = _turn("FutureSpecialist", tool_used="whatever")
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="emphasizing")

        reg = PresenceRegistry()
        reg.set_intent("FutureSpecialist", presentation_intent_for_turn(turn))
        assert reg.get_intent("FutureSpecialist").gesture == "emphasizing"

    def test_no_hardcoded_agent_name_in_the_widened_classifier(self):
        text = PRESENCE_SRC.read_text("utf-8")
        region = text.split("def presentation_intent_for_turn", 1)[1]
        for name in ("Alfred", "Arthur"):
            assert name not in region, name

    def test_no_hardcoded_agent_name_in_the_torso_edit(self):
        speaking = _speaking_branch(_mount_body())
        torso_line = [ln for ln in speaking.splitlines() if "const torsoTarget = restTorsoRotZ" in ln][0]
        for name in ("Alfred", "Arthur"):
            assert name not in torso_line


# ---------------------------------------------------------------------------
# 12. Reduced-motion behavior remains respected.
# ---------------------------------------------------------------------------


class TestReducedMotionRespected:
    def test_no_new_reduced_motion_block_or_media_query(self):
        shell = _shell()
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_no_math_random_in_the_widened_classifier_or_torso_edit(self):
        text = PRESENCE_SRC.read_text("utf-8")
        region = text.split("def presentation_intent_for_turn", 1)[1]
        assert "random" not in region.lower()
        speaking = _speaking_branch(_mount_body())
        assert "Math.random" not in speaking


# ---------------------------------------------------------------------------
# 13. Existing Stage 17.1-17.9 behavior remains intact.
# ---------------------------------------------------------------------------


class TestPriorStagesRemainIntact:
    def test_speaking_listening_idle_animation_fragments_all_still_present(self):
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        listening = _listening_branch(mount)
        idle = _idle_branch(mount)
        assert "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;" in speaking
        assert "const headLeanTarget = 0.12 + Math.sin(t*0.9) * 0.015;" in listening
        assert 'S.agents.some(a => a.name !== agentName && a.activity === "speaking")' in idle
        assert "group.rotation.y += attentionYaw;" in idle

    def test_intent_is_still_referenced_only_in_the_speaking_branch(self):
        mount = _mount_body()
        for block in (_listening_branch(mount), _idle_branch(mount)):
            assert "intentEffect" not in block
            assert "intentGesture" not in block

    def test_lip_sync_still_honestly_not_implemented(self):
        for rel in ("src/services/identity/presence.py", "src/channels/channel.py"):
            lowered = (REPO_ROOT / rel).read_text("utf-8").lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
                assert word not in lowered

    def test_evolution_extensions_upgrade_files_unchanged_by_this_phase(self):
        for rel in (
            "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            text = path.read_text("utf-8")
            for forbidden in ("presentation_intent_for_turn", "PRESENTATION_INTENT_EFFECTS", "intentEffect"):
                assert forbidden not in text, (rel, forbidden)

    def test_no_new_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text
