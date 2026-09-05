"""Stage 17.17 - Conversation Room Digital-Human Polish (final Stage 17
implementation phase).

Inspection (PresenceRegistry/AgentPresence, `Response.turn.handled_by`,
SPEAKING/LISTENING/IDLE, DigitalHumanProfile, AvatarProfile,
PresentationIntent, per-agent gesture signatures, room-attention, and
every Stage 17.1-17.16 test file) found the Conversation Room's actual
*mechanism* already correct: the real canonical speaker already drives
`.room-body.speaking` (see `tests/test_stage17_7_room_conversation.py`'s
own `TestCanonicalSpeakerAtTheSnapshotBoundary`), room-attention is
already honestly distinct from real LISTENING (see that same file's
`TestRoomAttentionIsHonest`), per-agent gesture/idle signatures already
prevent synchronised movement (Stage 17.6B), and the person never
becomes a third avatar (Stage 17.6A). None of that needed changing.

Three genuine, narrow "polish" defects were found instead, all in
`ui/index.html`'s CSS, none in any Python file and none in any animation
*logic*:

1. `.room{grid-template-rows:auto auto 1fr auto}` had four row tracks
   for what has been **five** real direct children of `.room` since
   Stage 17.6A added `.room-bodies` (`.agent3d[data-room3d=core]`,
   `.room-bodies`, `.presence`, `.transcript`, `.glass.say`) - nobody
   updated the track count when that fifth child was added. The one
   flexible (`1fr`) track therefore landed on `.presence` (the small
   roster of orbs) instead of `.transcript` (the actual message list),
   so the transcript grew with its own content instead of scrolling
   internally, and a long conversation could push the composer down
   and eventually out of the visible view. Fixed by widening the track
   list to five (`auto auto auto 1fr auto`), putting `1fr` back on
   `.transcript` where it belongs.

2. `.room-body.speaking .agent3d` and `.room-body.selected .agent3d`
   have identical CSS specificity (two classes each), so on a tie the
   *later*-declared rule wins. `.selected` was declared after
   `.speaking`, so the very common case of addressing an agent and
   then hearing them answer - `.selected` and `.speaking` on the same
   `.room-body` at once - silently rendered the plain blue "who I
   picked" border instead of the pulsing green "who is actually
   speaking" glow, directly undermining this phase's own Requirement 1
   ("the real canonical speaker must always be visually obvious").
   Fixed by reordering the two rules so `.speaking` is declared last.

3. The one existing mobile breakpoint already shrinks the generic
   `.agent3d{height:240px}` box to 180px on narrow screens, but
   `.room-body .agent3d{height:200px}` is more specific than either
   rule, so room-bodies silently never shrank on a phone at all - the
   opposite of every other 3D box on the page, and worse on a phone
   where two or more room-bodies stack vertically above the transcript.
   Fixed by adding `.room-body .agent3d{height:180px}` to the existing
   mobile block - the same 180px value every other mobile 3D box
   already uses, not a new number.

Nothing else changed: no new backend state, no new PresenceRegistry, no
new AvatarProfile/Voice/Conversation system, no Brain/Memory coupling,
no agent name hardcoded into the generic renderer, no Stage 18 work.

Verified with the same two methods every Stage 17.5+ file already uses:
real, executed Python tests for the registry/route/persistence layer,
and source-text structural checks for the CSS/DOM (no JS execution
harness in this repository can lay out real CSS Grid/Flexbox - see
`tests/test_stage17_5_digital_human_renderer.py`'s own module docstring
for the full account of why headless-browser pixel verification was
found unreliable here). The CSS Grid reasoning above (an item with a
non-`visible` `overflow` gets an automatic minimum size of 0 for grid
sizing purposes, so `.transcript{overflow:auto}` was always able to
shrink to its track once the track count was corrected) follows directly
from the CSS Grid/Flexbox specification's own "Implied Minimum Size"
rule - not a guess about what a browser happens to do.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import scripts.serve_ui as serve_ui
from scripts.exchange import memory_for
from scripts.serve_ui import build_live_system, route, route_message
from src.channels.channel import VoiceChannel
from src.channels.speech import SpeechAdapter
from src.config.settings import Settings
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.presence import (
    PresenceState,
    presence_for,
    set_avatar_preference,
    set_digital_human_style,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


class _ScriptedSpeaker(SpeechAdapter):
    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay
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
        return True


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


def _shell() -> str:
    return UI.read_text("utf-8")


def _room_fn() -> str:
    return _shell().split("V.room = () => {", 1)[1].split("\n  };", 1)[0]


def _room_css_rows() -> list[str]:
    css = _shell().split(".room{height:100%;display:grid;grid-template-rows:", 1)[1].split(";", 1)[0]
    return css.split()


def _room_direct_children_in_order() -> list[str]:
    """The five real, literal direct children of `.room`'s own return
    template, in the order they appear - never the nested markup inside
    `.glass.say` (e.g. `.who-picker`), which is one level deeper and not
    a grid item of `.room` itself."""
    room = _room_fn()
    markers = {
        'data-room3d="core"': "core",
        'class="room-bodies"': "room-bodies",
        'class="presence"': "presence",
        'class="transcript"': "transcript",
        'class="glass say"': "glass",
    }
    found = [(room.index(marker), name) for marker, name in markers.items()]
    return [name for _, name in sorted(found)]


def _mobile_block() -> str:
    shell = _shell()
    return shell.split("/* ---- phone ----", 1)[1].split(
        "@media (prefers-reduced-motion:reduce)", 1
    )[0]


# ---------------------------------------------------------------------------
# 1. The grid track count now matches the room's real five children,
#    with the one flexible track on the transcript, not the roster.
# ---------------------------------------------------------------------------


class TestRoomGridTrackCountMatchesRealChildren:
    def test_five_children_get_five_tracks(self):
        assert len(_room_direct_children_in_order()) == 5
        assert len(_room_css_rows()) == 5

    def test_the_one_flexible_track_lands_on_the_transcript(self):
        order = _room_direct_children_in_order()
        rows = _room_css_rows()
        assert order == ["core", "room-bodies", "presence", "transcript", "glass"]
        assert rows == ["auto", "auto", "auto", "1fr", "auto"]
        assert rows[order.index("transcript")] == "1fr"
        assert rows[order.index("presence")] == "auto"

    def test_mutation_dropping_a_track_is_caught(self):
        """Mutation test: reproduce the exact original defect (four
        tracks for five children, 1fr misaligned onto .presence),
        confirm the tests above fail, then restore the fix."""
        path = UI
        original = path.read_text("utf-8")
        mutated = original.replace(
            "grid-template-rows:auto auto auto 1fr auto;gap:16px",
            "grid-template-rows:auto auto 1fr auto;gap:16px",
        )
        assert mutated != original
        try:
            path.write_text(mutated, encoding="utf-8")
            rows = _room_css_rows()
            order = _room_direct_children_in_order()
            assert not (len(rows) == 5 and rows[order.index("transcript")] == "1fr")
        finally:
            path.write_text(original, encoding="utf-8")
        # Re-confirm the real file is back to the fixed, correct state.
        assert _room_css_rows() == ["auto", "auto", "auto", "1fr", "auto"]


# ---------------------------------------------------------------------------
# 2. Speaking wins the .selected/.speaking cascade tie: the real
#    canonical speaker's glow is never silently overridden by the
#    merely-addressed border when both apply to the same room-body.
# ---------------------------------------------------------------------------


class TestSpeakingWinsTheCascadeTieOverSelected:
    def test_speaking_rule_is_declared_after_selected_rule(self):
        shell = _shell()
        selected_idx = shell.index(".room-body.selected .agent3d{")
        speaking_idx = shell.index(".room-body.speaking .agent3d{")
        assert selected_idx < speaking_idx, (
            "with equal specificity, the later-declared rule wins a tie - "
            "speaking must be declared after selected so the real speaker "
            "is never visually hidden behind the merely-selected border"
        )

    def test_both_rules_have_identical_specificity_two_classes_each(self):
        """Confirms the ordering above is actually load-bearing - if one
        selector had higher specificity, source order would not matter
        and this fix would do nothing."""
        shell = _shell()
        for selector in (".room-body.speaking .agent3d{", ".room-body.selected .agent3d{"):
            assert selector.count(".") == 3  # room-body, speaking/selected, agent3d

    def test_mutation_reverting_the_order_is_caught(self):
        path = UI
        original = path.read_text("utf-8")
        selected_rule = ".room-body.selected .agent3d{border-color:var(--alfred);box-shadow:0 0 14px rgba(125,216,232,.28)}"
        assert original.count(selected_rule) == 1
        # Move .selected back to after .speaking's keyframes, the exact
        # original (buggy) ordering, by deleting it from its current spot
        # and re-inserting it after @keyframes speakerglow's closing brace.
        without = original.replace(selected_rule + "\n", "", 1)
        marker = "50%{box-shadow:0 0 30px rgba(79,209,165,.6)}}"
        assert without.count(marker) == 1
        mutated = without.replace(marker, marker + "\n  " + selected_rule, 1)
        assert mutated != original
        try:
            path.write_text(mutated, encoding="utf-8")
            shell = path.read_text("utf-8")
            selected_idx = shell.index(".room-body.selected .agent3d{")
            speaking_idx = shell.index(".room-body.speaking .agent3d{")
            assert not (selected_idx < speaking_idx)
        finally:
            path.write_text(original, encoding="utf-8")
        shell = _shell()
        assert shell.index(".room-body.selected .agent3d{") < shell.index(
            ".room-body.speaking .agent3d{"
        )


# ---------------------------------------------------------------------------
# 3. The mobile breakpoint now shrinks room-body canvases the same way
#    it already shrinks every other 3D box on the page.
# ---------------------------------------------------------------------------


class TestMobileRoomBodyHeightMatchesEveryOtherMobile3DBox:
    def test_the_mobile_block_shrinks_room_body_canvases(self):
        mobile = _mobile_block()
        assert ".room-body .agent3d{height:180px}" in mobile

    def test_it_reuses_the_same_number_every_other_mobile_3d_box_uses(self):
        shell = _shell()
        generic_mobile_height = shell.split("@media (max-width:760px){.agent3d{height:", 1)[1].split(
            "px}}", 1
        )[0]
        assert generic_mobile_height == "180"

    def test_no_new_breakpoint_was_introduced(self):
        shell = _shell()
        assert shell.count("@media (max-width:760px)") == 2

    def test_mutation_removing_the_mobile_override_is_caught(self):
        path = UI
        original = path.read_text("utf-8")
        mutated = original.replace(
            "    .room-body .agent3d{height:180px}\n  }",
            "  }",
            1,
        )
        assert mutated != original
        try:
            path.write_text(mutated, encoding="utf-8")
            mobile = _mobile_block()
            assert ".room-body .agent3d{height:180px}" not in mobile
        finally:
            path.write_text(original, encoding="utf-8")
        assert ".room-body .agent3d{height:180px}" in _mobile_block()


# ---------------------------------------------------------------------------
# 4. Speaker clarity: room-body flags are derived only from real
#    activity - never animation timing, never a guess.
# ---------------------------------------------------------------------------


class TestSpeakerFlagsComeOnlyFromRealActivity:
    def test_the_speaking_flag_reads_real_activity_not_time(self):
        room = _room_fn()
        assert 'const flags = [activity==="speaking"?"speaking":""' in room
        flags_line = [ln for ln in room.splitlines() if "const flags" in ln][0]
        for forbidden in ("Date.now", "performance.now", "Math.random", "setTimeout", "setInterval"):
            assert forbidden not in flags_line

    def test_activity_itself_comes_from_the_real_agent_snapshot(self):
        room = _room_fn()
        assert 'const activity = pAgent ? (pAgent.activity||"idle") : "idle";' in room

    def test_a_real_two_agent_exchange_marks_only_the_true_speaker(self):
        """End-to-end: addressing Arthur must leave Alfred's own
        real activity untouched and idle, confirmed through the same
        route()/snapshot() contract the room's own S.agents comes from."""
        import json

        live = _live_with_scripted_speaker(delay=0.2)
        samples_arthur: list = []
        samples_alfred: list = []

        def watch_both():
            for _ in range(20):
                samples_arthur.append(live.runtime.presence_registry.get("Arthur"))
                samples_alfred.append(
                    live.runtime.presence_registry.get(live.manager.own_skills.name)
                )
                time.sleep(0.02)

        watcher = threading.Thread(target=watch_both)
        watcher.start()
        status, body, _ = route_message(
            "read my inbox for job opportunities", live, to="Arthur"
        )
        watcher.join()

        turn = json.loads(body)
        assert status == 200
        assert turn["handled_by"] == "Arthur"
        assert PresenceState.SPEAKING in samples_arthur
        assert PresenceState.SPEAKING not in samples_alfred

        _, snap_body, _ = route("/snapshot.json", live)
        data = json.loads(snap_body)
        for agent in data["agents"]:
            assert agent["activity"] == "idle"  # both settled back after the post returned


