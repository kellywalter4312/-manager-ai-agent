"""Connections Management UI (2026-09-08).

The gap the completeness review named explicitly: a view-only quick button
existed, but connecting or disconnecting a service required typing the raw
"connections <service> connect <account-id> <credential> [model]" command
into the generic command box - usable only by someone who already knew the
backend's own grammar. This proves the guided form added to
V.control()/wireControlCenterCommands() in ui/index.html composes the
identical command strings run_connections_command() already accepts (see
scripts/control_center.py), reusing the exact same runCommand() confirm/
cancel/token flow tests/test_control_center_ui_confirmation.py already
proves against the shared function - never a second command-execution path.

Same Node vm-based extraction technique as that file: the real
wireControlCenterCommands() function, pulled straight out of the shipped
script and run against a scripted fetch()/confirm(), never a hand-copy of
the JS.
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
const confirmAnswer = process.argv[4] === "true";
const fields = JSON.parse(process.argv[5]);

function makeEl(id, initial) {
  const el = {
    id, _text: "", value: initial || "", disabled: false, listeners: {},
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
  "cc-conn-service": makeEl("cc-conn-service", fields.service),
  "cc-conn-account": makeEl("cc-conn-account", fields.account),
  "cc-conn-action": makeEl("cc-conn-action", fields.action),
  "cc-conn-credential": makeEl("cc-conn-credential", fields.credential),
  "cc-conn-model": makeEl("cc-conn-model", fields.model),
  "cc-conn-go": makeEl("cc-conn-go"),
};

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
const fnSrc = scriptSrc.slice(start, end);

vm.runInContext(fnSrc + "\nthis.__wire = wireControlCenterCommands;", sandbox);
sandbox.__wire();

byId["cc-conn-go"].click();

setTimeout(() => {
  console.log(JSON.stringify({
    fetchCalls, confirmCalls, outText: byId["cc-out"].textContent,
    credentialAfter: byId["cc-conn-credential"].value,
  }));
}, 50);
"""


def _run_harness(tmp_path, *, fields, fetch_script=(), confirm_answer: bool = True):
    node = _node()
    script_path = tmp_path / "ui_script.js"
    script_path.write_text(_script(), encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(_HARNESS, encoding="utf-8")
    fetch_script_path = tmp_path / "fetch_script.json"
    fetch_script_path.write_text(json.dumps(list(fetch_script)), encoding="utf-8")
    fields_full = {"service": "", "account": "", "action": "connect", "credential": "", "model": "", **fields}

    result = subprocess.run(
        [
            node, str(harness_path), str(script_path), str(fetch_script_path),
            str(confirm_answer).lower(), json.dumps(fields_full),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


PENDING_CONNECT = {
    "status": 200,
    "contentType": "application/json; charset=utf-8",
    "body": json.dumps({
        "requires_confirmation": True,
        "description": "connect the 'whatsapp' connection",
        "confirm_token": "tok-conn-1",
    }),
}
EXECUTED_CONNECT = {
    "status": 200,
    "contentType": "application/json; charset=utf-8",
    "body": json.dumps({"requires_confirmation": False, "result": "connected whatsapp/acc1 - no secret is ever echoed back."}),
}


class TestConnectComposesTheRealCommandGrammar:
    def test_connect_with_account_and_credential(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "acc1", "action": "connect", "credential": "tok-xyz"},
            fetch_script=[PENDING_CONNECT, EXECUTED_CONNECT],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "connections whatsapp connect acc1 tok-xyz"

    def test_connect_appends_the_optional_model(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "acc1", "action": "connect", "credential": "tok-xyz", "model": "gpt-x"},
            fetch_script=[PENDING_CONNECT, EXECUTED_CONNECT],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "connections whatsapp connect acc1 tok-xyz gpt-x"

    def test_connect_without_a_credential_sends_no_fetch(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "acc1", "action": "connect", "credential": ""},
        )
        assert result["fetchCalls"] == []
        assert "credential" in result["outText"]

    def test_connect_without_an_account_sends_no_fetch(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "", "action": "connect", "credential": "tok-xyz"},
        )
        assert result["fetchCalls"] == []

    def test_the_credential_field_is_cleared_after_sending(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "acc1", "action": "connect", "credential": "tok-xyz"},
            fetch_script=[PENDING_CONNECT, EXECUTED_CONNECT],
        )
        assert result["credentialAfter"] == ""

    def test_confirming_sends_the_real_token_back(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "acc1", "action": "connect", "credential": "tok-xyz"},
            fetch_script=[PENDING_CONNECT, EXECUTED_CONNECT],
        )
        assert result["fetchCalls"][1]["body"]["confirm_token"] == "tok-conn-1"


class TestDisconnectComposesTheRealCommandGrammar:
    def test_disconnect_with_an_account(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "acc1", "action": "disconnect"},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": True, "description": "disconnect", "confirm_token": "t"}),
            }, {
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "disconnected"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "connections whatsapp disconnect acc1"

    def test_disconnect_without_an_account_sends_no_fetch(self, tmp_path):
        result = _run_harness(tmp_path, fields={"service": "whatsapp", "account": "", "action": "disconnect"})
        assert result["fetchCalls"] == []


class TestStatusAndValidateComposeReadOnlyCommands:
    def test_status_needs_only_a_service(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "", "action": "status"},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "whatsapp: not connected"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "connections whatsapp status"

    def test_validate_without_a_credential_omits_it(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "", "action": "validate", "credential": ""},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "OK: reachable"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "connections whatsapp validate"

    def test_validate_with_a_credential_includes_it(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"service": "whatsapp", "account": "", "action": "validate", "credential": "tok-xyz"},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "OK: reachable"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "connections whatsapp validate tok-xyz"


class TestMissingServiceIsRefusedLocally:
    def test_no_service_sends_no_fetch(self, tmp_path):
        result = _run_harness(tmp_path, fields={"service": "", "action": "status"})
        assert result["fetchCalls"] == []
        assert "service" in result["outText"]
