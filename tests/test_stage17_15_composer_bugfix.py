"""Stage 17.15 - live UI bug: the conversation composer read as unusable.

**What was actually reported:** "I cannot type into the conversation/
input field" on the live server.

**Investigation, in order:**

1. Inspected every item the bug report itself asked about - pointer-
   events, z-index, absolutely-positioned 3D canvas overlays, focus
   handling, disabled/read-only state, intercepting event listeners, a
   modal/editor overlay. No modal system exists in this codebase at
   all; the only keydown listener is bound directly to the composer
   `<input>` itself (never document/window); no CSS in `.say`/
   `.composer`/`.glass` sets `pointer-events:none` or a z-index that
   could cover it; the only decorative fixed-position layers
   (`.atmos`/`.vignette`/`.scan`) are already `pointer-events:none`.
2. Confirmed the real POST /message endpoint genuinely works end to
   end against the live server (curl, bypassing the browser entirely) -
   submit and response both function.
3. Built a minimal, hand-rolled DOM (Node's own `vm` module, no
   dependency added) and actually *executed* the real script - not
   just syntax-checked it (`node --check`, which cannot catch a
   runtime error) and not just pattern-matched its source text (every
   structural test since Stage 17.5) - against a real snapshot. The
   entire boot sequence (go(start), wireComposer(), renderChrome(),
   initVoiceActivation(), the background canvas render loop) completed
   with zero uncaught errors, and the rendered `<input class="composer">`
   carried no `disabled` attribute.

**The actual root cause:** not a structural bug at all. The `.state`
label rendered immediately beside the composer read "read-only ·
unusable: no speech engine is installed" - a real, honest description
of *speech OUTPUT* (whether Alfred can talk back), reusing the word
"read-only" in a position and a font-weight (the page's own `--warn`
color) that gives a reasonable reader every reason to believe it
describes the text box a few pixels to its left, which was never
disabled, never read-only, and never anything other than fully
writable. Fixed by rewording that one label to "voice unavailable" -
a phrase that cannot be misread as describing the input.

This file adds a real, reusable verification method alongside the fix:
`_boot_room_in_node()` actually runs the live page's own script through
a Node `vm` sandbox against a real, freshly-taken snapshot from an
ephemeral test system, and inspects the genuinely rendered output -
not a guess about what the template would probably produce.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.services.email.in_memory_mailbox import InMemoryMailbox

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


def _shell() -> str:
    return UI.read_text("utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


def _node() -> str | None:
    for candidate in ("node", "node.exe"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return None


# A minimal, hand-rolled browser environment - just enough surface for
# ui/index.html's own top-level script to run start to finish (boot,
# go(start), wireComposer(), renderChrome(), initVoiceActivation(), the
# background canvas loop) without throwing. Not a general-purpose DOM;
# every stubbed method exists because the real script genuinely calls
# it, confirmed by first running with the stub absent and letting the
# resulting ReferenceError/TypeError name exactly what was missing.
_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const snapshot = fs.readFileSync(process.argv[3], "utf8");

class FakeClassList {
  constructor(el) { this.el = el; }
  add(...c) { c.forEach(x => this.el._classes.add(x)); }
  remove(...c) { c.forEach(x => this.el._classes.delete(x)); }
  toggle(c, on) { if (on) this.el._classes.add(c); else this.el._classes.delete(c); }
  contains(c) { return this.el._classes.has(c); }
}

function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    _classes: new Set(), children: [], parentElement: null, dataset: {}, style: {}, attrs: {},
    _text: "", _html: "", listeners: {},
    get className() { return [...el._classes].join(" "); },
    set className(v) { el._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
    get textContent() { return el._text; },
    set textContent(v) { el._text = String(v); },
    get innerHTML() { return el._html; },
    set innerHTML(v) { el._html = String(v); el.children = []; },
    get value() { return el._value || ""; },
    set value(v) { el._value = v; },
    classList: null,
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k]; },
    appendChild(child) { el.children.push(child); child.parentElement = el; return child; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    scrollIntoView() {}, remove() {}, focus() {},
    getContext(kind) {
      if (kind !== "2d") return null;
      const grad = { addColorStop() {} };
      return {
        clearRect() {}, beginPath() {}, closePath() {}, arc() {}, moveTo() {}, lineTo() {},
        fill() {}, stroke() {}, createLinearGradient: () => grad, createRadialGradient: () => grad,
        set fillStyle(v) {}, get fillStyle() { return ""; },
        set strokeStyle(v) {}, get strokeStyle() { return ""; },
        set lineWidth(v) {}, get lineWidth() { return 1; },
      };
    },
  };
  el.classList = new FakeClassList(el);
  return el;
}

const byId = {
  snapshot: (() => { const e = makeEl("script"); e._text = snapshot; return e; })(),
  scene: makeEl("canvas"), stage: makeEl("div"), modes: makeEl("div"),
  "crown-name": makeEl("div"), "crown-sub": makeEl("div"), stamp: makeEl("div"),
  "voice-toggle": makeEl("button"),
};
const documentStub = {
  getElementById: (id) => byId[id] || null,
  querySelector: (sel) => (sel === "#stage" ? byId.stage : null),
  querySelectorAll: () => [], createElement: (tag) => makeEl(tag),
  addEventListener: () => {}, documentElement: makeEl("html"), body: makeEl("body"),
};
const localStorageStub = {
  _store: { "alfred.mode": "room" },
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
  setItem(k, v) { this._store[k] = String(v); },
};
const sandbox = {
  console, document: documentStub, localStorage: localStorageStub,
  location: { protocol: "http:", href: "http://127.0.0.1:8642/" },
  navigator: { userAgent: "node-harness" },
  innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
  matchMedia: () => ({ matches: false }),
  ResizeObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  Math, JSON, Date,
  setInterval: () => 0, setTimeout: () => 0, clearTimeout: () => {},
  fetch: () => Promise.reject(new Error("no network in this harness")),
  CSS: { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&") },
  Event: class {}, addEventListener: () => {}, removeEventListener: () => {},
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
try {
  vm.runInContext(scriptSrc, sandbox, { filename: "index.html-script" });
  console.log(JSON.stringify({ ok: true, roomHtml: byId.stage.innerHTML }));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e && e.stack || e) }));
  process.exitCode = 1;
}
"""