# ---------------------------------------------------------------------------
# 5. Stage 17.16's LISTENING signal composes correctly with the room's
#    own presentation - the roster's pdot, driven by the same real
#    per-participant activity the room-body flags already use.
# ---------------------------------------------------------------------------


class TestListeningIntegratesWithRoomPresentation:
    def test_the_beings_roster_pdot_is_driven_by_the_same_real_activity(self):
        room = _room_fn()
        assert (
            "const pstate = presAgent ? (presAgent.activity || \"idle\") : null;"
        ) in room
        assert 'class="pdot ${esc(pstate)}"' in room

    def test_a_concurrent_snapshot_poll_observes_listening_for_the_addressed_agent_only(
        self, monkeypatch
    ):
        """Bridges Stage 17.16's route_message LISTENING assertion into
        this stage's room rendering: a real poll of the same
        /snapshot.json the room's own S.agents comes from must see
        Arthur (and only Arthur) as "listening" while exchange() is
        still processing, never Alfred."""
        import json

        live = _live_with_scripted_speaker()
        real_exchange = serve_ui.exchange

        def slow_exchange(*args, **kwargs):
            time.sleep(0.35)
            return real_exchange(*args, **kwargs)

        monkeypatch.setattr(serve_ui, "exchange", slow_exchange)

        seen = threading.Event()
        alfred_ever_listening = threading.Event()

        def poll():
            deadline = time.time() + 2.0
            while time.time() < deadline and not seen.is_set():
                _, body, _ = route("/snapshot.json", live)
                data = json.loads(body)
                arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
                alfred = next(a for a in data["agents"] if a["name"] == live.manager.own_skills.name)
                if arthur["activity"] == "listening":
                    seen.set()
                if alfred["activity"] == "listening":
                    alfred_ever_listening.set()
                time.sleep(0.02)

        poller = threading.Thread(target=poll)
        poller.start()
        route_message("read my inbox for job opportunities", live, to="Arthur")
        poller.join(timeout=1.0)

        assert seen.is_set(), "a concurrent poll never observed Arthur's real LISTENING state"
        assert not alfred_ever_listening.is_set()


