"""Final pre-push connection check: the real quick-view buttons.

The gap this closes is not a missing feature - it is missing *proof*.
Phase 5.1's own tests already proved (a) "tasks" is generated as a
button by the same MGMT_VERBS mechanism every other read-only verb
(status/actions/capabilities/...) already uses (structural, by reading
ui/index.html), and (b) POST /command with {"command": "tasks"} returns
real backend state (live, over the real HTTP server). What was not yet
proven is the middle link: that clicking the real, shipped button
actually calls the real, shipped runCommand() with the right command
string and renders the real response - the same Node vm-based
extraction technique tests/test_control_center_ui_confirmation.py
already established, applied here to the generic MGMT_VERBS button
path that file's own harness stubbed out (querySelectorAll("[data-cc-
verb]") always returned [] there, since that file only exercised the
dedicated capability-registry form).

**Control Center integration gate (2026-09-09).** "recordings" and
"unresolved" were real, already-governed, already-tested server verbs
(`scripts/control_center.py`'s own `run_command`) with no corresponding
quick-view button - reachable only by typing the exact word into the
free-text command box, unlike every sibling read-only view (personal/
calendar/experience/tasks/...), which the whole point of MGMT_VERBS is
to make one click away. Added to MGMT_VERBS; `TestTheRealTasksButtonIs
GenuinelyConnected` below is generalised to `_run_button_harness(verb=...)`
so the identical, already-proven click-to-fetch-to-render chain is
re-verified for each one, not merely asserted present by name.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"


def _shell() -> str:
    return UI.read_text(encoding="utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


def _node() -> str:
    for candidate in ("node", "node.exe"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    pytest.skip("no node runtime available in this environment")


_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const fetchScript = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

function makeEl(id) {
  const el = {
    id, _text: "", value: "", disabled: false, listeners: {}, dataset: {},
    get textContent() { return el._text; },
    set textContent(v) { el._text = String(v); },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    click() { (el.listeners["click"] || []).forEach(fn => fn()); },
    closest() { return null; },
  };
  return el;
}

const byId = { "cc-out": makeEl("cc-out"), "cc-cmd": makeEl("cc-cmd"), "cc-run": makeEl("cc-run") };

// The real MGMT_VERBS array, extracted from the real shipped script -
// never hand-copied - so this test fails loudly if "tasks" is ever
// removed from it, rather than silently testing a stale list.
const verbsMatch = scriptSrc.match(/const MGMT_VERBS = \[([^\]]*)\];/);
if (!verbsMatch) { console.log(JSON.stringify({ error: "MGMT_VERBS not found" })); process.exit(1); }
const verbs = verbsMatch[1].split(",").map(s => s.trim().replace(/^"|"$/g, "")).filter(Boolean);

// The real markup template MGMT_VERBS.map(...) uses, reproduced only in
// its DOM *shape* (a button carrying data-cc-verb), never its command
// logic - the logic under test is runCommand()/the click wiring below,
// pulled from the real script exactly as test_control_center_ui_
// confirmation.py's own harness already does for the capability form.
const verbButtons = verbs.map(v => {
  const btn = makeEl(`verb-${v}`);
  btn.dataset.ccVerb = v;
  return btn;
});

const stage = {
  querySelector: (sel) => { const m = /^#([\w-]+)$/.exec(sel); return m ? (byId[m[1]] || null) : null; },
  querySelectorAll: (sel) => (sel === "[data-cc-verb]" ? verbButtons : []),
};

const fetchCalls = [];
function fetchStub(url, options) {
  fetchCalls.push({ url, body: JSON.parse(options.body) });
  const scripted = fetchScript[0];
  return Promise.resolve({
    ok: scripted.status < 400,
    headers: { get: () => scripted.contentType },
    text: () => Promise.resolve(scripted.body),
  });
}

const sandbox = {
  console, document: { getElementById: (id) => byId[id] || null },
  fetch: fetchStub, confirm: () => true, setTimeout: (fn) => fn(), JSON,
};
vm.createContext(sandbox);
sandbox.stage = stage;
sandbox.LIVE = true;

const marker = "function wireControlCenterCommands(){";
const start = scriptSrc.indexOf(marker);
if (start === -1) { console.log(JSON.stringify({ error: "wireControlCenterCommands not found" })); process.exit(1); }
let depth = 0, i = start, end = -1;
for (; i < scriptSrc.length; i++) {
  if (scriptSrc[i] === "{") depth++;
  else if (scriptSrc[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
if (end === -1) { console.log(JSON.stringify({ error: "could not extract function body" })); process.exit(1); }
vm.runInContext(scriptSrc.slice(start, end) + "\nthis.__wire = wireControlCenterCommands;", sandbox);
sandbox.__wire();

const targetVerb = process.argv[4];
const targetButton = verbButtons.find(b => b.dataset.ccVerb === targetVerb);
if (!targetButton) { console.log(JSON.stringify({ error: `no ${targetVerb} button generated from MGMT_VERBS`, verbs })); process.exit(1); }
targetButton.click();

setTimeout(() => {
  console.log(JSON.stringify({ verbs, fetchCalls, outText: byId["cc-out"].textContent }));
}, 50);
"""


