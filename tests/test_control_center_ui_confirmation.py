"""Control Center repair item #3 (2026-09-07): the client-side half of
administrative-write confirmation.

The server-side half (tests/test_live_ui.py::TestAdministrativeConfirmation)
already proves the real governance boundary holds against a raw HTTP call.
This proves the *other* required behaviour - "the user must be able to
understand what change is about to happen, confirm it, or cancel it" - by
running the real, extracted wireControlCenterCommands() function against a
scripted fetch()/confirm(), the identical Node vm-based technique this
project's own Stage 17 tests already use (see
tests/test_stage17_18_private_agent_interface.py's own harness) - never a
hand-transcribed re-implementation of the JS.
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


# A minimal, purpose-built harness for exactly this repair item: a fake
# element for every id wireControlCenterCommands() actually queries,
# addEventListener that really registers and really fires, a fetch() this
# test script controls per-call, and a confirm() that records what it was
# asked and returns a scripted answer. Deliberately narrower than the full
# Stage 17 harness (no canvas/three.js/go() navigation needed - this
# function's own contract does not touch any of that): every element it
# stubs, it stubs because wireControlCenterCommands() genuinely calls it.
_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scriptSrc = fs.readFileSync(process.argv[2], "utf8");
const fetchScript = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const confirmAnswer = process.argv[4] === "true";

function makeEl(id) {
  const el = {
    id, _text: "", value: "", disabled: false, listeners: {},
    dataset: {},
    get textContent() { return el._text; },
    set textContent(v) { el._text = String(v); },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    click() { (el.listeners["click"] || []).forEach(fn => fn()); },
    closest() { return null; },
  };
  return el;
}

const byId = {
  "cc-out": makeEl("cc-out"),
  "cc-cmd": makeEl("cc-cmd"),
  "cc-run": makeEl("cc-run"),
  "cc-cap-name": makeEl("cc-cap-name"),
  "cc-cap-enable": makeEl("cc-cap-enable"),
  "cc-cap-disable": makeEl("cc-cap-disable"),
  "cc-eco-component": makeEl("cc-eco-component"),
  "cc-eco-action": makeEl("cc-eco-action"),
  "cc-eco-args": makeEl("cc-eco-args"),
  "cc-eco-go": makeEl("cc-eco-go"),
};

// The one stand-in for "the rendered stage element" - only the two
// selector methods wireControlCenterCommands() actually calls on it.
const stage = {
  querySelector: (sel) => {
    const m = /^#([\w-]+)$/.exec(sel);
    return m ? (byId[m[1]] || null) : null;
  },
  querySelectorAll: (sel) => (sel === "[data-cc-verb]" ? [] : []),
};

const fetchCalls = [];
let fetchCallIndex = 0;
function fetchStub(url, options) {
  fetchCalls.push({ url, body: JSON.parse(options.body) });
  const scripted = fetchScript[fetchCallIndex] || fetchScript[fetchScript.length - 1];
  fetchCallIndex += 1;
  return Promise.resolve({
    ok: scripted.status < 400,
    headers: { get: () => scripted.contentType },
    text: () => Promise.resolve(scripted.body),
  });
}

const confirmCalls = [];
function confirmStub(message) {
  confirmCalls.push(message);
  return confirmAnswer;
}

const sandbox = {
  console, document: { getElementById: (id) => byId[id] || null },
  fetch: fetchStub, confirm: confirmStub,
  setTimeout: (fn) => fn(), JSON,
};
vm.createContext(sandbox);

// Extract just wireControlCenterCommands() and its two direct
// dependencies (LIVE, stage) by evaluating the real script with `stage`
// and `LIVE` pre-seeded, then calling the real function - never a
// hand-copy of its body.
sandbox.stage = stage;
sandbox.LIVE = true;
const marker = "function wireControlCenterCommands(){";
const start = scriptSrc.indexOf(marker);
if (start === -1) { console.log(JSON.stringify({ error: "wireControlCenterCommands not found" })); process.exit(1); }
// Balance braces from the marker to find the real function's extent -
// robust to this function's own length changing later.
let depth = 0, i = start, end = -1;
for (; i < scriptSrc.length; i++) {
  if (scriptSrc[i] === "{") depth++;
  else if (scriptSrc[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
if (end === -1) { console.log(JSON.stringify({ error: "could not extract function body" })); process.exit(1); }
const fnSrc = scriptSrc.slice(start, end);

vm.runInContext(fnSrc + "\nthis.__wire = wireControlCenterCommands;", sandbox);
sandbox.__wire();

byId["cc-cap-name"].value = "small_talk";
byId["cc-cap-disable"].click();

setTimeout(() => {
  console.log(JSON.stringify({
    fetchCalls, confirmCalls, outText: byId["cc-out"].textContent,
  }));
}, 50);
"""