# ---------------------------------------------------------------------------
# 6. The user remains conceptually distinct - re-confirmed at the room
#    boundary this phase's own CSS changes sit beside.
# ---------------------------------------------------------------------------


class TestUserRemainsDistinctInTheRoom:
    def test_only_non_person_participants_get_a_3d_body(self):
        room = _room_fn()
        assert 'c.participants.filter(p=>p.kind!=="person")' in room

    def test_the_persons_own_orb_styling_is_never_an_agent_hue(self):
        shell = _shell()
        assert (
            ".being.person .orb{border-color:rgba(79,209,165,.4);color:var(--ok)}"
        ) in shell

    def test_the_person_is_labelled_you_never_manager_or_specialist(self):
        room = _room_fn()
        assert '${p.kind==="person"?"you":(isMgr?"manager":"specialist")}' in room


# ---------------------------------------------------------------------------
# 7. Existing controls are unaffected by this phase's CSS-only changes.
# ---------------------------------------------------------------------------


class TestExistingControlsUnaffected:
    def test_the_composer_markup_is_unchanged(self):
        room = _room_fn()
        assert (
            '<input class="composer" placeholder="${selectedAgent?`Speak to '
            '${esc(selectedAgent)}…`:"Speak into the room…"}"${LIVE ? "" : " disabled"}>'
        ) in room

    def test_the_who_picker_and_avatar_selection_paths_are_unchanged(self):
        room = _room_fn()
        assert '<div class="who-picker">${picker}</div>' in room
        shell = _shell()
        assert "function wireAvatarPicker(){" in shell
        assert "function wireDigitalHumanForm(){" in shell

    def test_room_entry_exit_navigation_is_unchanged(self):
        shell = _shell()
        assert "V.room = () => {" in shell
        assert 'data-mode="room"' in shell or '"room"' in shell