def _run_harness(tmp_path, *, fetch_body: str, verb: str = "tasks"):
    node = _node()
    script_path = tmp_path / "ui_script.js"
    script_path.write_text(_script(), encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(_HARNESS, encoding="utf-8")
    fetch_script_path = tmp_path / "fetch_script.json"
    fetch_script_path.write_text(
        json.dumps([{"status": 200, "contentType": "application/json; charset=utf-8", "body": fetch_body}]),
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(harness_path), str(script_path), str(fetch_script_path), verb],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestTheRealTasksButtonIsGenuinelyConnected:
    def test_tasks_is_among_the_real_generated_quick_view_buttons(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fetch_body=json.dumps({"requires_confirmation": False, "result": "You have no pending tasks."}),
        )
        assert "tasks" in result["verbs"]

    def test_clicking_the_real_button_sends_the_exact_tasks_command(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fetch_body=json.dumps({"requires_confirmation": False, "result": "You have no pending tasks."}),
        )
        assert len(result["fetchCalls"]) == 1
        assert result["fetchCalls"][0]["body"]["command"] == "tasks"
        assert "confirm_token" not in result["fetchCalls"][0]["body"]

    def test_the_real_backend_response_is_rendered_into_cc_out(self, tmp_path):
        """Real UI state: whatever the backend genuinely returns is what
        a person sees - not a placeholder, not a hardcoded string."""
        result = _run_harness(
            tmp_path,
            fetch_body=json.dumps({
                "requires_confirmation": False,
                "result": "TASKS - Alfred\n  [ ] buy milk (abc123def456)",
            }),
        )
        assert result["outText"] == "TASKS - Alfred\n  [ ] buy milk (abc123def456)"

    def test_a_read_only_verb_never_triggers_the_confirmation_flow(self, tmp_path):
        """Tasks visibility must never be gated behind a confirm() the
        way an administrative write's second call already is - proven by
        a scripted response that would immediately fail the test if the
        client-side code tried to read requires_confirmation/confirm_token
        off it and got nothing to work with, since only one fetch call
        happens either way."""
        result = _run_harness(
            tmp_path,
            fetch_body=json.dumps({"requires_confirmation": False, "result": "no pending tasks"}),
        )
        assert len(result["fetchCalls"]) == 1


@pytest.mark.parametrize("verb", ["recordings", "unresolved", "services"])
class TestTheNewlyConnectedReadOnlyButtonsWork:
    """Control Center integration gate: 'recordings', 'unresolved' and
    (open-ended service discovery, 2026-09-09) 'services' were real,
    governed, tested server verbs with no one-click path in the served
    page - reachable only by typing the exact word into the free-text
    command box. Every sibling read-only verb (tasks, above) already
    gets the full click-to-fetch-to-render proof; these three now get
    the identical proof, not just a name check. 'services' with no
    argument is the bare, read-only list - declare/forget still need
    arguments the generic free-text box supplies, unchanged."""

    def test_the_verb_is_among_the_real_generated_quick_view_buttons(self, tmp_path, verb):
        result = _run_harness(
            tmp_path, verb=verb,
            fetch_body=json.dumps({"requires_confirmation": False, "result": "placeholder"}),
        )
        assert verb in result["verbs"]

    def test_clicking_the_real_button_sends_the_exact_command(self, tmp_path, verb):
        result = _run_harness(
            tmp_path, verb=verb,
            fetch_body=json.dumps({"requires_confirmation": False, "result": "placeholder"}),
        )
        assert len(result["fetchCalls"]) == 1
        assert result["fetchCalls"][0]["body"]["command"] == verb
        assert "confirm_token" not in result["fetchCalls"][0]["body"]

    def test_the_real_backend_response_is_rendered_into_cc_out(self, tmp_path, verb):
        result = _run_harness(
            tmp_path, verb=verb,
            fetch_body=json.dumps({
                "requires_confirmation": False,
                "result": f"real {verb} output, not a placeholder",
            }),
        )
        assert result["outText"] == f"real {verb} output, not a placeholder"

    def test_never_triggers_the_confirmation_flow(self, tmp_path, verb):
        result = _run_harness(
            tmp_path, verb=verb,
            fetch_body=json.dumps({"requires_confirmation": False, "result": "placeholder"}),
        )
        assert len(result["fetchCalls"]) == 1


class TestTheServerGenuinelyAcceptsBothVerbsAndNeverAsksForConfirmation:
    """The other half of the connection: the button is real, and the
    verb it sends is one the real, unmocked classify_administrative_write
    (scripts/control_center.py) genuinely treats as read-only - not just
    something the frontend hopes is true."""

    def test_recordings_is_never_classified_as_an_administrative_write(self):
        from scripts.control_center import classify_administrative_write

        is_write, _ = classify_administrative_write("recordings")
        assert is_write is False

    def test_unresolved_is_never_classified_as_an_administrative_write(self):
        from scripts.control_center import classify_administrative_write

        is_write, _ = classify_administrative_write("unresolved")
        assert is_write is False
