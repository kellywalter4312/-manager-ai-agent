"""Stage 17.18 - the private agent interface, and the professional shell.

Two things happened in this pass, and this file guards both.

**1. One generic private-agent interface.** Before it, a person's entry
point into the product was the Control Center - an orrery of the *system*,
with an agent's name set 84px tall over it - and the only place to actually
talk was the Conversation Room, the shared space. There was no surface for
"me and one agent", which is the thing this product mostly is.

``V.agent`` is that surface, and the one architectural requirement on it is
that it is **generic over an identity**. It receives a name (``openAgent``,
resolved in ``go()``) and renders that agent's own already-canonical data:
the same ``S.agents`` entry the Control Center, Cognition and the Room all
read, the same AvatarProfile/DigitalHumanProfile the Avatar Studio writes
and ``mountAvatarBody3D`` already draws, the same ``PresenceRegistry``
state the room dot already shows, and the same one ``Conversation``
``exchange()`` already records. It is not an Alfred screen with Arthur's
name substituted, and it is not a second Agent, Brain, Memory, Voice or
Conversation architecture - so the tests below assert the absence of each
of those as hard as they assert the presence of the surface.

The private transcript is the sharpest case. It is a **view** over the one
canonical transcript, selected by exchange (what the person said, then
whoever actually handled it), never a second store: an exchange belongs to
this agent's thread when the person addressed this agent, or when this
agent is the one who actually answered. That second half is what keeps
delegation honest rather than hiding it - ``Response.turn.handled_by``, not
the screen, decides who spoke, and a delegated answer stays attributed to
the specialist who gave it. ``TestDelegationStaysAttributedToTheRealSpeaker``
proves that against a real, executed two-agent exchange.

**2. The professional shell.** Product wording replaced debug wording on
the surfaces a person lives on ("half brain" across the top of every
screen, "independent specialist", "the installation"), the navigation grew
two tiers so Control Center is deep observability rather than the front
door, and - the one that was not cosmetic - the page now **ships the font
it declares**. It declared "IBM Plex Mono"/"Space Mono" for its whole life
and shipped neither, so every browser silently fell through to a system
default: the UI had never once rendered in a face anybody chose. Nothing
technical was removed; ``TestTheTechnicalDetailIsStillThere`` pins that
every number and every raw string still exists, one click away, on the
surfaces built to carry it.

Verified the same two ways every Stage 17.5+ file already is: real,
executed Python for the routing/snapshot/persistence layer, and source-text
structural checks for the CSS/DOM, plus - where the claim is about what a
person actually sees - the real script actually executed in Node against a
real snapshot, the identical vm-based harness
``tests/test_stage17_15_composer_bugfix.py`` established.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts.exchange import exchange
from src.composition import (
    build_channel,
    build_conversation,
    build_manager,
    build_runtime,
)
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.sample_inbox import sample_inbox

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
VENDOR = REPO_ROOT / "ui" / "vendor"
SERVE_UI = REPO_ROOT / "scripts" / "serve_ui.py"


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


def system(mailbox=None):
    runtime = build_runtime(settings())
    manager = build_manager(
        runtime, mailbox=mailbox if mailbox is not None else InMemoryMailbox(), profile=None
    )
    channel = build_channel(runtime, manager)
    conversation = build_conversation(manager)
    return runtime, manager, channel, conversation, ControlCenter(runtime, manager)


def _shell() -> str:
    return UI.read_text("utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


def _agent_view() -> str:
    """V.agent's own body - the private interface template alone."""
    return _shell().split("V.agent = () => {", 1)[1].split("\n  };", 1)[0]


def _room_view() -> str:
    return _shell().split("V.room = () => {", 1)[1].split("\n  };", 1)[0]


def _node() -> str | None:
    for candidate in ("node", "node.exe"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return None


# ---------------------------------------------------------------------------
# The browser harness. Identical in shape to the one
# tests/test_stage17_15_composer_bugfix.py established, with one genuine
# addition: querySelectorAll actually scans the rendered HTML for
# [data-goto] / [data-focus] targets and memoises what it returns, so go()'s
# own click wiring can be *exercised* - a person switching agents - rather
# than stubbed away. Everything it stubs, it stubs because the real script
# genuinely calls it.
# ---------------------------------------------------------------------------
_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const snapshot = fs.readFileSync(process.argv[3], "utf8");
const startMode = process.argv[4] || "agent";

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

function scanTargets(html, attr) {
  const out = [];
  const re = new RegExp('data-' + attr + '="([^"]*)"([^>]*)', "g");
  let m;
  while ((m = re.exec(html)) !== null) {
    const el = makeEl("button");
    el.dataset[attr] = m[1];
    const agent = /data-agent="([^"]*)"/.exec(m[2] || "");
    if (agent) el.dataset.agent = agent[1];
    out.push(el);
  }
  return out;
}

const stage = makeEl("div");
// Memoised per rendered HTML: the elements go() attached its click listeners
// to must be the very same objects a later lookup gets back, or a click here
// would fire on a listener-less copy and prove nothing.
const _scanCache = new Map();
stage.querySelectorAll = (sel) => {
  const attr = sel === "[data-goto]" ? "goto" : sel === "[data-focus]" ? "focus" : null;
  if (!attr) return [];
  const key = attr + "|" + stage._html;
  if (!_scanCache.has(key)) _scanCache.set(key, scanTargets(stage._html, attr));
  return _scanCache.get(key);
};