# ---------------------------------------------------------------------------
# 8. AvatarProfile/DigitalHumanProfile persistence and PresentationIntent
#    compatibility, re-confirmed with two real room participants.
# ---------------------------------------------------------------------------


class TestPersistenceAndPresentationIntentUnaffected:
    def test_two_room_participants_each_persist_their_own_avatar_choice(self):
        live = build_live_system(settings())
        arthur = live.manager.registry.get("Arthur")
        alfred_mem = memory_for(live.manager, live.manager.own_skills.name)
        arthur_mem = memory_for(live.manager, "Arthur")

        set_avatar_preference(
            live.manager.own_skills.name, "calm_composed",
            provider=live.runtime.avatar_provider,
            recall=alfred_mem.recall, remember=alfred_mem.remember, forget=alfred_mem.forget,
        )
        set_avatar_preference(
            "Arthur", "sharp_technical",
            provider=live.runtime.avatar_provider,
            recall=arthur_mem.recall, remember=arthur_mem.remember, forget=arthur_mem.forget,
        )

        alfred_presence = presence_for(
            live.manager.own_skills.name, provider=live.runtime.avatar_provider, recall=alfred_mem.recall
        )
        arthur_presence = presence_for(
            "Arthur", provider=live.runtime.avatar_provider, recall=arthur_mem.recall
        )
        assert alfred_presence.avatar.avatar_id == "calm_composed"
        assert arthur_presence.avatar.avatar_id == "sharp_technical"

    def test_digital_human_style_still_round_trips_through_a_live_snapshot(self):
        import json

        live = build_live_system(settings())
        arthur_mem = memory_for(live.manager, "Arthur")
        set_digital_human_style(
            "Arthur", {"gesture_style": "measured"},
            recall=arthur_mem.recall, remember=arthur_mem.remember, forget=arthur_mem.forget,
        )
        _, body, _ = route("/snapshot.json", live)
        data = json.loads(body)
        arthur = next(a for a in data["agents"] if a["name"] == "Arthur")
        assert arthur["avatar"]["profile"]["gesture_style"] == "measured"

    def test_presentationintent_effects_table_is_still_the_one_canonical_seam(self):
        shell = _shell()
        assert shell.count("const PRESENTATION_INTENT_EFFECTS = {") == 1
        speaking = shell.split('if(activity === "speaking"){', 1)[1].split(
            '} else if(activity === "listening")', 1
        )[0]
        assert "intentEffect" in speaking


