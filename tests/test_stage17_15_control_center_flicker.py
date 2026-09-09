"""Stage 17.15 - live UI bug: the Control Center visibly blinking.

**What was actually reported:** "the live Control Center is visibly
blinking/flickering."

**Investigation:** every item the bug report itself asked about -
repeated boot/render calls, repeated DOM replacement, polling/state
updates replacing the main UI, setInterval/setTimeout loops, repeated
Three.js/canvas initialization, CSS animations restarting on state
update, snapshot refreshes rebuilding the page, message-handler-driven
full renders, runtime errors, canvas destruction/recreation.

The real mechanism, confirmed by actually executing the live page's own
script (Node's `vm` module, no dependency added - not a guess about
what the template would probably do): `refreshFromLive()` polls every
5 seconds while live-served and calls `go(current)` on every tick -
genuinely necessary, since that is how live state updates (presence,
speaking/listening, memory counts, the transcript) ever reach the
screen at all. `go()` always rebuilds `stage.innerHTML` from scratch
(`<div class="view on">...`), and `.view.on`'s own CSS
(`animation:enter .5s ...`, a real, intentional fade-in meant for
genuine navigation) plays on *any* element that newly carries the
`on` class - including the brand-new element every single poll
creates, even though the user never navigated anywhere. The 3D/WebGL
canvases were never the problem: `wireStages()` already reuses their
renderer/context across a re-render (see its own comment), which is
exactly why this bug reads as "the *page* is blinking," not "the 3D
scenes are restarting."

**The fix:** `go()` now tracks whether it is re-rendering the view it
was *already on* (`isRefresh`) - true only for a same-view refresh (a
poll, or clicking the tab you're already on), never for an actual
navigation or the very first render. A `no-enter` marker class is added
only in that case, and a small, more-specific CSS rule
(`.view.on.no-enter{animation:none}`) suppresses just that one replay,
without touching `.view.on` itself or the real entrance animation a
genuine navigation still gets.

Verified two ways: `_boot_and_render()` executes the real script once
against a real snapshot (does the initial render still carry the real
entrance animation?); `_boot_and_poll_once()` additionally simulates
one real poll cycle end to end (does the *second* render of the same
view carry `no-enter`, proving the fade genuinely stops repeating?).
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


# The shared minimal DOM stub (same design as
# test_stage17_15_composer_bugfix.py's own harness - kept independent
# rather than imported, so each bug's own regression test stays
# self-contained and readable on its own).
_DOM_STUB = r"""
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
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; }, scrollIntoView() {}, remove() {}, focus() {},
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
"""

# Harness 1: one real boot, no poll - proves a genuine first render (and
# so a real navigation) still gets the real entrance animation.
_BOOT_ONLY_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const snapshot = fs.readFileSync(process.argv[3], "utf8");
""" + _DOM_STUB + r"""
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
  console.log(JSON.stringify({ ok: false, error: String((e && e.stack) || e) }));
  process.exitCode = 1;
}
"""