def _run_harness(tmp_path, *, fetch_script, confirm_answer: bool):
    node = _node()
    script_path = tmp_path / "ui_script.js"
    script_path.write_text(_script(), encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(_HARNESS, encoding="utf-8")
    fetch_script_path = tmp_path / "fetch_script.json"
    fetch_script_path.write_text(json.dumps(fetch_script), encoding="utf-8")

    result = subprocess.run(
        [node, str(harness_path), str(script_path), str(fetch_script_path), str(confirm_answer).lower()],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


PENDING_RESPONSE = {
    "status": 200,
    "contentType": "application/json; charset=utf-8",
    "body": json.dumps({
        "requires_confirmation": True,
        "description": "disable the 'small_talk' capability",
        "confirm_token": "tok-abc123",
    }),
}
EXECUTED_RESPONSE = {
    "status": 200,
    "contentType": "application/json; charset=utf-8",
    "body": json.dumps({"requires_confirmation": False, "result": "'small_talk' is now disabled"}),
}


class TestConfirmingProceedsToTheSecondCall:
    def test_confirm_is_shown_the_real_description(self, tmp_path):
        result = _run_harness(tmp_path, fetch_script=[PENDING_RESPONSE, EXECUTED_RESPONSE], confirm_answer=True)
        assert len(result["confirmCalls"]) == 1
        assert "disable the 'small_talk' capability" in result["confirmCalls"][0]

    def test_confirming_sends_the_exact_token_back(self, tmp_path):
        result = _run_harness(tmp_path, fetch_script=[PENDING_RESPONSE, EXECUTED_RESPONSE], confirm_answer=True)
        assert len(result["fetchCalls"]) == 2
        first_command = result["fetchCalls"][0]["body"]["command"]
        second = result["fetchCalls"][1]["body"]
        assert second["command"] == first_command
        assert second["confirm_token"] == "tok-abc123"

    def test_the_first_call_carries_no_token(self, tmp_path):
        result = _run_harness(tmp_path, fetch_script=[PENDING_RESPONSE, EXECUTED_RESPONSE], confirm_answer=True)
        assert "confirm_token" not in result["fetchCalls"][0]["body"]

    def test_the_final_result_is_shown_after_confirming(self, tmp_path):
        result = _run_harness(tmp_path, fetch_script=[PENDING_RESPONSE, EXECUTED_RESPONSE], confirm_answer=True)
        assert result["outText"] == "'small_talk' is now disabled"


class TestCancellingNeverSendsTheSecondCall:
    def test_cancelling_makes_only_one_fetch_call(self, tmp_path):
        result = _run_harness(tmp_path, fetch_script=[PENDING_RESPONSE], confirm_answer=False)
        assert len(result["fetchCalls"]) == 1

    def test_cancelling_reports_nothing_changed(self, tmp_path):
        result = _run_harness(tmp_path, fetch_script=[PENDING_RESPONSE], confirm_answer=False)
        assert result["outText"] == "Cancelled - nothing changed."