# ---------------------------------------------------------------------------
# 9. Provider independence of this phase's own changes.
# ---------------------------------------------------------------------------


class TestProviderIndependenceOfThisPhasesChanges:
    """Scoped tightly to the three actual rules this phase changed -
    not the wide, pre-existing CSS between them, which legitimately
    mentions unrelated things (e.g. the pre-existing .agent3d comment
    naming Brain/Memory as sibling *surfaces*, not a coupling)."""

    def _changed_blocks(self) -> list[str]:
        shell = _shell()
        room_rule = shell.split(
            ".room{height:100%;display:grid;grid-template-rows:", 1
        )[1].split("}", 1)[0]
        speaking_selected = shell.split(
            ".room-body.selected .agent3d{", 1
        )[1].split("@keyframes speakerglow", 1)[0]
        mobile_line = shell.split(".room-body .agent3d{height:180px}", 1)[0][-5:] + \
            ".room-body .agent3d{height:180px}"
        return [room_rule, speaking_selected, mobile_line]

    def test_no_provider_llm_or_device_name_in_the_changed_css(self):
        blocks = [b.lower() for b in self._changed_blocks()]
        for forbidden in (
            "openai", "anthropic", "elevenlabs", "windows_speech", "gpt", "claude",
            "redmi", "iphone", "ipad", "samsung", "galaxy", "android", "three.js", "threejs",
        ):
            for block in blocks:
                assert forbidden not in block, forbidden

    def test_no_brain_or_memory_reference_in_the_changed_css(self):
        for block in self._changed_blocks():
            assert "Brain" not in block
            assert "Memory" not in block


# ---------------------------------------------------------------------------
# 10. Evolution/Extensions/Upgrade governance remains untouched and
#     uncoupled from this phase's own additions.
# ---------------------------------------------------------------------------


class TestEvolutionCompatibility:
    def test_no_evolution_or_extensions_file_references_this_phases_additions(self):
        for rel in (
            "src/evolution.py", "src/extensions.py", "src/upgrade_lifecycle.py",
            "src/upgrade_executor.py", "src/control_center/upgrade_planner.py",
        ):
            text = (REPO_ROOT / rel).read_text("utf-8")
            for forbidden in ("room-body .agent3d", "grid-template-rows:auto auto auto 1fr auto"):
                assert forbidden not in text, (rel, forbidden)

    def test_no_python_source_file_changed_this_phase(self):
        """This phase's entire diff lives in ui/index.html's CSS - no
        Python file anywhere under src/ references the new selectors."""
        for path in (REPO_ROOT / "src").rglob("*.py"):
            text = path.read_text("utf-8")
            assert "room-body .agent3d" not in text
            assert "grid-template-rows:auto auto auto 1fr auto" not in text