# Harness 2: boot, then let one real poll cycle actually run
# (setInterval -> setImmediate -> refreshFromLive() -> await fetch ->
# go(current)) - the literal mechanism that repeats every 5s live.
_POLL_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const snapshot = fs.readFileSync(process.argv[3], "utf8");
""" + _DOM_STUB + r"""
const localStorageStub = {
  _store: { "alfred.mode": "room" },
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
  setItem(k, v) { this._store[k] = String(v); },
};
const renders = [];
let pollCount = 0;
const sandbox = {
  console, document: documentStub, localStorage: localStorageStub,
  location: { protocol: "http:", href: "http://127.0.0.1:8642/" },
  navigator: { userAgent: "node-harness" },
  innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
  matchMedia: () => ({ matches: false }),
  ResizeObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  Math, JSON, Date,
  // pollCount tracks specifically the live-refresh interval (the
  // literal mechanism this test is about), not "any interval the page
  // ever registers" - the Real Audio Recording/Capture checkpoint
  // legitimately added a second, unrelated page-lifetime interval (a
  // once-per-second elapsed-recording-time display, wireRecordingControls()'s
  // own setInterval(..., 1000)) that has nothing to do with the poll/
  // flicker mechanism this file guards. Filtering on the 5000ms delay
  // is what keeps this test measuring the thing it says it measures.
  setInterval: (fn, delay) => { if (delay === 5000) pollCount++; setImmediate(fn); return 0; },
  setTimeout: () => 0, clearTimeout: () => {},
  fetch: (url) => {
    if (String(url).includes("snapshot.json")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(snapshot)) });
    }
    return Promise.reject(new Error("unexpected fetch: " + url));
  },
  CSS: { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&") },
  Event: class {}, addEventListener: () => {}, removeEventListener: () => {},
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
(async () => {
  try {
    vm.runInContext(scriptSrc, sandbox, { filename: "index.html-script" });
    renders.push(byId.stage.innerHTML);
    await new Promise((resolve) => setTimeout(resolve, 200));
    renders.push(byId.stage.innerHTML);
    console.log(JSON.stringify({
      ok: true, pollCount, renderCount: renders.length,
      initialViewOpenTag: renders[0].match(/<div class="view[^"]*">/)?.[0] || null,
      afterPollViewOpenTag: renders[renders.length - 1].match(/<div class="view[^"]*">/)?.[0] || null,
    }));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: String((e && e.stack) || e) }));
    process.exitCode = 1;
  }
})();
"""


def _run_harness(harness_src: str, real_snapshot: dict, tmp_path: Path) -> dict:
    node = _node()
    if node is None:
        pytest.skip("no node runtime available in this environment")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(harness_src, encoding="utf-8")
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


class TestTheActualReportedFlicker:
    def test_go_tracks_whether_it_is_re_rendering_the_same_view(self):
        """The structural fix: a real distinction between a genuine
        navigation and a same-view refresh."""
        shell = _shell()
        assert "const isRefresh = hasEnteredAnyView && current === key;" in shell
        assert (
            '`<div class="view on${isRefresh ? " no-enter" : ""}">${V[key]()}</div>`'
        ) in shell

    def test_the_no_enter_css_rule_disables_only_the_entrance_animation(self):
        shell = _shell()
        assert ".view.on.no-enter{animation:none}" in shell
        # Never weakens the real rule - .view.on itself is untouched.
        assert ".view.on{display:block;animation:enter .5s cubic-bezier(.16,.8,.28,1)}" in shell

    def test_a_genuine_first_render_still_gets_the_real_entrance_animation(self, tmp_path):
        """The fix must not silence the animation for real navigation -
        only for a same-view refresh."""
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)

        result = _run_harness(_BOOT_ONLY_HARNESS, data, tmp_path)
        assert result["ok"], result.get("error")
        assert '<div class="view on">' in result["roomHtml"]
        assert "no-enter" not in result["roomHtml"].split("</html>")[0][:200]

    def test_a_real_simulated_poll_no_longer_replays_the_entrance_animation(self, tmp_path):
        """The actual regression proof: run the real script, let one
        real poll cycle genuinely happen (the literal mechanism behind
        the reported flicker), and confirm the second render of the
        same view carries the marker that stops the fade from
        repeating."""
        _, manager, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)

        result = _run_harness(_POLL_HARNESS, data, tmp_path)
        assert result["ok"], result.get("error")
        assert result["pollCount"] == 1
        assert result["initialViewOpenTag"] == '<div class="view on">'
        assert result["afterPollViewOpenTag"] == '<div class="view on no-enter">'

    def test_clicking_the_already_active_tab_also_does_not_replay(self):
        """The same isRefresh logic covers a second real-world case with
        the identical honest reasoning: nothing actually changed."""
        shell = _shell()
        fn = shell.split("function go(key, agentName){", 1)[1].split("\n  }\n", 1)[0]
        assert "current === key" in fn


class TestNoOtherFlickerCauseExists:
    """The rest of the bug report's own checklist, each re-confirmed."""

    def test_the_3d_stage_lifecycle_already_reuses_its_own_canvas_across_a_rerender(self):
        """The 3D canvases were never the actual cause - wireStages()
        already avoids tearing them down on every poll (pre-existing,
        unchanged by this fix - re-confirmed here, not just assumed)."""
        shell = _shell()
        fn = shell.split("function wireStages(attrKey", 1)[1].split("\n  }\n", 1)[0]
        assert "running.renderer.domElement" in fn
        assert "mountFn(el, name);" in fn

    def test_build_and_frame_the_background_canvas_are_not_called_from_go(self):
        """The orrery background canvas is built once at boot, never
        re-built by go() - the flicker was never about it."""
        shell = _shell()
        fn = shell.split("function go(key, agentName){", 1)[1].split("\n  }\n", 1)[0]
        assert "build()" not in fn
        assert "frame()" not in fn

    def test_only_one_poll_interval_exists(self):
        shell = _shell()
        assert shell.count("setInterval(refreshFromLive, 5000)") == 1

    def test_no_second_view_transition_animation_was_added(self):
        shell = _shell()
        assert shell.count("@keyframes enter{") == 1
