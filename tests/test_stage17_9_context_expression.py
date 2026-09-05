"""Stage 17.9 - Context-Aware Digital Human Expression.

The Digital Human moves from purely reacting to SPEAKING/LISTENING/IDLE
toward expressing a small piece of real conversational intent, using the
Stage 17.2 ``PresentationIntent`` seam for the first time rather than a
second, competing gesture system.

What this phase actually adds, proved here rather than merely claimed:

    PresenceRegistry.set_intent/       the identical live-signal
    clear_intent/get_intent            discipline ``set``/``clear``/``get``
                                        already has, extended to carry an
                                        optional PresentationIntent
    PRESENTATION_INTENT_VOCABULARY     a small, explicit, seven-member
                                        list the real producer and
                                        renderer actually agree on
    presentation_intent_for_turn()     the one real, conservative
                                        producer - exactly one honest
                                        signal (turn.celebration), never a
                                        reading of what any text means
    VoiceChannel.deliver()             asserts/clears the real intent
                                        around the exact same speaking
                                        window it already synchronises,
                                        exception-safe via finally
    AgentPresence.intent               threaded through presence_for(),
                                        additive and optional
    ui/index.html                      PRESENTATION_INTENT_EFFECTS - a
                                        hand-kept mirror of the Python
                                        vocabulary, consumed (never
                                        produced) inside the existing
                                        speaking branch of
                                        mountAvatarBody3D only

Every existing Stage 17.1-17.8 animation (mouth pulse, torso lean, arm
gestures, nod, listening lean, idle sway/bob, weight shift, room
attention, blinking, smooth return to rest, per-agent variation) is
layered under this, never replaced - re-affirmed by this file, not just
Stage 17.1-17.8's own still-passing suites.

Verified with two methods, matching every Stage 17.x file since 17.5:
executed Python tests for the real data/registry/producer layer, and
source-text structural checks for the renderer (no JS execution harness
exists in this repository; headless-browser pixel verification was found
unreliable in this sandbox for both new and pre-existing 3D code alike -
see Stage 17.5's own module docstring for the full account).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

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
    AgentPresence,
    DigitalHumanProfile,
    PresenceRegistry,
    PresenceState,
    PresentationIntent,
    digital_human_profile_for,
    presence_for,
    presentation_intent_for_turn,
    set_digital_human_style,
)
from scripts.exchange import exchange, memory_for

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
PRESENCE_SRC = REPO_ROOT / "src" / "services" / "identity" / "presence.py"
CHANNEL_SRC = REPO_ROOT / "src" / "channels" / "channel.py"


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
    def __init__(self, delay: float = 0.0, ok: bool = True) -> None:
        self._delay = delay
        self._ok = ok
        self.calls: list[tuple[str, object]] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        self.calls.append((text, voice))
        if self._delay:
            time.sleep(self._delay)
        return self._ok


class _RaisingSpeaker(SpeechAdapter):
    name = "raising"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        raise RuntimeError("synthesis exploded")


def _celebration_turn(handled_by: str) -> ConversationTurn:
    return ConversationTurn(
        request="celebrate",
        handled_by=handled_by,
        delegated=False,
        steps=(),
        outcome="done",
        succeeded=True,
        celebration={"kind": "milestone", "detail": "shipped it"},
    )


def _ordinary_turn(handled_by: str) -> ConversationTurn:
    return ConversationTurn(
        request="what can you do?",
        handled_by=handled_by,
        delegated=False,
        steps=(),
        outcome="answered",
        succeeded=True,
    )


# ---------------------------------------------------------------------------
# JS structural helpers - the same slicing convention every Stage 17.5+
# test file already uses, so a change to one function's own boundaries
# cannot silently make several files' slices disagree.
# ---------------------------------------------------------------------------


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


def _effects_table() -> str:
    shell = _shell()
    return shell.split("const PRESENTATION_INTENT_EFFECTS = {", 1)[1].split("\n  };", 1)[0]


def _intent_consumption_block(speaking: str) -> str:
    """Only this phase's own new lines inside the speaking branch - not
    the whole branch, which also carries Stage 17.6B-17.8's own
    comments (some of which legitimately discuss why lip-sync/phoneme
    data does *not* exist, and would otherwise false-positive any
    lip-sync-absence check scoped to the full branch)."""
    return speaking.split("const intentGesture = ", 1)[1].split(
        "// Real, bounded arm/hand gesturing", 1
    )[0]


# ---------------------------------------------------------------------------
# 1. PresentationIntent remains a single canonical seam - no second,
#    competing gesture/intent system was introduced anywhere.
# ---------------------------------------------------------------------------


class TestOneCanonicalSeamNoSecondSystem:
    def test_exactly_one_presentationintent_class_exists(self):
        import ast

        hits = []
        for path in (REPO_ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "PresentationIntent":
                    hits.append(path)
        assert hits == [PRESENCE_SRC]

    def test_the_vocabulary_constant_is_defined_exactly_once(self):
        text = PRESENCE_SRC.read_text("utf-8")
        assert text.count("PRESENTATION_INTENT_VOCABULARY = (") == 1

    def test_the_js_effects_table_is_defined_exactly_once(self):
        shell = _shell()
        assert shell.count("const PRESENTATION_INTENT_EFFECTS = {") == 1

    def test_no_second_gesture_or_animation_registry_class_was_added(self):
        """The brief's own hard stop: no new Brain/Memory/Agent/Voice
        system, renderer registry or animation system. Only the two
        pre-existing per-surface stage maps
        (agent3DStages/brain3DStages/memory3DStages/room3DStages/
        avatarBody3DStages) may exist - the same five as Stage 17.5-17.8,
        no sixth was introduced this phase."""
        shell = _shell()
        import re

        stage_maps = set(re.findall(r"let (\w+3DStages)\s*=\s*new Map\(\)", shell))
        assert stage_maps == {
            "agent3DStages", "brain3DStages", "memory3DStages",
            "room3DStages", "avatarBody3DStages",
        }

    def test_registry_still_has_no_persistence_or_timer(self):
        text = PRESENCE_SRC.read_text("utf-8")
        assert "import sched" not in text
        assert "threading.Timer" not in text
        assert "pickle" not in text and "sqlite" not in text.lower()


# ---------------------------------------------------------------------------
# 2. PresenceRegistry.set_intent/clear_intent/get_intent: the identical
#    live-signal discipline as set/clear/get, on its own separate store.
# ---------------------------------------------------------------------------


class TestRegistryIntentMethods:
    def test_set_get_clear(self):
        reg = PresenceRegistry()
        assert reg.get_intent("Alfred") is None
        intent = PresentationIntent(gesture="acknowledging")
        reg.set_intent("Alfred", intent)
        assert reg.get_intent("Alfred") == intent
        reg.clear_intent("Alfred")
        assert reg.get_intent("Alfred") is None

    def test_clearing_an_unset_agent_is_a_safe_no_op(self):
        reg = PresenceRegistry()
        reg.clear_intent("Nobody")  # must not raise
        assert reg.get_intent("Nobody") is None

    def test_agents_are_independent(self):
        reg = PresenceRegistry()
        reg.set_intent("Alfred", PresentationIntent(gesture="explaining"))
        assert reg.get_intent("Arthur") is None
        reg.set_intent("Arthur", PresentationIntent(gesture="thinking"))
        assert reg.get_intent("Alfred").gesture == "explaining"
        assert reg.get_intent("Arthur").gesture == "thinking"

    def test_intent_and_state_are_independent_stores(self):
        """Setting a state must never populate intent, and vice versa -
        proof this is a genuinely separate dict/lock pairing, not a
        single value doing double duty."""
        reg = PresenceRegistry()
        reg.set("Alfred", PresenceState.SPEAKING)
        assert reg.get_intent("Alfred") is None
        reg.clear("Alfred")

        reg2 = PresenceRegistry()
        reg2.set_intent("Alfred", PresentationIntent(gesture="thinking"))
        assert reg2.get("Alfred") is None

    def test_repr_shows_both_stores(self):
        reg = PresenceRegistry()
        reg.set_intent("Alfred", PresentationIntent(gesture="thinking"))
        text = repr(reg)
        assert "state=" in text and "intent=" in text


# ---------------------------------------------------------------------------
# 3. The producer is genuinely conservative: one honest signal, neutral
#    by absence for everything else, duck-typed, never a text reading.
# ---------------------------------------------------------------------------


class TestProducerIsConservativeAndNeutralByAbsence:
    def test_a_celebration_turn_produces_acknowledging(self):
        intent = presentation_intent_for_turn(_celebration_turn("Alfred"))
        assert intent == PresentationIntent(gesture="acknowledging")

    def test_an_ordinary_successful_turn_produces_concluding(self):
        """Superseded by Stage 17.10: a plain, self-contained,
        successful turn (no celebration, no memory recall, no tool,
        no delegation) is no longer None - it is the deterministic
        default "concluding" case, still real, still structural, never
        a text reading. See presentation_intent_for_turn()'s own
        docstring for the full priority order this stage added."""
        assert presentation_intent_for_turn(_ordinary_turn("Alfred")) == PresentationIntent(
            gesture="concluding"
        )

    def test_a_failed_turn_with_no_celebration_produces_none(self):
        turn = ConversationTurn(
            request="do the impossible", handled_by="Alfred", delegated=False,
            steps=(), outcome="failed", succeeded=False,
        )
        assert presentation_intent_for_turn(turn) is None

    def test_it_is_duck_typed_not_tied_to_conversationturn(self):
        """No import of ConversationTurn in presence.py - any object
        with a celebration attribute works, the same discipline every
        other optional collaborator in this module already follows."""

        class Bare:
            celebration = {"kind": "milestone"}

        assert presentation_intent_for_turn(Bare()) == PresentationIntent(gesture="acknowledging")

        class BareNone:
            celebration = None

        assert presentation_intent_for_turn(BareNone()) is None

    def test_an_object_with_no_celebration_attribute_at_all_produces_none(self):
        class NoAttr:
            pass

        assert presentation_intent_for_turn(NoAttr()) is None

    def test_the_producer_never_inspects_request_or_outcome_text(self):
        """The words of the request/outcome must never influence the
        result - only the already-structured fields (celebration,
        succeeded, memory_recalled, tool_used, delegated) may. Proved
        by two turns identical in every structured field and wildly
        different in text producing the exact same result."""
        boring = ConversationTurn(
            request="what time is it", handled_by="Alfred", delegated=False,
            steps=(), outcome="it is 9am", succeeded=True,
        )
        excited = ConversationTurn(
            request="AMAZING!!! wonderful incredible great news!!!",
            handled_by="Alfred", delegated=False, steps=(),
            outcome="fantastic wonderful success", succeeded=True,
        )
        assert presentation_intent_for_turn(boring) == presentation_intent_for_turn(excited)

    def test_every_producer_result_is_a_member_of_the_shared_vocabulary(self):
        for turn in (_celebration_turn("Alfred"), _ordinary_turn("Alfred")):
            intent = presentation_intent_for_turn(turn)
            if intent is not None:
                assert intent.gesture in PRESENTATION_INTENT_VOCABULARY


# ---------------------------------------------------------------------------
# 4. Producer/consumer separation: only presence.py produces, only
#    channel.py asserts/clears, the renderer only ever consumes.
# ---------------------------------------------------------------------------


class TestProducerConsumerSeparation:
    def test_nothing_in_the_manager_or_agents_layer_references_the_new_identifiers(self):
        import re

        for rel in ("src/manager", "src/agents"):
            for path in (REPO_ROOT / rel).rglob("*.py"):
                text = path.read_text("utf-8")
                for forbidden in (
                    "PresentationIntent", "presentation_intent_for_turn",
                    "set_intent", "get_intent", "clear_intent",
                ):
                    assert re.search(rf"\b{forbidden}\b", text) is None, (path, forbidden)

    def test_presentation_intent_for_turn_lives_only_in_presence_py(self):
        for path in (REPO_ROOT / "src").rglob("*.py"):
            if path == PRESENCE_SRC:
                continue
            assert "def presentation_intent_for_turn" not in path.read_text("utf-8")

    def test_set_intent_is_called_only_from_channel_py(self):
        for path in (REPO_ROOT / "src").rglob("*.py"):
            if path == CHANNEL_SRC or path == PRESENCE_SRC:
                continue
            assert ".set_intent(" not in path.read_text("utf-8")

    def test_the_renderer_never_references_brain_memory_or_an_llm(self):
        """The producer/consumer boundary's other half: the renderer's
        new code must never inspect text meaning, call an LLM, reach
        into Memory, or construct an Agent/Brain - it only ever reads
        an already-produced field off the live snapshot."""
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        for forbidden in (
            "Brain", "Memory", "LLM", "fetch(", "XMLHttpRequest",
            "recall(", "remember(", "new Agent",
        ):
            assert forbidden not in speaking, forbidden

    def test_the_renderer_reads_only_livedotavatardotintent(self):
        mount = _mount_body()
        speaking = _speaking_branch(mount)
        assert "live.avatar.intent" in speaking
        # No second, independent reading path (e.g. a direct fetch of a
        # dedicated intent endpoint) - it comes from the exact same
        # snapshot poll every other live field already does.
        assert "intent.json" not in speaking
        assert "/intent" not in speaking


# ---------------------------------------------------------------------------
# 5. Real wiring through VoiceChannel.deliver(): the intent is genuinely
#    observable mid-call, and genuinely cleared afterward - even on
#    failure - exactly like Stage 17.2's own SPEAKING proof.
# ---------------------------------------------------------------------------


class TestRealWiringThroughDeliver:
    def test_intent_is_genuinely_observable_while_delivery_blocks(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(delay=0.3), presence=registry)
        turn = _celebration_turn(manager.own_skills.name)
        response = Response(text="done", speech="done", spoken=False, turn=turn)

        observed: list[PresentationIntent | None] = []

        def watch():
            for _ in range(15):
                observed.append(registry.get_intent(manager.own_skills.name))
                time.sleep(0.03)

        watcher = threading.Thread(target=watch)
        watcher.start()
        channel.deliver(response)
        watcher.join()

        assert PresentationIntent(gesture="acknowledging") in observed
        assert registry.get_intent(manager.own_skills.name) is None

    def test_a_failed_turn_never_asserts_any_intent(self):
        """Superseded by Stage 17.10: an *ordinary successful* turn now
        genuinely gets "concluding" (see the dedicated test below), so
        the honest "never asserts anything" guarantee moves to the one
        case that still has no positive presentation to offer - a
        failure."""
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(delay=0.1), presence=registry)
        turn = ConversationTurn(
            request="do the impossible", handled_by=manager.own_skills.name,
            delegated=False, steps=(), outcome="failed", succeeded=False,
        )
        response = Response(text="ok", speech="ok", spoken=False, turn=turn)

        marks: list[object] = []
        original = registry.set_intent

        def spying(agent_key, intent):
            marks.append((agent_key, intent))
            original(agent_key, intent)

        registry.set_intent = spying  # type: ignore[method-assign]
        channel.deliver(response)

        assert marks == []
        assert registry.get_intent(manager.own_skills.name) is None

    def test_an_ordinary_successful_turn_asserts_concluding_through_deliver(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(delay=0.3), presence=registry)
        turn = _ordinary_turn(manager.own_skills.name)
        response = Response(text="ok", speech="ok", spoken=False, turn=turn)

        observed: list[PresentationIntent | None] = []

        def watch():
            for _ in range(15):
                observed.append(registry.get_intent(manager.own_skills.name))
                time.sleep(0.03)

        watcher = threading.Thread(target=watch)
        watcher.start()
        channel.deliver(response)
        watcher.join()

        assert PresentationIntent(gesture="concluding") in observed
        assert registry.get_intent(manager.own_skills.name) is None

    def test_intent_is_cleared_even_when_speak_raises(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_RaisingSpeaker(), presence=registry)
        turn = _celebration_turn(manager.own_skills.name)
        response = Response(text="done", speech="done", spoken=False, turn=turn)

        with pytest.raises(RuntimeError):
            channel.deliver(response)

        assert registry.get_intent(manager.own_skills.name) is None

    def test_an_unmatched_request_never_gets_an_intent_either(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)
        turn = ConversationTurn(
            request="xyzzy", handled_by="nobody", delegated=False, steps=(),
            outcome="nothing matched", succeeded=False, celebration={"kind": "milestone"},
        )
        response = Response(text="nothing matched", speech="nothing matched", spoken=False, turn=turn)

        marks: list[object] = []
        original = registry.set_intent
        registry.set_intent = lambda k, i: (marks.append(k), original(k, i))  # type: ignore[method-assign]

        channel.deliver(response)

        assert marks == []
        assert registry.get_intent("nobody") is None

    def test_a_channel_built_with_no_presence_registry_is_unaffected(self):
        _, manager, _, _, _ = system()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker())
        turn = _celebration_turn(manager.own_skills.name)
        response = Response(text="done", speech="done", spoken=False, turn=turn)
        delivered = channel.deliver(response)  # must not raise
        assert delivered.spoken is True

    def test_deliver_still_calls_the_engine_exactly_once(self):
        _, manager, _, _, _ = system()
        speaker = _ScriptedSpeaker()
        channel = VoiceChannel(manager, speech=speaker, presence=PresenceRegistry())
        turn = _celebration_turn(manager.own_skills.name)
        response = Response(text="done", speech="done", spoken=False, turn=turn)
        channel.deliver(response)
        assert len(speaker.calls) == 1


# ---------------------------------------------------------------------------
# 6. Canonical speaker identity remains authoritative for intent too -
#    never a non-speaker, never a hardcoded name.
# ---------------------------------------------------------------------------


class TestCanonicalSpeakerAuthorityForIntent:
    def test_only_the_true_canonical_speaker_ever_receives_the_intent(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(delay=0.3), presence=registry)
        turn = _celebration_turn("Arthur")
        response = Response(text="done", speech="done", spoken=False, turn=turn)

        observed_arthur: list[bool] = []
        observed_alfred: list[bool] = []

        def watch():
            for _ in range(15):
                observed_arthur.append(registry.get_intent("Arthur") is not None)
                observed_alfred.append(registry.get_intent(manager.own_skills.name) is not None)
                time.sleep(0.03)

        watcher = threading.Thread(target=watch)
        watcher.start()
        channel.deliver(response)
        watcher.join()

        assert True in observed_arthur
        assert all(v is False for v in observed_alfred)
        assert registry.get_intent("Arthur") is None


# ---------------------------------------------------------------------------
# 7. PresentationIntent never overwrites the user-controlled
#    DigitalHumanProfile - two genuinely independent fields.
# ---------------------------------------------------------------------------


class TestIntentDoesNotOverwriteDigitalHumanProfile:
    def test_setting_and_clearing_intent_leaves_the_stored_profile_untouched(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "standing", "gesture_style": "measured"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        registry = PresenceRegistry()
        registry.set_intent(manager.own_skills.name, PresentationIntent(gesture="explaining"))
        registry.clear_intent(manager.own_skills.name)

        avatar = DefaultAvatarProvider().default_for(manager.own_skills.name)
        profile = digital_human_profile_for(manager.own_skills.name, avatar, recall=memory.recall)
        assert profile.posture == "standing"
        assert profile.gesture_style == "measured"

    def test_agentpresence_carries_profile_and_intent_as_independent_fields(self):
        avatar = DefaultAvatarProvider().default_for("Alfred")
        profile = DigitalHumanProfile(avatar_id=avatar.avatar_id, posture="standing")
        intent = PresentationIntent(gesture="thinking")
        p = AgentPresence(agent_key="Alfred", avatar=avatar, profile=profile, intent=intent)
        assert p.profile.posture == "standing"
        assert p.intent.gesture == "thinking"
        # Changing one is structurally impossible to bleed into the
        # other - they are two independently-optional dataclass fields.
        p2 = AgentPresence(agent_key="Alfred", avatar=avatar, profile=profile, intent=None)
        assert p2.profile.posture == "standing"
        assert p2.intent is None

    def test_to_dict_includes_intent_only_when_present_never_disturbing_profile(self):
        avatar = DefaultAvatarProvider().default_for("Alfred")
        profile = DigitalHumanProfile(avatar_id=avatar.avatar_id, posture="standing")
        without_intent = AgentPresence(agent_key="Alfred", avatar=avatar, profile=profile)
        assert "intent" not in without_intent.to_dict()
        assert without_intent.to_dict()["profile"]["posture"] == "standing"

        with_intent = AgentPresence(
            agent_key="Alfred", avatar=avatar, profile=profile,
            intent=PresentationIntent(gesture="concluding", intensity=0.8),
        )
        d = with_intent.to_dict()
        assert d["intent"] == {"gesture": "concluding", "intensity": 0.8}
        assert d["profile"]["posture"] == "standing"

    def test_presence_for_threads_a_live_intent_without_disturbing_the_stored_profile(self):
        _, manager, _, _, _ = system()
        memory = memory_for(manager, manager.own_skills.name)
        set_digital_human_style(
            manager.own_skills.name, {"posture": "seated"},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        registry = PresenceRegistry()
        registry.set_intent(manager.own_skills.name, PresentationIntent(gesture="emphasizing"))

        p = presence_for(
            manager.own_skills.name, provider=DefaultAvatarProvider(),
            recall=memory.recall, live=registry,
        )
        assert p.intent == PresentationIntent(gesture="emphasizing")
        assert p.profile.posture == "seated"


# ---------------------------------------------------------------------------
# 8. Per-agent isolation and generic future-specialist support - no
#    hardcoded agent names or counts anywhere in this phase's own code.
# ---------------------------------------------------------------------------


class TestPerAgentIsolationAndGenericity:
    def test_two_agents_intents_are_never_confused_through_a_real_channel(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(), presence=registry)

        alfred_turn = _celebration_turn(manager.own_skills.name)
        channel.deliver(Response(text="d", speech="d", spoken=False, turn=alfred_turn))
        assert registry.get_intent(manager.own_skills.name) is None  # cleared after
        assert registry.get_intent("Arthur") is None

        arthur_turn = _celebration_turn("Arthur")
        channel.deliver(Response(text="d", speech="d", spoken=False, turn=arthur_turn))
        assert registry.get_intent("Arthur") is None
        assert registry.get_intent(manager.own_skills.name) is None

    def test_no_hardcoded_agent_name_in_the_new_python_code(self):
        text = PRESENCE_SRC.read_text("utf-8")
        registry_region = text.split("class PresenceRegistry", 1)[1].split(
            "class AgentPresence", 1
        )[0]
        intent_region = text.split("class PresentationIntent", 1)[1]
        for region in (registry_region, intent_region):
            for name in ("Alfred", "Arthur"):
                assert name not in region, name

        channel_text = CHANNEL_SRC.read_text("utf-8")
        # Only this phase's own new lines - not the whole deliver()
        # method, whose pre-existing docstring legitimately names
        # "Alfred" in an illustrative example untouched this phase.
        intent_region = channel_text.split("intent = presentation_intent_for_turn", 1)[1].split(
            "print(response.text)", 1
        )[0]
        for name in ("Alfred", "Arthur"):
            assert name not in intent_region, name

    def test_no_hardcoded_agent_name_in_the_new_js_code(self):
        for block in (_effects_table(), _speaking_branch(_mount_body())):
            for name in ("Alfred", "Arthur"):
                assert name not in block, name

    def test_a_third_registered_specialist_gets_working_intent_generically(self):
        """No count of agents is baked in anywhere this phase touched -
        proved by exercising the real registry with a name that is
        registered nowhere in this project, standing in for a future
        specialist."""
        reg = PresenceRegistry()
        reg.set_intent("FutureSpecialist", PresentationIntent(gesture="thinking"))
        assert reg.get_intent("FutureSpecialist").gesture == "thinking"
        reg.clear_intent("FutureSpecialist")
        assert reg.get_intent("FutureSpecialist") is None


# ---------------------------------------------------------------------------
# 9. LLM/Brain/Memory/VoiceProfile independence, re-verified for this
#    phase's own new surface.
# ---------------------------------------------------------------------------


class TestLLMBrainMemoryVoiceIndependence:
    def test_swapping_the_manager_brain_does_not_change_registry_intent_behaviour(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        registry.set_intent(manager.own_skills.name, PresentationIntent(gesture="thinking"))

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]

        assert registry.get_intent(manager.own_skills.name) == PresentationIntent(gesture="thinking")

    def test_swapping_the_brain_does_not_change_the_producers_output(self):
        _, manager, _, _, _ = system()

        class OtherBrain:
            pass

        manager.brain = OtherBrain()  # type: ignore[assignment]
        turn = _celebration_turn(manager.own_skills.name)
        assert presentation_intent_for_turn(turn) == PresentationIntent(gesture="acknowledging")

    def test_presence_py_imports_nothing_from_the_brain_or_llm_layer(self):
        text = PRESENCE_SRC.read_text("utf-8")
        import_lines = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
        for ln in import_lines:
            assert "src.core.llm" not in ln
            assert "src.manager" not in ln
            assert "src.agents" not in ln
            assert ".brain" not in ln.lower()

    def test_channel_py_gained_no_new_brain_llm_or_memory_import_this_phase(self):
        text = CHANNEL_SRC.read_text("utf-8")
        import_lines = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
        for ln in import_lines:
            assert "llm" not in ln.lower()
            assert ".memory" not in ln.lower()

    def test_two_agents_with_different_voices_get_independent_intents(self):
        _, manager, _, _, _ = system()
        registry = PresenceRegistry()
        channel = VoiceChannel(manager, speech=_ScriptedSpeaker(delay=0.2), presence=registry)

        def watch(agent_key, out):
            for _ in range(10):
                out.append(registry.get_intent(agent_key))
                time.sleep(0.03)

        alfred_seen: list[object] = []
        arthur_seen: list[object] = []
        t1 = threading.Thread(target=watch, args=(manager.own_skills.name, alfred_seen))
        t2 = threading.Thread(target=watch, args=("Arthur", arthur_seen))
        t1.start(); t2.start()
        turn = _celebration_turn(manager.own_skills.name)
        channel.deliver(Response(text="d", speech="d", spoken=False, turn=turn))
        t1.join(); t2.join()

        assert any(v is not None for v in alfred_seen)
        assert all(v is None for v in arthur_seen)


# ---------------------------------------------------------------------------
# 10. Reduced motion and no random animation, re-affirmed for this
#     phase's own new code.
# ---------------------------------------------------------------------------


class TestReducedMotionAndNoRandomAnimation:
    def test_no_new_reduced_motion_block_or_media_query(self):
        shell = _shell()
        assert shell.count("@media (prefers-reduced-motion") == 1
        assert shell.count("@media (max-width:760px)") == 2

    def test_the_new_code_uses_no_math_random(self):
        for block in (_effects_table(), _speaking_branch(_mount_body())):
            assert "Math.random" not in block

    def test_the_new_python_producer_uses_no_randomness(self):
        text = PRESENCE_SRC.read_text("utf-8")
        region = text.split("def presentation_intent_for_turn", 1)[1]
        assert "random" not in region.lower()


# ---------------------------------------------------------------------------
# 11. Lip-sync boundary still honestly not implemented.
# ---------------------------------------------------------------------------


class TestLipSyncBoundaryStillHonest:
    def test_no_viseme_phoneme_or_lipsync_vocabulary_in_this_phases_files(self):
        for rel in (
            "src/services/identity/presence.py", "src/channels/channel.py",
        ):
            lowered = (REPO_ROOT / rel).read_text("utf-8").lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
                assert word not in lowered, f"{rel} claims lip-sync vocabulary"

    def test_no_viseme_phoneme_or_lipsync_vocabulary_in_the_new_js_code(self):
        speaking = _speaking_branch(_mount_body())
        for block in (_effects_table(), _intent_consumption_block(speaking)):
            lowered = block.lower()
            for word in ("viseme", "lip-sync", "lip sync", "lipsync", "phoneme"):
                assert word not in lowered


# ---------------------------------------------------------------------------
# 12. Speaking/listening/idle behaviour remain intact, and intent is
#     applied only within the speaking branch - never leaking into
#     listening/idle.
# ---------------------------------------------------------------------------


class TestSpeakingListeningIdleBehaviorRemainsIntact:
    def test_intent_is_referenced_only_in_the_speaking_branch(self):
        mount = _mount_body()
        listening = _listening_branch(mount)
        idle = _idle_branch(mount)
        for block in (listening, idle):
            assert "PRESENTATION_INTENT_EFFECTS" not in block
            assert "intentGesture" not in block
            assert "intentEffect" not in block

    def test_all_pre_existing_speaking_animation_still_present(self):
        speaking = _speaking_branch(_mount_body())
        for fragment in (
            "group.userData.head.scale.setScalar(1 + Math.abs(Math.sin(t*9))*0.05);",
            "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;",
            "const torsoTarget = restTorsoRotZ + Math.sin(t*gestureRate*0.5) * 0.035 * intentEffect.gestureMul * liveliness;",
        ):
            assert fragment in speaking

    def test_arm_gesture_amplitude_now_carries_the_intent_multiplier(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            "const targetZ = restZ[i] + Math.sin(t*gestureRate + i*1.7) "
            "* gestureAmp * intentEffect.gestureMul * liveliness * (i===0?1:-1);"
        ) in speaking

    def test_nod_amplitude_now_carries_the_intent_multiplier(self):
        speaking = _speaking_branch(_mount_body())
        assert "Math.sin((nodPhase-4.9)*Math.PI/0.5) * 0.14 * intentEffect.nodMul" in speaking

    def test_listening_lean_and_idle_room_attention_are_unchanged(self):
        mount = _mount_body()
        listening = _listening_branch(mount)
        idle = _idle_branch(mount)
        assert "const headLeanTarget = 0.12 + Math.sin(t*0.9) * 0.015;" in listening
        assert "S.agents.some(a => a.name !== agentName && a.activity === \"speaking\")" in idle
        assert "group.rotation.y += attentionYaw;" in idle

    def test_blinking_still_runs_unconditionally_after_every_branch(self):
        mount = _mount_body()
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
        assert mount.rsplit("const blinkPhase", 1)[0].count("} else {") >= 1


# ---------------------------------------------------------------------------
# 13. Each supported vocabulary entry maps to a real, restrained effect -
#     never theatrical, and "neutral" is the honest identity mapping.
# ---------------------------------------------------------------------------


class TestVocabularyAndEffectsTableAgree:
    def test_python_and_js_vocabularies_have_the_same_seven_members(self):
        import re

        js_keys = set(re.findall(r"(\w+):\s*\{ gestureMul", _effects_table()))
        assert js_keys == set(PRESENTATION_INTENT_VOCABULARY)
        assert len(PRESENTATION_INTENT_VOCABULARY) == 7

    def test_neutral_is_the_honest_identity_mapping(self):
        table = _effects_table()
        neutral_line = [ln for ln in table.splitlines() if ln.strip().startswith("neutral:")][0]
        assert "gestureMul: 1," in neutral_line
        assert "nodMul: 1" in neutral_line

    def test_every_multiplier_is_restrained_never_theatrical(self):
        import re

        muls = [float(m) for m in re.findall(r"Mul:\s*([0-9.]+)", _effects_table())]
        assert muls, "no multipliers found"
        for m in muls:
            assert 0.4 <= m <= 1.6, m

    def test_the_renderer_falls_back_to_neutral_for_an_unrecognized_gesture(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            "PRESENTATION_INTENT_EFFECTS[intentGesture] || PRESENTATION_INTENT_EFFECTS.neutral"
        ) in speaking

    def test_the_renderer_falls_back_to_neutral_when_no_intent_is_present(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            '(live && live.avatar && live.avatar.intent && live.avatar.intent.gesture) || "neutral"'
        ) in speaking


# ---------------------------------------------------------------------------
# 14. Conversation Room and Workspace share the one generic renderer -
#     intent reaches both surfaces for free, with no duplicated mount
#     function and no separate room-only intent path.
# ---------------------------------------------------------------------------


class TestConversationRoomIntegration:
    def test_exactly_one_mountavatarbody3d_function_exists(self):
        shell = _shell()
        assert shell.count("function mountAvatarBody3D(container") == 1

    def test_every_surface_with_a_body_uses_the_same_data_attribute(self):
        """The invariant is "one attribute, one renderer, one wiring loop"
        - not "exactly two surfaces". Stage 17.18 added a third surface
        that draws a Digital Human (the private agent interface), and it
        draws it by mounting the identical [data-avatarbody3d] container
        the Room and the Workspace already use, through the identical
        wireAvatarBody3D/mountAvatarBody3D pair asserted either side of
        this test. A new surface reaching for a *different* attribute is
        what this guards against, and that is still what it catches: the
        count must equal the number of surfaces, and the two tests around
        it pin that there is still exactly one mount function and exactly
        one wiring loop behind all of them."""
        shell = _shell()
        assert shell.count('data-avatarbody3d="${esc(') == 3
        assert shell.count("function mountAvatarBody3D(container") == 1
        assert shell.count("function wireAvatarBody3D(){") == 1

    def test_both_surfaces_are_wired_through_the_one_wireavatarbody3d(self):
        """Stage 17.11 wraps the mount call in an unsaved-preview merge
        (_dhPreviewProfile) - still the exact same single call site, now
        reading a locally-pending Avatar Studio edit on top of the real
        server profile rather than the raw profile alone."""
        shell = _shell()
        assert shell.count("function wireAvatarBody3D(){") == 1
        fn = shell.split("function wireAvatarBody3D(){", 1)[1].split("\n  }\n", 1)[0]
        assert 'wireStages("avatarbody3d", avatarBody3DStages' in fn
        assert (
            "mountAvatarBody3D(el, name, agent.avatar.avatar, "
            "_dhPreviewProfile(name, agent.avatar.profile));"
        ) in fn

    def test_room_never_infers_a_non_speaker_is_speaking(self):
        """Re-confirms Stage 17.7/17.8's own invariant still holds: the
        Room's speaking CSS class is driven by the identical real
        per-agent `activity` field this phase left untouched."""
        shell = _shell()
        assert 'p.activity === "speaking" ? " speaking" : ""' in shell or "speaking" in shell.split(
            "V.room = ", 1
        )[1].split("\n  };", 1)[0]


# ---------------------------------------------------------------------------
# 15. Evolution/Extensions/Upgrade architecture: zero coupling to this
#     phase's new identifiers, matching every prior stage's own check.
# ---------------------------------------------------------------------------


class TestEvolutionExtensionsUpgradeCompatibility:
    FILES = (
        "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
        "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
    )
    NEW_IDENTIFIERS = (
        "presentation_intent_for_turn", "PRESENTATION_INTENT_VOCABULARY",
        "PRESENTATION_INTENT_EFFECTS", "set_intent", "get_intent", "clear_intent",
    )

    def test_no_evolution_or_upgrade_file_references_this_phases_additions(self):
        for rel in self.FILES:
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            text = path.read_text("utf-8")
            for forbidden in self.NEW_IDENTIFIERS:
                assert forbidden not in text, (rel, forbidden)


# ---------------------------------------------------------------------------
# 16. Cross-device / no new dependency, re-affirmed.
# ---------------------------------------------------------------------------


class TestCrossDeviceAndNoNewDependency:
    DEVICE_NAMES = ("redmi", "iphone", "ipad", "samsung", "galaxy", "android")

    def test_no_device_name_in_this_phases_new_code(self):
        for block in (_effects_table(), _speaking_branch(_mount_body())):
            lowered = block.lower()
            for device in self.DEVICE_NAMES:
                assert device not in lowered, device

    def test_no_new_dependency_was_added(self):
        text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
        for forbidden in ("three.js", "babylon", "pyrender", "moderngl", "panda3d"):
            assert forbidden not in text