def _boot_room_in_node(real_snapshot: dict, tmp_path: Path) -> dict:
    """Actually execute ui/index.html's real script (Node's vm module,
    no browser, no dependency added) against a real snapshot, with
    localStorage pre-seeded so the boot sequence's own go(start) lands
    on Room - the one view that has the composer. Returns
    {"ok": bool, "roomHtml": str} or {"ok": False, "error": str}.
    """
    node = _node()
    if node is None:
        pytest.skip("no node runtime available in this environment")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(_HARNESS, encoding="utf-8")
    script_path = tmp_path / "ui_script.js"
    script_path.write_text(_script(), encoding="utf-8")
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(real_snapshot), encoding="utf-8")

    result = subprocess.run(
        [node, str(harness_path), str(script_path), str(snapshot_path)],
        capture_output=True, text=True,
    )
    assert result.stdout.strip(), result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestTheActualReportedBug:
    def test_the_misleading_read_only_wording_is_gone(self):
        """The literal root cause: reworded so "read-only" (which reads
        as describing the adjacent text input) can never appear next to
        the composer again. Scoped to the actual template expression -
        not the whole file, which legitimately still *discusses* the
        old wording in this fix's own explanatory comment above it."""
        shell = _shell()
        speech_line = shell.split("const speech = S.speech.usable", 1)[1].split("\n", 2)[0:2]
        speech_expr = "const speech = S.speech.usable" + "\n".join(speech_line)
        assert "read-only" not in speech_expr
        assert "voice unavailable" in speech_expr

    def test_the_speech_state_label_is_never_derived_onto_the_input_itself(self):
        """Structural proof the two were always genuinely independent -
        `speech` (the mislabelled variable) is used only inside the
        `.state` span, never as part of the composer's own attributes."""
        shell = _shell()
        room_fn = shell.split("V.room = () => {", 1)[1].split("\n  V.workspace", 1)[0]
        composer_line = [ln for ln in room_fn.splitlines() if 'class="composer"' in ln][0]
        assert "speech" not in composer_line
        assert composer_line.count("${LIVE ? ") == 1  # the input's only real conditional

    def test_the_composer_disabled_attribute_depends_only_on_live(self):
        shell = _shell()
        assert (
            '<input class="composer" placeholder="${selectedAgent?`Speak to '
            '${esc(selectedAgent)}…`:"Speak into the room…"}"${LIVE ? "" : " disabled"}>'
        ) in shell

    def test_boot_and_room_render_succeed_against_a_real_snapshot(self, tmp_path):
        """The real, executable proof: run the actual script (not a
        pattern match of its source) against a freshly-taken real
        snapshot and confirm it boots clean and Room's own composer
        renders genuinely enabled."""
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)

        result = _boot_room_in_node(data, tmp_path)
        assert result["ok"], result.get("error")
        room_html = result["roomHtml"]
        assert 'class="composer"' in room_html
        composer_idx = room_html.index('class="composer"')
        composer_tag = room_html[max(0, composer_idx - 20):composer_idx + 200]
        assert "disabled" not in composer_tag
        assert "read-only" not in room_html

    def test_the_speech_unusable_label_still_appears_when_genuinely_unusable(self, tmp_path):
        """The fix must not hide real information - a genuinely unusable
        speech engine must still be reported, just never with wording
        that could be mistaken for the input's own state."""
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        assert data["speech"]["usable"] is False  # this test system has no speech engine configured

        result = _boot_room_in_node(data, tmp_path)
        assert result["ok"], result.get("error")
        assert "voice unavailable" in result["roomHtml"]

    def test_room_specific_3d_mount_points_are_unaffected_by_the_fix(self, tmp_path):
        """Verifies item 6 of the bug report: the fix must not disturb
        the 3D avatar/Conversation Room rendering it sits beside."""
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)

        result = _boot_room_in_node(data, tmp_path)
        assert result["ok"], result.get("error")
        room_html = result["roomHtml"]
        assert 'class="room-bodies"' in room_html
        assert 'data-room3d="core"' in room_html
        assert 'class="who-picker"' in room_html