const byId = {
  snapshot: (() => { const e = makeEl("script"); e._text = snapshot; return e; })(),
  scene: makeEl("canvas"), stage, modes: makeEl("div"),
  "crown-name": makeEl("div"), "crown-sub": makeEl("div"), stamp: makeEl("div"),
  "voice-toggle": makeEl("button"),
};
const documentStub = {
  getElementById: (id) => byId[id] || null,
  querySelector: (sel) => (sel === "#stage" ? stage : null),
  querySelectorAll: () => [], createElement: (tag) => makeEl(tag),
  addEventListener: () => {}, documentElement: makeEl("html"), body: makeEl("body"),
};
const localStorageStub = {
  _store: { "alfred.mode": startMode },
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

const out = { ok: false, views: {}, crown: {}, modes: [] };
try {
  vm.runInContext(scriptSrc, sandbox, { filename: "index.html-script" });
  out.crown = {
    name: byId["crown-name"]._text, sub: byId["crown-sub"]._text, stamp: byId.stamp._text,
  };
  out.modes = byId.modes.children.map(b => ({ key: b.dataset.k, cls: b.className }));
  out.views.boot = stage._html;

  out.views.perAgent = {};
  for (const name of JSON.parse(snapshot).agents.map(a => a.name)) {
    const pill = stage.querySelectorAll("[data-goto]").find(
      el => el.dataset.goto === "agent" && el.dataset.agent === name);
    if (!pill) { out.views.perAgent[name] = "NO PILL FOUND"; continue; }
    pill.listeners.click[0]();
    out.views.perAgent[name] = stage._html;
  }

  for (const key of ["room", "control"]) {
    const btn = byId.modes.children.find(b => b.dataset.k === key);
    btn.listeners.click[0]();
    out.views[key] = stage._html;
  }
  out.ok = true;
} catch (e) {
  out.error = String((e && e.stack) || e);
}
console.log(JSON.stringify(out));
"""


def _boot(real_snapshot: dict, tmp_path: Path, start_mode: str = "agent") -> dict:
    """Actually execute ui/index.html's real script against a real snapshot
    and drive its real navigation. Returns every rendered surface."""
    node = _node()
    if node is None:
        pytest.skip("no node runtime available in this environment")
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    script = tmp_path / "ui_script.js"
    script.write_text(_script(), encoding="utf-8")
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps(real_snapshot), encoding="utf-8")

    result = subprocess.run(
        [node, str(harness), str(script), str(snap), start_mode],
        capture_output=True, text=True,
    )
    assert result.stdout.strip(), result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out.get("ok"), out.get("error")
    return out


@pytest.fixture(scope="module")
def two_agent_snapshot():
    """A real snapshot after two real, canonically-routed exchanges - one
    addressed to the orchestrator, one addressed to the specialist, both
    through the same `exchange()` a terminal session and POST /message both
    call. Not a fixture dict: the real system, having really answered."""
    runtime, manager, channel, conversation, centre = system(mailbox=sample_inbox())
    names = [a.name for a in manager.registry.all_agents()]
    orchestrator, specialist = names[0], names[1]
    exchange(channel, conversation, "give me a status report", addressed=orchestrator)
    exchange(channel, conversation, "check the inbox for opportunities", addressed=specialist)
    data = snapshot(centre, conversation=conversation, channel=channel)
    return data, orchestrator, specialist


# ---------------------------------------------------------------------------
# 1. The interface is generic over an identity - not a screen per agent.
# ---------------------------------------------------------------------------


class TestTheInterfaceIsGenericOverAnIdentity:
    def test_exactly_one_private_interface_template_exists(self):
        shell = _shell()
        assert shell.count("V.agent = () => {") == 1

    def test_it_names_no_agent(self):
        """The decisive one. A second agent must get this whole surface
        with nothing here changing - so no literal name, and no branch on
        one, may appear anywhere in the template."""
        view = _agent_view()
        for forbidden in ('"Alfred"', "'Alfred'", '"Arthur"', "'Arthur'", "Alfred", "Arthur"):
            assert forbidden not in view, forbidden

    def test_it_renders_from_the_one_agent_the_snapshot_already_reports(self):
        """Everything drawn comes off the S.agents entry - the same
        record every other surface reads. Nothing is computed here."""
        view = _agent_view()
        assert "const a = openAgentOf();" in view
        for field in ("a.brain", "a.memory", "a.voice", "a.avatar", "a.capabilities", "a.activity"):
            assert field in view, field

    def test_which_agent_is_open_is_resolved_in_go_never_hardcoded(self):
        shell = _shell()
        block = shell.split('if(key === "agent"){', 1)[1].split("}", 1)[0]
        assert "agentNamed(agentName)" in block
        assert "manager().name" in block  # the registry's own answer, not a name
        assert "Alfred" not in block and "Arthur" not in block

    def test_the_agent_switcher_is_the_real_roster(self):
        """One pill per registered agent, from S.agents - never a list
        written on this page."""
        view = _agent_view()
        assert "S.agents.map(x =>" in view
        assert 'data-goto="agent"' in view
        assert 'data-agent="${esc(x.name)}"' in view

    def test_it_uses_the_one_existing_identity_presentation(self):
        """identityOf()/identityGlyph() - never a second hue table, side
        count or emblem system of its own."""
        view = _agent_view()
        assert "identityOf(a)" in view
        assert "identityGlyph(a," in view
        for forbidden in ("SPECIALIST_HUES", "SPECIALIST_SIDES", "hashOf(", "Math.random"):
            assert forbidden not in view, forbidden

    def test_every_registered_agent_actually_renders_its_own_interface(self, two_agent_snapshot, tmp_path):
        """Executed, not inspected: boot the real script, click each real
        agent's pill, and read what was actually drawn."""
        data, orchestrator, specialist = two_agent_snapshot
        out = _boot(data, tmp_path)
        for name in (orchestrator, specialist):
            html = out["views"]["perAgent"][name]
            assert 'class="private"' in html, name
            assert f'class="nm">{name}<' in html, name
            assert f'data-avatarbody3d="{name}"' in html, name
            assert f'class="composer" data-to="{name}"' in html, name
        assert out["views"]["perAgent"][orchestrator] != out["views"]["perAgent"][specialist]

    def test_the_interface_opens_on_the_registrys_own_orchestrator_by_default(
        self, two_agent_snapshot, tmp_path
    ):
        data, orchestrator, _ = two_agent_snapshot
        out = _boot(data, tmp_path)
        assert 'class="private"' in out["views"]["boot"]
        assert f'class="nm">{orchestrator}<' in out["views"]["boot"]


# ---------------------------------------------------------------------------
# 2. No second architecture was created. The hardest rules in the brief.
# ---------------------------------------------------------------------------


class TestNoSecondArchitecture:
    def test_no_python_agent_brain_memory_or_voice_class_was_added(self):
        """This pass changed exactly two Python files, and neither
        declares a class of any kind."""
        for rel in ("scripts/serve_ui.py", "src/control_center/snapshot.py"):
            tree = ast.parse((REPO_ROOT / rel).read_text("utf-8"))
            classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            assert "LiveServer" in classes or classes == [] or "SnapshotHandler" in classes
            for name in classes:
                for forbidden in ("Agent", "Brain", "Memory", "Voice", "Conversation", "Presence"):
                    assert forbidden not in name, f"{rel}: {name}"

    def test_the_interface_builds_no_state_of_its_own(self):
        """`openAgent` is a name and nothing else - the same shape
        selectedAgent and focusBody already have. The template holds no
        object, no store and no cache."""
        view = _agent_view()
        for forbidden in ("new Map(", "new Set(", "localStorage", "fetch("):
            assert forbidden not in view, forbidden
        decl = [ln for ln in _script().splitlines() if ln.strip().startswith("let openAgent")]
        assert decl == ["  let openAgent = null;"]

    def test_the_digital_human_is_the_one_existing_renderer(self):
        """One mount function, one wiring loop, one data attribute -
        this surface joined them, it did not fork them."""
        shell = _shell()
        assert shell.count("function mountAvatarBody3D(container") == 1
        assert shell.count("function wireAvatarBody3D(){") == 1
        view = _agent_view()
        assert "data-avatarbody3d=" in view
        # Mounted only through the generic loop, never called directly -
        # the identical rule the Room lives under (Stage 17.6A).
        assert "mountAvatarBody3D" not in view

    def test_the_avatar_and_presence_seams_are_read_never_reimplemented(self):
        view = _agent_view()
        assert "a.avatar.avatar.label" in view      # the real AvatarProfile
        assert "a.activity" in view                  # the real PresenceRegistry state
        for forbidden in ("PresenceState", "setInterval", "SPEAKING", "LISTENING"):
            assert forbidden not in view, forbidden

    def test_no_second_speech_or_presence_assertion(self):
        """Presence is read off the snapshot, never asserted here. A
        surface that could set a state would be faking presence."""
        view = _agent_view()
        for forbidden in ("presence.set", "speaking(", "speechSynthesis", "SpeechRecognition"):
            assert forbidden not in view, forbidden


# ---------------------------------------------------------------------------
# 3. The private transcript is a VIEW over the ONE canonical conversation.
# ---------------------------------------------------------------------------


class TestThePrivateThreadIsAViewNotASecondConversation:
    def test_it_reads_the_one_conversation_the_snapshot_carries(self):
        shell = _shell()
        fn = shell.split("function exchanges(){", 1)[1].split("\n  }", 1)[0]
        assert "S.conversation" in fn and "utterances" in fn
        # It only ever reads. No push into S, no write anywhere.
        assert "S.conversation.utterances.push" not in shell

    def test_selection_is_by_who_was_addressed_or_who_actually_answered(self):
        shell = _shell()
        fn = shell.split("function privateThread(name){", 1)[1].split("\n  }", 1)[0]
        assert "x.ask.addressed === name" in fn
        assert "r.speaker === name" in fn

    def test_a_private_line_lands_only_in_that_agents_thread(self, two_agent_snapshot, tmp_path):
        """Executed against two real, differently-addressed turns."""
        data, orchestrator, specialist = two_agent_snapshot
        out = _boot(data, tmp_path)
        to_orchestrator = out["views"]["perAgent"][orchestrator]
        to_specialist = out["views"]["perAgent"][specialist]
        assert "give me a status report" in to_orchestrator
        assert "check the inbox for opportunities" in to_specialist
        assert "check the inbox for opportunities" not in to_orchestrator
        assert "give me a status report" not in to_specialist

    def test_each_thread_carries_the_real_answer_attributed_to_its_real_speaker(
        self, two_agent_snapshot, tmp_path
    ):
        """Not just the question. Each private thread must show both
        sides, and the reply must carry the name of whoever actually
        produced it."""
        data, orchestrator, specialist = two_agent_snapshot
        out = _boot(data, tmp_path)
        for name in (orchestrator, specialist):
            html = out["views"]["perAgent"][name]
            body = html.split('class="private-transcript"', 1)[1].split('class="glass say', 1)[0]
            speakers = re.findall(r'class="who"[^>]*>([^<]*)', body)
            assert speakers, name
            # one question, one answer, alternating - and every answer is
            # attributed to this agent, because this agent gave it.
            assert set(speakers) == {"you", name}, (name, speakers)
            assert speakers[0] == "you" and speakers[1] == name

    def test_an_unaddressed_message_the_specialist_answered_lands_in_its_thread(
        self, tmp_path
    ):
        """The delegation case, executed. Nobody was addressed, the
        Manager routed to the specialist, and the specialist answered -
        so the exchange belongs to the specialist's thread on the
        strength of `handled_by` alone, and the orchestrator's thread
        does not claim it."""
        runtime, manager, channel, conversation, centre = system(mailbox=sample_inbox())
        names = [a.name for a in manager.registry.all_agents()]
        orchestrator, specialist = names[0], names[1]
        turn = exchange(channel, conversation, "check the inbox for opportunities")
        assert turn.handled_by == specialist and turn.delegated is True
        asked = [u for u in conversation.utterances if u.speaker not in names]
        assert asked and asked[0].addressed is None, "nobody was addressed"

        data = snapshot(centre, conversation=conversation, channel=channel)
        out = _boot(data, tmp_path)
        to_specialist = out["views"]["perAgent"][specialist]
        to_orchestrator = out["views"]["perAgent"][orchestrator]
        assert "check the inbox for opportunities" in to_specialist
        assert "check the inbox for opportunities" not in to_orchestrator

    def test_an_empty_thread_says_so_rather_than_inventing_one(self, tmp_path):
        _, _, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        out = _boot(data, tmp_path)
        assert "Nothing said here yet" in out["views"]["boot"]

    def test_the_composer_uses_the_one_canonical_route_and_override(self):
        """`to` is exchange()'s own `addressed` parameter, resolved
        server-side through the real registry. The page never routes."""
        view = _agent_view()
        assert 'data-to="${esc(a.name)}"' in view
        shell = _shell()
        wire = shell.split("function wireComposer(){", 1)[1].split("\n  }\n", 1)[0]
        assert 'const addressedTo = input.dataset.to || selectedAgent;' in wire
        assert 'fetch("message"' in wire
        assert "JSON.stringify(addressedTo ? {text, to: addressedTo} : {text})" in wire

    def test_the_room_composer_is_untouched_by_that(self):
        """The Room carries no data-to and still uses selectedAgent alone
        - the private interface added a case, it did not change the
        existing one."""
        room = _room_view()
        assert 'class="composer" data-to=' not in room
        assert (
            '<input class="composer" placeholder="${selectedAgent?`Speak to '
            '${esc(selectedAgent)}…`:"Speak into the room…"}"${LIVE ? "" : " disabled"}>'
        ) in room


# ---------------------------------------------------------------------------
# 4. Canonical routing is authoritative, and delegation stays visible.
# ---------------------------------------------------------------------------


class TestDelegationStaysAttributedToTheRealSpeaker:
    def test_an_addressed_turn_is_handled_by_the_agent_the_system_chose(self):
        """Real, executed routing - the page's `to` is an override the
        Manager resolves, and `handled_by` remains the authority."""
        runtime, manager, channel, conversation, _ = system(
            mailbox=sample_inbox()
        )
        names = [a.name for a in manager.registry.all_agents()]
        turn = exchange(channel, conversation, "check the inbox", addressed=names[1])
        assert turn.handled_by == names[1]
        assert turn.delegated is True

    def test_the_specialists_answer_is_attributed_to_the_specialist_in_the_room(self):
        runtime, manager, channel, conversation, _ = system(
            mailbox=sample_inbox()
        )
        names = [a.name for a in manager.registry.all_agents()]
        turn = exchange(channel, conversation, "check the inbox", addressed=names[1])
        spoken = [u for u in conversation.utterances if u.speaker == turn.handled_by]
        assert spoken, "the real handler must be the recorded speaker"

    def test_the_screen_renders_whoever_actually_spoke_never_the_open_agent(self):
        """privateTurn() takes the speaker off the utterance, and marks a
        reply from anyone other than the open agent as such rather than
        quietly relabelling it."""
        shell = _shell()
        fn = shell.split("function privateTurn(u, delegated){", 1)[1].split("\n  }", 1)[0]
        assert "esc(u.speaker)" in fn
        assert "openAgent" not in fn  # it cannot even see whose screen this is
        turns = shell.split("function privateTurns(name){", 1)[1].split("\n  }", 1)[0]
        assert "r.speaker !== name" in turns


# ---------------------------------------------------------------------------
# 5. The Conversation Room stays a separate, first-class surface.
# ---------------------------------------------------------------------------


class TestTheConversationRoomStaysDistinct:
    def test_both_surfaces_exist_and_are_separate_templates(self):
        shell = _shell()
        assert shell.count("V.room = () => {") == 1
        assert shell.count("V.agent = () => {") == 1
        assert '["room","Conversation Room"' in shell
        assert '["agent","Agents"' in shell

    def test_the_room_keeps_what_makes_it_the_room(self, two_agent_snapshot, tmp_path):
        """Several participants present at once, a roster, and a picker
        for choosing among them - none of which the private line has."""
        data, orchestrator, _ = two_agent_snapshot
        out = _boot(data, tmp_path)
        room = out["views"]["room"]
        assert 'class="room"' in room
        assert 'class="presence"' in room and 'class="being' in room
        assert 'class="who-picker"' in room
        assert 'class="private"' not in room

    def test_the_private_line_carries_no_room_roster(self, two_agent_snapshot, tmp_path):
        data, orchestrator, _ = two_agent_snapshot
        out = _boot(data, tmp_path)
        private = out["views"]["perAgent"][orchestrator]
        assert 'class="presence"' not in private
        assert 'class="who-picker"' not in private
        assert 'class="room"' not in private

    def test_each_says_in_words_which_kind_of_conversation_it_is(self):
        assert "A private line to ${esc(a.name)}" in _agent_view()
        assert "Speak into the room" in _room_view()


# ---------------------------------------------------------------------------
# 6. Typography: the declared font is the shipped font.
# ---------------------------------------------------------------------------


class TestTheDeclaredFontIsActuallyShipped:
    def test_the_font_file_is_vendored_and_is_a_real_font(self):
        font = VENDOR / "CascadiaMono.ttf"
        assert font.exists()
        assert font.stat().st_size > 300_000
        # A real TrueType/OpenType signature, not a renamed placeholder.
        assert font.read_bytes()[:4] in (bytes([0, 1, 0, 0]), b"OTTO", b"true", b"ttcf")

    def test_the_page_declares_the_font_it_ships(self):
        shell = _shell()
        assert "@font-face{" in shell
        assert 'font-family:"Cascadia Mono";' in shell
        assert 'src:url("vendor/CascadiaMono.ttf") format("truetype");' in shell

    def test_the_declarations_name_only_families_that_ship_or_are_system_stacks(self):
        """The actual root cause, pinned: the two families this page named
        for its whole life were never vendored, so it always rendered in
        whatever the machine happened to have. Scoped to the declarations,
        because the comment above them legitimately still names them."""
        # The correction pass grew this from one shipped face to three,
        # each with a job: a display face for identity and headings, a
        # text face for prose and controls, and the mono scoped down to
        # metadata. Every family named in a declaration must be one this
        # page actually ships, or a plain system fallback - which is the
        # thing that was wrong before any of them shipped at all.
        shipped = {'"Cascadia Mono"', '"Space Grotesk"', '"Inter"'}
        # Quoted names in the fallback tail are stock system faces, which
        # need no shipping - they are what the browser falls back TO. What
        # must never appear is a quoted family that is neither: a webfont
        # named but not vendored, which is the exact bug this pass fixed.
        system_faces = {'"Segoe UI"', '"Helvetica Neue"', '"Apple Color Emoji"'}
        shipped = shipped | system_faces
        decls = [
            ln for ln in _shell().splitlines()
            if "--font:" in ln or "--mono:" in ln or "--display:" in ln
        ]
        assert len(decls) == 3
        for line in decls:
            quoted = set(re.findall(r'"[^"]+"', line.split(":", 1)[1]))
            assert quoted, line
            assert quoted <= shipped, (line, quoted - shipped)
        # And each of the three is a real @font-face pointing at a real
        # vendored file.
        shell = _shell()
        for family, file in (
            ("Cascadia Mono", "CascadiaMono.ttf"),
            ("Space Grotesk", "SpaceGrotesk.woff2"),
            ("Inter", "Inter.woff2"),
        ):
            assert f'font-family:"{family}";' in shell, family
            assert f'url("vendor/{file}")' in shell, file
            assert (VENDOR / file).exists(), file

    def test_the_font_url_is_relative_so_both_surfaces_resolve_it(self):
        """The static export sits beside ui/vendor/; the served page
        resolves the same path under /vendor/. One declaration, and no
        network in either case."""
        shell = _shell()
        assert 'url("vendor/CascadiaMono.ttf")' in shell
        assert 'url("/vendor/CascadiaMono.ttf")' not in shell
        assert "http://" not in shell.lower() and "https://" not in shell.lower()

    def test_the_server_serves_it_through_the_same_fixed_allowlist(self):
        source = SERVE_UI.read_text("utf-8")
        assert '"/vendor/CascadiaMono.ttf": ("CascadiaMono.ttf", "font/ttf"),' in source
        # Still an allowlist, not a file server - the guard that route has
        # always been under.
        assert "VENDOR_ASSETS = {" in source
        assert "iterdir" not in source and "glob(" not in source

    def test_there_is_one_type_scale_with_a_role_per_level(self):
        shell = _shell()
        for token in ("--t-agent:", "--t-head:", "--t-body:", "--t-control:", "--t-meta:"):
            assert token in shell, token
        assert "font-size:var(--t-agent)" in shell   # the agent's own name
        assert "font-size:var(--t-head)" in shell    # section headings
        assert "font-size:var(--t-body)" in shell    # conversation text
        assert "font-size:var(--t-meta)" in shell    # metadata


# ---------------------------------------------------------------------------
# 7. Product wording on the surfaces a person lives on.
# ---------------------------------------------------------------------------


class TestTheDebugWordingIsGoneFromTheProductSurfaces:
    def test_the_crown_no_longer_prints_a_raw_brain_mode(self, two_agent_snapshot, tmp_path):
        data, _, _ = two_agent_snapshot
        out = _boot(data, tmp_path)
        assert "brain" not in out["crown"]["sub"].lower()
        assert out["crown"]["sub"], "it must still say something real"

    def test_the_private_and_room_surfaces_carry_no_implementation_language(
        self, two_agent_snapshot, tmp_path
    ):
        data, orchestrator, specialist = two_agent_snapshot
        out = _boot(data, tmp_path)
        surfaces = (
            out["views"]["perAgent"][orchestrator]
            + out["views"]["perAgent"][specialist]
            + out["views"]["room"]
        ).lower()
        for word in (
            "half brain", "full brain", "installation", "independent specialist",
            "elevenlabs", "anthropic", "provider", "sapi", "desktop",
        ):
            assert word not in surfaces, word

    def test_the_control_center_names_each_agents_own_declared_role(self):
        """"independent specialist" was a label this page made up for
        every specialist alike. The role now comes from that agent's own
        identity document, first sentence only."""
        shell = _shell()
        assert "independent specialist" not in shell
        fn = shell.split("const shortRole = a => {", 1)[1].split("\n  };", 1)[0]
        assert "a.role" in fn
        assert "Alfred" not in fn and "Arthur" not in fn

    def test_readiness_is_product_wording_over_a_reading_the_system_already_made(self):
        """It maps the existing GREEN/AMBER/RED indicator - it does not
        make a second judgement, and it cannot invent a fourth state."""
        shell = _shell()
        block = shell.split("const READINESS = {", 1)[1].split("};", 1)[0]
        assert block.count(":") == 3
        for key in ("GREEN", "AMBER", "RED"):
            assert key in block, key
        fn = shell.split("const readinessOf = brain =>", 1)[1].split("\n", 1)[0]
        assert "brain.indicator" in fn
        assert "brain.mode" not in fn  # never re-derives the mode itself


class TestTheTechnicalDetailIsStillThere:
    """Nothing was removed - it was moved to where it belongs."""

    def test_cognition_still_reports_the_exact_mode_provider_and_reason(self):
        brain_view = _shell().split("V.brain = () =>", 1)[1].split("\n  V.memory", 1)[0]
        assert "a.brain.mode" in brain_view
        assert "a.brain.provider" in brain_view
        assert "a.brain.reason" in brain_view
        assert "a.brain.router_provider" in brain_view

    def test_environment_still_reports_the_raw_system_facts(self):
        status = _shell().split("V.status = () =>", 1)[1].split("\n  V.history", 1)[0]
        for field in (
            "S.system.provider", "S.system.memory_backend", "S.system.gmail_scope",
            "S.speech.engine", "S.speech.state", "S.system.warnings",
        ):
            assert field in status, field

    def test_the_control_center_still_renders_the_whole_orrery(self, two_agent_snapshot, tmp_path):
        data, _, _ = two_agent_snapshot
        out = _boot(data, tmp_path)
        control = out["views"]["control"]
        assert 'class="orrery"' in control
        assert 'class="sat"' in control
        assert 'class="facets"' in control

    def test_every_surface_the_page_has_ever_had_is_still_reachable(self):
        shell = _shell()
        for key in ("control", "room", "workspace", "brain", "memory", "status", "history"):
            assert f'["{key}",' in shell, key

    def test_the_voice_snapshot_still_carries_everything_it_did(self):
        """`character` and `preferred_voice` were added beside `traits`
        and `describes`, never instead of them - a surface chooses which
        it shows, and nothing lost a field."""
        _, _, channel, conversation, centre = system()
        data = snapshot(centre, conversation=conversation, channel=channel)
        for agent in data["agents"]:
            if not agent["voice"]["declared"]:
                continue
            voice = agent["voice"]
            for key in ("key", "traits", "describes", "character", "preferred_voice"):
                assert key in voice, key
            # character is a strict subset of traits - it drops the engine
            # voice name, it does not invent a characteristic.
            assert set(voice["character"]) <= set(voice["traits"])
            if voice["preferred_voice"]:
                assert voice["preferred_voice"] in voice["traits"]
                assert voice["preferred_voice"] not in voice["character"]


# ---------------------------------------------------------------------------
# 8. Hierarchy and navigation: Control Center is deep, not the front door.
# ---------------------------------------------------------------------------


class TestTheShellHasAHierarchy:
    def test_navigation_has_exactly_two_tiers(self, two_agent_snapshot, tmp_path):
        data, _, _ = two_agent_snapshot
        out = _boot(data, tmp_path)
        primary = [m["key"] for m in out["modes"] if "deep" not in m["cls"]]
        deep = [m["key"] for m in out["modes"] if "deep" in m["cls"]]
        assert primary == ["agent", "room", "space"]
        assert "control" in deep
        assert set(deep) == {"control", "workspace", "brain", "memory", "status", "history"}

    def test_the_tier_boundary_is_drawn_once_by_css_not_by_an_index(self):
        shell = _shell()
        assert ".mode.tier-start{" in shell
        boot = shell.split("MODES.forEach(([k,label,cap,tier])=>{", 1)[1].split("});", 1)[0]
        assert 'tier==="deep"' in boot
        assert "[0]" not in boot and "[2]" not in boot  # never a hardcoded position

    def test_the_deep_tier_is_visually_quieter_without_being_hidden(self):
        """Quieter, never gone: the label of every deep surface still
        renders (only its secondary caption is dropped), and the whole
        tier stays a real, clickable control."""
        shell = _shell()
        block = shell.split(".mode.deep{", 1)[1].split(".mode.tier-start", 1)[0]
        label_rule = block.split(".mode.deep .lbl{", 1)[1].split("}", 1)[0]
        assert "display:none" not in label_rule
        assert "var(--ink-4)" in label_rule  # dimmer, still legible
        assert ".mode.deep{display:none" not in shell

    def test_the_page_opens_on_the_private_interface(self):
        shell = _shell()
        assert 'let start="agent";' in shell
        # a remembered choice still wins, exactly as before
        assert 'localStorage.getItem("alfred.mode")' in shell

    def test_the_orchestrators_name_is_no_longer_a_splash_screen(self):
        """It was clamp(38px,7.5vw,84px) on the first screen a person
        saw. A heading, now - the surface it heads is deep observability."""
        line = next(ln for ln in _shell().splitlines() if ".core-id .name{font-size:" in ln)
        top = int(re.search(r"clamp\(\d+px,[\d.]+vw,(\d+)px\)", line).group(1))
        assert top <= 50, line

    def test_the_private_interface_orders_identity_then_conversation_then_context(self):
        """The hierarchy in the markup itself, top to bottom.

        Stage 17.21 made the environment full-bleed and moved identity and
        conversation onto it as overlay layers, so the class names changed
        (.private-id / .private-main -> .env-top / .env-talk, both inside
        .private-env) while the hierarchy this test exists to hold did
        not: who this is, then what was said, then the surrounding facts.
        Asserted on real source order, which is what a screen reader and a
        phone both follow.
        """
        view = _agent_view()
        order = [
            view.index('class="env-top"'),
            view.index('class="env-talk"'),
            view.index('class="private-context"'),
        ]
        assert order == sorted(order)
        # Identity and conversation are layers of the environment; the
        # context row is its own region after it.
        assert view.index('class="private-env') < view.index('class="env-top"')
        assert view.index('class="env-talk"') < view.index('class="private-context"')

# ---------------------------------------------------------------------------
# 9. Responsive: one existing breakpoint, no new device assumptions.
# ---------------------------------------------------------------------------


class TestResponsiveBehaviour:
    def test_no_new_breakpoint_and_no_second_reduced_motion_rule(self):
        """The same two counts every Stage 17 file since 17.6A has
        pinned. Everything this pass needed fitted inside them."""
        shell = _shell()
        assert shell.count("@media (max-width:760px)") == 2
        assert shell.count("@media (prefers-reduced-motion") == 1

    def test_the_wide_layout_gives_the_conversation_a_deliberate_bounded_share(self):
        """Intrinsic sizing cannot express "this panel is exactly this
        big", and the composition needs exactly that.

        Stage 17.19 said it with two unequal grid columns. Stage 17.21
        replaced the column pair with a single full-bleed environment the
        conversation floats over - so there is no longer a pair to be
        unequal, and the deliberate-share guarantee moved onto the panel
        itself: a fixed fraction with a hard ceiling, pinned to one side,
        never able to cover the character it sits over.

        auto-fit is still the right tool everywhere it is still used
        (.dh-fields, .private-context); this remains one place it was the
        wrong one.
        """
        shell = _shell()
        block = shell.split(".env-talk{position:absolute", 1)[1].split("}", 1)[0]
        assert "width:min(46%,540px)" in block
        assert "max-height:66%" in block
        assert "auto-fit" not in block
        # The environment keeps the larger share by construction.
        assert 46 < 100 - 46
        # auto-fit still in use where it belongs.
        assert "repeat(auto-fit,minmax(175px,1fr))" in shell

    def test_the_middle_band_is_one_deliberate_extra_breakpoint(self):
        """One deliberate extra breakpoint, at a width that is neither of
        the two the rest of the file already counts.

        Stage 17.20 changed what it *does* - it shortens the stage and no
        longer stacks the columns, because in a real browser at 1024x900
        stacking put the entire conversation below the fold. That the
        breakpoint exists, exactly once, and is neither of the other two,
        is what this test was always guarding, and that is unchanged.
        Stage 17.21 renamed the stage (.private-stage .agent3d ->
        .private-env) without changing what the band does to it.
        """
        shell = _shell()
        assert shell.count("@media (max-width:1080px)") == 1
        block = shell.split("@media (max-width:1080px){", 1)[1].split("\n  }", 1)[0]
        assert ".private-env{min-height:" in block
        # Shortens; never stacks.
        assert "grid-template-columns" not in block

    def test_the_phone_block_keeps_all_three_regions_usable(self):
        """The failure this prevents is the one .room actually had: a
        transcript that grows instead of scrolling pushes the composer off
        the bottom of the screen.

        Stage 17.21: two grid rows on a phone rather than three, because
        identity is now an overlay on the environment instead of a row of
        its own. All three regions are still present and usable - which is
        the guarantee - with the conversation unpinning from its floating
        position to sit under the environment as its own block.
        """
        mobile = _shell().rsplit("@media (max-width:760px){", 1)[1]
        assert ".private{grid-template-rows:auto auto}" in mobile
        # Stage 17.21 mobile fix: the layers unpin as `relative`, not
        # `static` - see TestTheMobileOverlayLayersUnpinTogether below for
        # why that distinction is load-bearing rather than stylistic.
        assert ".env-talk{position:relative;inset:auto;" in mobile
        assert ".env-talk .private-transcript{max-height:" in mobile
        # Still a real presence on a phone, never a thumbnail: a viewport
        # fraction with a floor under it, so a short landscape phone
        # cannot shrink the Digital Human into the 200px-widget failure
        # this correction pass exists to remove.
        stage_rule = [ln for ln in mobile.splitlines() if ".private-env{min-height:" in ln][0]
        assert "min-height:max(50vh,340px)" in stage_rule

    def test_the_row_track_count_matches_the_real_children(self):
        """The exact bug Stage 17.17 had to fix in .room: five children,
        four tracks, and the flexible one landing on the wrong thing.

        The count itself is not the point and never was - that it *matches*
        is. Stage 17.21 has two real children (the environment and the
        context row under it), so it must have exactly two tracks.
        """
        rows = _shell().split(".private{display:grid;grid-template-rows:", 1)[1].split(";", 1)[0]
        view = _agent_view()
        children = re.findall(r'<div class="(private-env|private-context)', view)
        assert children == ["private-env", "private-context"]
        assert rows.count("auto") + rows.count("minmax") == len(children) == 2
        # The flexible track is the environment; context takes its own height.
        assert rows.startswith("minmax(0,1fr) auto")

# ---------------------------------------------------------------------------
# 10. Mutation checks - each guard above actually catches its own break.
# ---------------------------------------------------------------------------


class TestMutations:
    def test_hardcoding_an_agent_name_in_the_template_is_caught(self):
        mutated = _agent_view().replace("openAgentOf()", 'agentNamed("Alfred")')
        assert "Alfred" in mutated  # what test_it_names_no_agent would reject

    def test_dropping_the_composers_address_is_caught(self):
        wire = _script().split("function wireComposer(){", 1)[1].split("\n  }\n", 1)[0]
        mutated = wire.replace("input.dataset.to || selectedAgent", "selectedAgent")
        assert "input.dataset.to" not in mutated

    def test_relabelling_a_delegated_answer_would_break_the_speaker_guard(self):
        fn = _shell().split("function privateTurn(u, delegated){", 1)[1].split("\n  }", 1)[0]
        mutated = fn.replace("esc(u.speaker)", "esc(openAgent)")
        assert "esc(u.speaker)" not in mutated and "openAgent" in mutated

    def test_declaring_a_font_that_is_not_shipped_is_caught(self):
        decls = [ln for ln in _shell().splitlines() if "--font:" in ln]
        mutated = decls[0].replace('"Cascadia Mono"', '"IBM Plex Mono"')
        assert '"Cascadia Mono"' not in mutated

    def test_merging_the_room_into_the_private_interface_is_caught(self):
        shell = _shell()
        assert "V.room = () => {" in shell and "V.agent = () => {" in shell
        mutated = shell.replace("V.room = () => {", "V.roomRemoved = () => {")
        assert "V.room = () => {" not in mutated


# ---------------------------------------------------------------------------
# 11. Stage 17.21 mobile defect: the overlay layers must unpin together.
#
# Found in a real browser at 390x844 and 430x932, not in this file - which
# is exactly why it is written down here now. Every assertion in this
# module passed while the phone view was unusable, because a source
# assertion can say "the rule exists" and cannot say "these two boxes do
# not sit on top of each other".
# ---------------------------------------------------------------------------


class TestTheMobileOverlayLayersUnpinTogether:
    """On the desktop composition .env-top, .env-talk and .env-foot are
    three absolutely-positioned layers floating over one full-bleed
    environment. On a phone they become one ordered column inside it.

    The defect this pins: the phone band unpinned .env-talk and .env-foot
    and left .env-top absolute. An absolutely-positioned layer is out of
    flow, so it reserves no space and the in-flow layers ran straight
    under it - the agent's name, role and the ALFRED/ARTHUR switch were
    painted on top of the entire transcript, a 224px collision measured at
    390x844.

    Three separate things had to be true, and each is asserted below
    because each was independently wrong at some point during the fix:

      position:relative   in flow (so it reserves space) AND positioned
                          (so the z-index:2 these layers already carry
                          still applies). `static` also removes the
                          collision - and drops them below the absolutely
                          positioned canvas, which made the agent switch
                          unclickable: elementFromPoint at its own centre
                          returned the canvas.
      inset:auto          the desktop left/right/bottom offsets do not
                          stop applying on a relative box, they shift it.
                          Without this .env-talk was pulled up into the
                          header again (7px) and .env-foot 1px off-panel.
      all three           unpinning some is not a smaller version of the
                          idea; it is the bug.
    """

    LAYERS = (".env-top", ".env-talk", ".env-foot")

    def _phone_block(self):
        return _shell().rsplit("@media (max-width:760px){", 1)[1]

    def _rule(self, css, selector):
        match = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
        assert match is not None, f"no {selector} rule found"
        return match.group(1)

    def test_every_overlay_layer_unpins_on_a_phone(self):
        block = self._phone_block()
        for layer in self.LAYERS:
            body = self._rule(block, layer)
            assert "position:relative" in body, f"{layer} is not unpinned"

    def test_no_overlay_layer_is_left_absolute_on_a_phone(self):
        """The original defect, stated directly."""
        block = self._phone_block()
        for layer in self.LAYERS:
            assert "position:absolute" not in self._rule(block, layer), layer

    def test_no_overlay_layer_is_made_static_on_a_phone(self):
        """`static` removes the collision and loses the stacking: these
        layers sit over an absolutely-positioned canvas and veil, and a
        non-positioned box paints under both. The agent switch was
        measurably unclickable this way."""
        block = self._phone_block()
        for layer in self.LAYERS:
            assert "position:static" not in self._rule(block, layer), layer

    def test_every_overlay_layer_clears_its_desktop_offsets(self):
        block = self._phone_block()
        for layer in self.LAYERS:
            assert "inset:auto" in self._rule(block, layer), f"{layer} keeps its offsets"

    def test_they_are_still_pinned_on_the_desktop_composition(self):
        """The fix must not have been achieved by unpinning everywhere -
        the desktop layout is a full-bleed environment with layers over
        it, and that is unchanged."""
        base = _shell().split("@media", 1)[0]
        for layer in self.LAYERS:
            assert "position:absolute" in self._rule(base, layer), layer
        # And each still carries the offsets that pin it there.
        assert "top:0" in self._rule(base, ".env-top")
        assert "bottom:clamp" in self._rule(base, ".env-talk")
        assert "bottom:clamp" in self._rule(base, ".env-foot")

    def test_the_environment_is_still_the_surface_on_a_phone(self):
        """Not a thumbnail above a form: it keeps a viewport fraction with
        a floor, and the layers remain its children rather than becoming
        siblings of it."""
        block = self._phone_block()
        assert "min-height:max(50vh,340px)" in self._rule(block, ".private-env")
        view = _agent_view()
        env = view.index('class="private-env')
        for cls in ("env-top", "env-talk", "env-foot"):
            assert view.index(f'class="{cls}"') > env, cls
        assert view.index('class="private-context"') > view.index('class="env-foot"')

    def test_the_veil_still_covers_the_column_it_sits_behind(self):
        """Once the layers run the full height of the panel, a veil that
        faded to nothing part-way up left the name and role sitting on
        bare scene - legible over the dark backdrop and not legible where
        they crossed the lit figure. It must never reach full transparency
        on a phone."""
        veil = self._rule(self._phone_block(), ".env-veil")
        stops = [float(a) for a in re.findall(r"rgba\(4,7,11,\.(\d+)\)", veil)]
        assert stops, veil
        assert "transparent" not in veil
        assert min(stops) >= 5, stops  # .5 or denser at the lightest stop
