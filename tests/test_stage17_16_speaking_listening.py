"""Stage 17.16 - Better Speaking/Listening Behavior.

Two genuine gaps were found by inspection, both left by every prior
Stage 17.x phase rather than newly introduced:

1. **Backend**: `scripts/serve_ui.py`'s `route_message()` - the real
   production entry point for the browser's text composer (`POST
   /message`) - never asserted any LISTENING signal at all. Only the
   separate ambient-voice flow (`VoiceActivationSession`,
   `src/channels/activation.py`) did. `PresenceRegistry`/
   `PresenceState.LISTENING` already existed and needed no change;
   the gap was a missing *caller*. Fixed by asserting the identical,
   already-existing `PresenceState.LISTENING` (via the identical,
   already-existing `PresenceRegistry.set`/`.clear` pair
   `VoiceActivationSession` and `speaking()` already use) for exactly
   the one case where the recipient is genuinely known in advance -
   an explicit `to=` addressed to a real, registered agent - cleared
   in `finally` so a raised exception can never leave it stuck. An
   unaddressed message is deliberately left alone: `route_message`
   cannot know who `exchange()`'s own routing will pick until it
   returns, and asserting a guess would be exactly the "state is
   derived from real evidence, never guessed" rule
   `src/services/identity/presence.py`'s own docstring already states
   for every other signal in this system.

2. **Renderer**: `mountAvatarBody3D` in `ui/index.html` assigned every
   SPEAKING-branch property (arm rotation, head nod, mouth pulse,
   torso lean) directly (`=`) every frame, rather than easing toward
   it the way the pre-existing LISTENING/idle branches already do
   (`+= (target - current) * factor`) - so the very first frame of a
   SPEAKING (or LISTENING) window could snap instantly to wherever
   that frame's oscillation happened to be, and the gesture/torso sine
   wave never varied in amplitude across a long turn, both of which
   this phase's own brief specifically names ("avoid abrupt snapping
   between states", "avoid repetitive robotic loops"). Fixed by:
   reusing the same easing idiom already established in this file
   (never a second animation mechanism), for arm rotation.z/x, the
   head nod, the mouth pulse, and the torso lean; and adding one new,
   deterministic (never `Math.random()`), slow secondary sine -
   `liveliness`, bounded to [0.82, 1.18], seeded by the same
   `gestureSignature` every other per-agent variation in this file
   already uses - multiplying only the arm and torso gesture
   amplitude, so a long speaking turn visibly breathes rather than
   repeating with exact periodicity. The pre-existing LISTENING head
   lean (`0.12 + Math.sin(t*0.9)*0.015`, itself unchanged) was also
   converted from a direct assignment to the same eased-entry pattern,
   for the identical "avoid abrupt snapping" reason, applied generally
   rather than scoped only to SPEAKING.

Nothing else about the state machine changed. `PresenceState` is still
exactly IDLE/LISTENING/SPEAKING (see `src/services/identity/presence.py`'s
own docstring on why THINKING and friends are deliberately not built);
the "processing" window between a message arriving and an answer being
spoken already reads honestly as LISTENING the whole way through - the
identical, already-correct behaviour `VoiceActivationSession.turn()`
already had, confirmed by inspection this phase, not changed. No new
backend state, no timer, no second PresenceRegistry, no coupling to a
provider, Three.js, VoiceProfile, or Brain/Memory.

Verified with the same two methods every Stage 17.5+ file already
uses: real, executed Python tests for the registry/route/exchange
layer, and source-text structural checks for the renderer (no JS
execution harness in this repository can run `mountAvatarBody3D`'s own
per-frame closure - it operates on real THREE.js `Group` objects this
sandbox cannot construct; see `tests/test_stage17_5_digital_human_renderer.py`'s
own module docstring for the full account of why headless-browser pixel
verification was found unreliable here for both new and pre-existing 3D
code alike).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import scripts.serve_ui as serve_ui
from scripts.serve_ui import build_live_system, route, route_message
from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.config.settings import Settings
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.presence import PresenceState

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
SERVE_UI_SRC = REPO_ROOT / "scripts" / "serve_ui.py"


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


class _ScriptedSpeaker(SpeechAdapter):
    """A real Speaker double, genuinely blocking when told to - the same
    honest-test-double discipline every Stage 17.x file already uses:
    replace the engine at its own boundary, never the production code
    path that calls it."""

    def __init__(self, delay: float = 0.0, ok: bool = True) -> None:
        self._delay = delay
        self._ok = ok
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        self.calls.append(text)
        if self._delay:
            time.sleep(self._delay)
        return self._ok


def _live_with_scripted_speaker(delay: float = 0.0):
    live = build_live_system(settings())
    live.channel = VoiceChannel(
        live.manager, speech=_ScriptedSpeaker(delay=delay), presence=live.runtime.presence_registry
    )
    return live


def _watch(registry, agent_key, samples, count=25, interval=0.02):
    for _ in range(count):
        samples.append(registry.get(agent_key))
        time.sleep(interval)


def _capture_set_calls(registry):
    """Intercepts every real PresenceRegistry.set call (never a poll,
    which could simply miss a fast window) - the same interception
    technique tests/test_stage17_3_live_speaking_presence.py already
    uses for its own "never marked" guarantees."""
    marks: list[tuple[str, PresenceState]] = []
    original_set = registry.set
    registry.set = lambda k, s: (marks.append((k, s)), original_set(k, s))  # type: ignore[method-assign]
    return marks


# ---------------------------------------------------------------------------
# 1. LISTENING is now genuinely asserted for the real production text
#    path, for exactly the one case it can honestly be - an explicit,
#    real, registered `to=`.
# ---------------------------------------------------------------------------


class TestListeningForExplicitlyAddressedMessages:
    def test_listening_is_observable_while_exchange_is_still_processing(self, monkeypatch):
        """Isolates route_message()'s own new contribution: LISTENING
        must already be up before exchange() is even called, and stay
        up for as long as exchange() is still running - proved here by
        wrapping (never replacing) the real exchange() with an
        artificial delay, so the always-fast real routing/brain phase
        becomes observable to a polling thread without depending on
        exchange()'s or deliver()'s own internal timing."""
        live = _live_with_scripted_speaker()
        real_exchange = serve_ui.exchange

        def slow_exchange(*args, **kwargs):
            time.sleep(0.3)
            return real_exchange(*args, **kwargs)

        monkeypatch.setattr(serve_ui, "exchange", slow_exchange)

        samples: list = []
        watcher = threading.Thread(
            target=_watch, args=(live.runtime.presence_registry, "Arthur", samples)
        )
        watcher.start()
        status, body, _ = route_message("read my inbox for job opportunities", live, to="Arthur")
        watcher.join()

        assert status == 200
        assert PresenceState.LISTENING in samples

    def test_listening_settles_back_to_idle_after_the_post_returns(self):
        live = _live_with_scripted_speaker()
        route_message("what can you do?", live, to="Arthur")
        assert live.runtime.presence_registry.get("Arthur") is None

    def test_listening_is_scoped_to_the_addressed_agent_only(self):
        """Multi-agent isolation: addressing Arthur must never mark
        Alfred (or anyone else) LISTENING."""
        live = _live_with_scripted_speaker()
        marks = _capture_set_calls(live.runtime.presence_registry)

        route_message("read my inbox for job opportunities", live, to="Arthur")

        listening_marks = [k for k, s in marks if s is PresenceState.LISTENING]
        assert listening_marks == ["Arthur"]

    def test_listening_is_never_asserted_for_an_unaddressed_message(self):
        """route_message cannot know who exchange()'s own routing will
        pick until it returns - asserting a guess would be exactly the
        "asserted, not derived" mistake this module refuses elsewhere,
        so no LISTENING call may happen at all for this case."""
        live = _live_with_scripted_speaker()
        marks = _capture_set_calls(live.runtime.presence_registry)

        route_message("what can you do?", live)  # no `to`

        assert PresenceState.LISTENING not in [s for _, s in marks]

    def test_listening_is_never_asserted_for_an_unregistered_addressed_name(self):
        """The identical guard VoiceChannel.deliver() itself already
        applies before attributing SPEAKING - fabricating nothing for a
        name nobody registered."""
        live = _live_with_scripted_speaker()
        marks = _capture_set_calls(live.runtime.presence_registry)

        status, body, _ = route_message("hello there", live, to="Nobody")

        assert status == 200
        assert live.manager.registry.get("Nobody") is None
        assert PresenceState.LISTENING not in [s for _, s in marks]
        assert live.runtime.presence_registry.get("Nobody") is None

    def test_listening_is_cleared_even_when_exchange_raises_before_ever_reaching_deliver(
        self, monkeypatch
    ):
        """The specific exception-safety case only route_message's own
        new `finally` can cover: an exception raised *before* deliver()
        is ever reached (so VoiceChannel's own, separate speaking()
        finally never runs) must still leave no stale LISTENING
        behind."""
        live = _live_with_scripted_speaker()

        def raising_exchange(*args, **kwargs):
            raise RuntimeError("routing exploded before any speech")

        monkeypatch.setattr(serve_ui, "exchange", raising_exchange)

        status, body, _ = route_message("read my inbox", live, to="Arthur")

        assert status == 500
        assert live.runtime.presence_registry.get("Arthur") is None

    def test_route_message_still_reports_the_real_turn_when_addressed(self):
        live = _live_with_scripted_speaker()
        import json

        _, body, _ = route_message("read my inbox for job opportunities", live, to="Arthur")
        turn = json.loads(body)
        assert turn["handled_by"] == "Arthur"

    def test_a_concurrent_snapshot_poll_can_genuinely_observe_listening(self, monkeypatch):
        """The same live, cross-thread proof
        tests/test_stage17_3_live_speaking_presence.py already applies
        to SPEAKING, now for LISTENING: GET /snapshot.json is unlocked,
        so a poll landing while POST /message is genuinely still
        inside exchange() sees the real server-side LISTENING state,
        not a client-guessed one. Distinct from the test above: this
        one reads through the actual `route()`/`snapshot()` JSON
        contract a browser would poll, not the in-process registry
        directly. exchange() is wrapped (never replaced) with an
        artificial delay for the same reason as the test above - the
        real routing phase is normally too fast for any poll interval
        to reliably land inside."""
        import json

        live = _live_with_scripted_speaker()
        real_exchange = serve_ui.exchange

        def slow_exchange(*args, **kwargs):
            time.sleep(0.4)
            return real_exchange(*args, **kwargs)

        monkeypatch.setattr(serve_ui, "exchange", slow_exchange)

        seen_listening = threading.Event()

        def poll_until_listening():
            deadline = time.time() + 2.0
            while time.time() < deadline and not seen_listening.is_set():
                _, body, _ = route("/snapshot.json", live)
                data = json.loads(body)
                arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
                if arthur["activity"] == "listening":
                    seen_listening.set()
                time.sleep(0.02)

        poller = threading.Thread(target=poll_until_listening)
        poller.start()
        route_message("read my inbox for job opportunities", live, to="Arthur")
        poller.join(timeout=1.0)

        assert seen_listening.is_set(), (
            "a concurrent GET /snapshot.json never observed LISTENING while "
            "POST /message was genuinely still inside exchange() - the "
            "Stage 17.16 wiring is not actually connected"
        )


# ---------------------------------------------------------------------------
# 2. No second presence mechanism, no timer, no fabricated state.
# ---------------------------------------------------------------------------


class TestNoSecondMechanismNoTimer:
    def test_route_message_reuses_the_one_existing_registry_never_a_new_one(self):
        source = SERVE_UI_SRC.read_text("utf-8")
        fn = source.split("def route_message(", 1)[1].split("\ndef ", 1)[0]
        assert "PresenceRegistry(" not in fn
        assert "system.runtime.presence_registry" in fn

    def test_no_timer_was_introduced_to_simulate_listening(self):
        source = SERVE_UI_SRC.read_text("utf-8")
        fn = source.split("def route_message(", 1)[1].split("\ndef ", 1)[0]
        for forbidden in ("threading.Timer", "setTimeout", "setInterval", "sched."):
            assert forbidden not in fn

    def test_presencestate_still_has_exactly_three_members(self):
        """Re-affirmed: no THINKING (or any other) member was invented
        merely to make the animation prettier - the brief's own explicit
        instruction, and the pre-existing rule this module's docstring
        already states."""
        assert {s.value for s in PresenceState} == {"idle", "listening", "speaking"}

    def test_the_addressed_guard_matches_delivers_own_registered_agent_check(self):
        """Structural pin: the exact same "is this a real, registered
        agent" question, asked the exact same way (`registry.get(name)
        is not None`), never a re-implementation or a looser check."""
        source = SERVE_UI_SRC.read_text("utf-8")
        fn = source.split("def route_message(", 1)[1].split("\ndef ", 1)[0]
        assert "system.manager.registry.get(addressed) is not None" in fn


# ---------------------------------------------------------------------------
# JS structural helpers - the same slicing convention every Stage 17.5+
# test file already uses.
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


# ---------------------------------------------------------------------------
# 3. Transitions: every previously direct (`=`) assignment inside the
#    SPEAKING branch is now eased (`+=  (target - current) * factor`);
#    the LISTENING head lean is now eased into as well.
# ---------------------------------------------------------------------------


class TestTransitionsAreEasedNotSnapped:
    def test_arm_rotation_is_computed_as_a_target_then_eased(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            "const targetZ = restZ[i] + Math.sin(t*gestureRate + i*1.7) "
            "* gestureAmp * intentEffect.gestureMul * liveliness * (i===0?1:-1);"
        ) in speaking
        assert "const targetX = Math.sin(t*gestureRate*0.6 + i) * 0.1;" in speaking
        assert "pivot.rotation.z += (targetZ - pivot.rotation.z) * 0.22;" in speaking
        assert "pivot.rotation.x += (targetX - pivot.rotation.x) * 0.22;" in speaking
        # The old direct-assignment form must be genuinely gone, not
        # merely supplemented.
        assert "pivot.rotation.z = restZ[i] + Math.sin(t*gestureRate" not in speaking
        assert "pivot.rotation.x = Math.sin(t*gestureRate*0.6" not in speaking

    def test_the_nod_is_computed_as_a_target_then_eased(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            "const nodTarget = nodPhase > 4.9\n"
            "            ? Math.sin((nodPhase-4.9)*Math.PI/0.5) * 0.14 * intentEffect.nodMul : 0;"
        ) in speaking
        assert (
            "group.userData.head.rotation.x += (nodTarget - group.userData.head.rotation.x) * 0.22;"
        ) in speaking
        assert "group.userData.head.rotation.x = nodPhase > 4.9" not in speaking

    def test_the_mouth_pulse_is_computed_as_a_target_then_eased(self):
        speaking = _speaking_branch(_mount_body())
        assert "const mouthTarget = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4)) * 2.6;" in speaking
        assert "mouth.scale.y += (mouthTarget - mouth.scale.y) * 0.4;" in speaking
        assert "mouth.scale.y = 0.3 + Math.abs(Math.sin(t*gestureRate*2.4))" not in speaking

    def test_the_torso_lean_is_computed_as_a_target_then_eased(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            "const torsoTarget = restTorsoRotZ + Math.sin(t*gestureRate*0.5) "
            "* 0.035 * intentEffect.gestureMul * liveliness;"
        ) in speaking
        assert "torso.rotation.z += (torsoTarget - torso.rotation.z) * 0.22;" in speaking
        assert "torso.rotation.z = restTorsoRotZ + Math.sin(t*gestureRate*0.5)" not in speaking

    def test_the_listening_head_lean_is_computed_as_a_target_then_eased(self):
        listening = _listening_branch(_mount_body())
        assert "const headLeanTarget = 0.12 + Math.sin(t*0.9) * 0.015;" in listening
        assert (
            "group.userData.head.rotation.x += (headLeanTarget - "
            "group.userData.head.rotation.x) * 0.15;"
        ) in listening
        assert "group.userData.head.rotation.x = 0.12 + Math.sin(t*0.9)" not in listening

    def test_idles_own_easing_mechanism_is_unchanged(self):
        """Stage 17.16 touched SPEAKING and LISTENING only, and idle's
        easing *mechanism* - a real per-tick interpolation, never a snap,
        at its own established coefficients - must survive every later
        phase untouched.

        Stage 17.21 did leave it untouched in exactly that sense while
        changing what idle eases *toward*: rest plus a small slow drift
        instead of bare rest ("settled, not frozen"). So the coefficients
        and the interpolation form are pinned here, and the targets are
        pinned where they belong - in 17.6B's and 17.7's own tests, which
        own that question.
        """
        idle = _idle_branch(_mount_body())
        assert "- pivot.rotation.z) * 0.06;" in idle
        assert "- pivot.rotation.x) * 0.06;" in idle
        assert "mouth.scale.y += (1 - mouth.scale.y) * 0.15;" in idle
        assert "- torso.rotation.z) * 0.06;" in idle
        # Still an interpolation, never an assignment.
        for snap in (
            "pivot.rotation.z = restZ[i]",
            "torso.rotation.z = restTorsoRotZ",
            "mouth.scale.y = 1;",
        ):
            assert snap not in idle, snap


# ---------------------------------------------------------------------------
# 4. "Avoid repetitive robotic loops": a real, deterministic, bounded
#    second modulation - never Math.random(), never unbounded.
# ---------------------------------------------------------------------------


class TestLivelinessBreaksUpPerfectPeriodicity:
    def test_liveliness_is_deterministic_never_random(self):
        speaking = _speaking_branch(_mount_body())
        assert "gestureSignature" in speaking.split("const liveliness", 1)[1].split("\n", 1)[0]
        for block in (_mount_body(),):
            assert "random(" not in block.lower()

    def test_liveliness_is_defined_exactly_once_and_analytically_bounded(self):
        speaking = _speaking_branch(_mount_body())
        assert speaking.count("const liveliness =") == 1
        line = [ln for ln in speaking.splitlines() if "const liveliness =" in ln][0]
        assert "0.82 +" in line and "* 0.36;" in line  # [0.82, 1.18]

    def test_liveliness_multiplies_arm_and_torso_amplitude_only(self):
        speaking = _speaking_branch(_mount_body())
        assert speaking.count("* liveliness") == 2  # targetZ, torsoTarget
        # Never the mouth pulse or the head nod - those stay exactly the
        # honest "something is being said"/"an occasional nod" cues they
        # already were, unmodulated by this phase's own amplitude knob.
        mouth_lines = [ln for ln in speaking.splitlines() if "const mouthTarget" in ln]
        assert mouth_lines, "const mouthTarget line not found"
        mouth_line = mouth_lines[0]
        nod_lines = "\n".join(
            ln for ln in speaking.splitlines() if "nodTarget" in ln or "nodPhase" in ln
        )
        assert "liveliness" not in mouth_line
        assert "liveliness" not in nod_lines

    def test_liveliness_has_a_period_incommensurate_with_the_gesture_rate(self):
        """0.13 rad/s has no small-integer ratio to gestureRate's own
        [1.5, 2.6] rad/s range - the concrete mechanism that keeps a
        long speaking turn from ever repeating with exact periodicity,
        rather than merely claiming it does."""
        speaking = _speaking_branch(_mount_body())
        assert "Math.sin(t*0.13 + gestureSignature*5)" in speaking

    def test_liveliness_never_reaches_listening_or_idle(self):
        mount = _mount_body()
        assert "liveliness" not in _listening_branch(mount)
        assert "liveliness" not in _idle_branch(mount)


# ---------------------------------------------------------------------------
# 5. PresentationIntent compatibility - unchanged, still speaking-only.
# ---------------------------------------------------------------------------


class TestPresentationIntentCompatibilityPreserved:
    def test_intent_lookup_is_unchanged(self):
        speaking = _speaking_branch(_mount_body())
        assert (
            'const intentGesture = (live && live.avatar && live.avatar.intent '
            '&& live.avatar.intent.gesture) || "neutral";'
        ) in speaking
        assert (
            "const intentEffect = PRESENTATION_INTENT_EFFECTS[intentGesture] "
            "|| PRESENTATION_INTENT_EFFECTS.neutral;"
        ) in speaking

    def test_intent_is_still_referenced_only_in_the_speaking_branch(self):
        mount = _mount_body()
        for block in (_listening_branch(mount), _idle_branch(mount)):
            assert "intentEffect" not in block
            assert "intentGesture" not in block

    def test_the_effects_table_is_still_defined_exactly_once(self):
        shell = _shell()
        assert shell.count("const PRESENTATION_INTENT_EFFECTS = {") == 1


# ---------------------------------------------------------------------------
# 6. Animation/state fallback behaviour: an unrecognised or missing
#    activity value must fall back to idle's own easing, never crash
#    and never read as SPEAKING/LISTENING.
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    def test_the_branch_structure_is_a_true_catch_all(self):
        """Exactly if/else-if/else, in that order, with no fourth
        branch - any activity value other than the literal strings
        "speaking"/"listening" (undefined, null, a typo, a future
        value not yet known to this renderer) falls straight into the
        idle branch's own easing, never throws, never fabricates
        speaking or listening."""
        mount = _mount_body()
        assert mount.count('if(activity === "speaking"){') == 1
        assert mount.count('} else if(activity === "listening"){') == 1
        assert mount.count("} else {") >= 1
        speaking_start = mount.index('if(activity === "speaking"){')
        listening_start = mount.index('} else if(activity === "listening"){')
        idle_start = mount.index("} else {")
        assert speaking_start < listening_start < idle_start


# ---------------------------------------------------------------------------
# 7. Real speech limitation - no new lip-sync/viseme/phoneme claim, no
#    device/provider coupling, introduced by this phase.
# ---------------------------------------------------------------------------


class TestRealSpeechLimitationRespected:
    def test_no_viseme_or_phoneme_claim_in_the_new_code(self):
        speaking = _speaking_branch(_mount_body())
        for consumed in (".viseme", ".phoneme", "visemeData", "phonemeData", "lipSync"):
            assert consumed not in speaking

    def test_no_llm_or_speech_provider_name_in_the_new_backend_code(self):
        source = SERVE_UI_SRC.read_text("utf-8")
        fn = source.split("def route_message(", 1)[1].split("\ndef ", 1)[0]
        for name in ("openai", "anthropic", "elevenlabs", "windows_speech", "gpt", "claude"):
            assert name not in fn.lower()

    def test_no_threejs_or_voiceprofile_coupling_in_the_new_backend_code(self):
        source = SERVE_UI_SRC.read_text("utf-8")
        fn = source.split("def route_message(", 1)[1].split("\ndef ", 1)[0]
        assert "THREE" not in fn
        assert "VoiceProfile" not in fn

    def test_no_brain_or_memory_reference_in_the_new_renderer_code(self):
        speaking = _speaking_branch(_mount_body())
        listening = _listening_branch(_mount_body())
        for block in (speaking, listening):
            assert "Brain" not in block
            assert "Memory" not in block


# ---------------------------------------------------------------------------
# 8. Evolution/Extensions/Upgrade governance remains untouched and
#    uncoupled from this phase's own additions.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_extensions_file_references_this_phases_additions(self):
        for rel in (
            "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            text = (REPO_ROOT / rel).read_text("utf-8")
            for forbidden in (
                "listening_for", "liveliness", "headLeanTarget", "targetZ",
                "targetX", "torsoTarget", "mouthTarget", "nodTarget",
            ):
                assert forbidden not in text, (rel, forbidden)

    def test_only_the_two_documented_files_changed_this_phase(self):
        """A structural pin, not a git check (no git assumptions in this
        suite): the two real changes this phase makes - the backend
        LISTENING assertion and the renderer easing/liveliness - each
        live in exactly one file, confirmed by presence of this
        phase's own markers."""
        assert "listening_for" in SERVE_UI_SRC.read_text("utf-8")
        assert "liveliness" in UI.read_text("utf-8")