class TestNoOtherBlockingMechanismExists:
    """The rest of the bug report's own checklist, each re-confirmed."""

    def test_no_modal_or_overlay_system_exists(self):
        shell = _shell()
        assert "modal" not in shell.lower()

    def test_no_pointer_events_none_applies_to_the_composer_or_its_container(self):
        shell = _shell()
        for selector in (".say{", ".say input{", ".composer{", ".glass{", ".glass > *{"):
            block = shell.split(selector, 1)
            if len(block) < 2:
                continue
            rule = block[1].split("}", 1)[0]
            assert "pointer-events:none" not in rule.replace(" ", "")

    def test_keydown_listeners_are_bound_to_their_own_input_never_globally(self):
        """The real invariant the original bug report needed: no
        document/window-level keydown listener can ever globally
        intercept keystrokes. Alfred's Real 3D Workspace added a second,
        legitimate element-scoped listener (the task board's own
        add-input, wired the identical way the conversation composer's
        own input already is) - a second safe listener is not the bug
        this test exists to catch, and the count below is updated to
        say so explicitly rather than silently drifting stale."""
        shell = _shell()
        assert shell.count('addEventListener("keydown"') == 2
        assert 'input.addEventListener("keydown"' in shell
        assert "document.addEventListener(\"keydown\"" not in shell
        assert "window.addEventListener(\"keydown\"" not in shell

    def test_the_workspace_add_input_never_carries_the_composer_class(self):
        """A genuine, live-verified regression: the Workspace's own
        add-task input was originally given class="composer ws-add-input"
        purely to inherit visual styling (never needed - ".say input"
        already styles it generically) - but wireComposer()'s own
        selector, ".say input.composer", matched it too, so BOTH
        listeners fired on Enter. wireComposer's ran first, cleared the
        input and sent the raw text with no "add a task to " prefix and
        no addressing - silently swallowing every attempt to add a task
        from the Workspace UI. Caught only by executing the real script
        against a real browser (see tests/test_alfred_workspace.py's own
        wiring tests for the structural half of this guard); pinned here
        as a hard regex boundary so "composer" can never again appear as
        a whole class token on this element.
        """
        import re

        shell = _shell()
        input_line = [ln for ln in shell.splitlines() if "ws-add-input" in ln][0]
        class_match = re.search(r'class="([^"]*)"', input_line)
        assert class_match is not None
        classes = class_match.group(1).split()
        assert "composer" not in classes
        assert "ws-add-input" in classes

    def test_the_composer_carries_no_readonly_or_negative_tabindex(self):
        shell = _shell()
        composer_line = [ln for ln in shell.splitlines() if 'class="composer"' in ln][0]
        assert "readonly" not in composer_line
        assert "tabindex" not in composer_line
