"""Provider accounts UI (final acceptance pass, 2026-09-08).

The gap this check found: run_provider_command's add/select/revoke/
validate/accounts were already governed (confirmation-gated, audit-
recorded via classify_administrative_write) but had no guided Control
Center form - a user could only reach them by typing the raw
"provider <category> add/select/revoke <backend> <account-id> <credential>
[model]" command into the generic box, the exact "requires internal
command syntax" gap already fixed once for Connections. This proves the
guided form added to V.control()/wireControlCenterCommands() in
ui/index.html composes the identical command strings
run_provider_command() already accepts (see scripts/control_center.py),
reusing the exact same runCommand() confirm/cancel/token flow already
proven generically - never a second command-execution path.

Same Node vm-based extraction technique as
tests/test_control_center_connections_ui.py: the real
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
const clicked = process.argv[6];

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
  "cc-conn-service": makeEl("cc-conn-service"),
  "cc-conn-account": makeEl("cc-conn-account"),
  "cc-conn-action": makeEl("cc-conn-action"),
  "cc-conn-credential": makeEl("cc-conn-credential"),
  "cc-conn-model": makeEl("cc-conn-model"),
  "cc-conn-go": makeEl("cc-conn-go"),
  "cc-prov-category": makeEl("cc-prov-category", fields.category),
  "cc-prov-view": makeEl("cc-prov-view"),
  "cc-prov-accounts": makeEl("cc-prov-accounts"),
  "cc-prov-backend": makeEl("cc-prov-backend", fields.backend),
  "cc-prov-account": makeEl("cc-prov-account", fields.account),
  "cc-prov-action": makeEl("cc-prov-action", fields.action),
  "cc-prov-credential": makeEl("cc-prov-credential", fields.credential),
  "cc-prov-model": makeEl("cc-prov-model", fields.model),
  "cc-prov-go": makeEl("cc-prov-go"),
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

byId[clicked].click();

setTimeout(() => {
  console.log(JSON.stringify({
    fetchCalls, confirmCalls, outText: byId["cc-out"].textContent,
    credentialAfter: byId["cc-prov-credential"].value,
  }));
}, 50);
"""


def _run_harness(tmp_path, *, fields, clicked="cc-prov-go", fetch_script=(), confirm_answer: bool = True):
    node = _node()
    script_path = tmp_path / "ui_script.js"
    script_path.write_text(_script(), encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(_HARNESS, encoding="utf-8")
    fetch_script_path = tmp_path / "fetch_script.json"
    fetch_script_path.write_text(json.dumps(list(fetch_script)), encoding="utf-8")
    fields_full = {"category": "", "backend": "", "account": "", "action": "add", "credential": "", "model": "", **fields}

    result = subprocess.run(
        [
            node, str(harness_path), str(script_path), str(fetch_script_path),
            str(confirm_answer).lower(), json.dumps(fields_full), clicked,
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


PENDING_ADD = {
    "status": 200,
    "contentType": "application/json; charset=utf-8",
    "body": json.dumps({
        "requires_confirmation": True,
        "description": "add a new 'anthropic' account 'acc1' to 'llm_provider' (the credential itself is never shown here)",
        "confirm_token": "tok-prov-1",
    }),
}
EXECUTED_ADD = {
    "status": 200,
    "contentType": "application/json; charset=utf-8",
    "body": json.dumps({"requires_confirmation": False, "result": "added anthropic/acc1 (active) - no secret is ever echoed back."}),
}


class TestAddComposesTheRealCommandGrammar:
    def test_add_with_backend_account_and_credential(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "add", "credential": "sk-xyz"},
            fetch_script=[PENDING_ADD, EXECUTED_ADD],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider add anthropic acc1 sk-xyz"

    def test_add_appends_the_optional_model(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "add", "credential": "sk-xyz", "model": "claude-x"},
            fetch_script=[PENDING_ADD, EXECUTED_ADD],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider add anthropic acc1 sk-xyz claude-x"

    def test_add_without_a_credential_sends_no_fetch(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "add", "credential": ""},
        )
        assert result["fetchCalls"] == []
        assert "credential" in result["outText"]

    def test_add_without_a_backend_sends_no_fetch(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "", "account": "acc1", "action": "add", "credential": "sk-xyz"},
        )
        assert result["fetchCalls"] == []

    def test_the_credential_field_is_cleared_after_sending(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "add", "credential": "sk-xyz"},
            fetch_script=[PENDING_ADD, EXECUTED_ADD],
        )
        assert result["credentialAfter"] == ""

    def test_confirming_sends_the_real_token_back(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "add", "credential": "sk-xyz"},
            fetch_script=[PENDING_ADD, EXECUTED_ADD],
        )
        assert result["fetchCalls"][1]["body"]["confirm_token"] == "tok-prov-1"


class TestSelectAndRevokeComposeTheRealCommandGrammar:
    def test_select_with_backend_and_account(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "select"},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": True, "description": "select", "confirm_token": "t"}),
            }, {
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "anthropic/acc1 is now active."}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider select anthropic acc1"

    def test_select_without_an_account_sends_no_fetch(self, tmp_path):
        result = _run_harness(tmp_path, fields={"category": "llm_provider", "backend": "anthropic", "account": "", "action": "select"})
        assert result["fetchCalls"] == []

    def test_revoke_with_backend_and_account(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "acc1", "action": "revoke"},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": True, "description": "revoke", "confirm_token": "t"}),
            }, {
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "revoked anthropic/acc1"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider revoke anthropic acc1"

    def test_revoke_without_an_account_sends_no_fetch(self, tmp_path):
        result = _run_harness(tmp_path, fields={"category": "llm_provider", "backend": "anthropic", "account": "", "action": "revoke"})
        assert result["fetchCalls"] == []


class TestValidateComposesAReadOnlyCommand:
    def test_validate_without_a_credential_omits_it(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "", "action": "validate", "credential": ""},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "OK: reachable"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider validate anthropic"

    def test_validate_with_a_credential_includes_it(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider", "backend": "anthropic", "account": "", "action": "validate", "credential": "sk-xyz"},
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "OK: reachable"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider validate anthropic sk-xyz"


class TestViewAndListAccountsNeedOnlyACategory:
    def test_view_composes_the_bare_category_command(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider"},
            clicked="cc-prov-view",
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "describes llm_provider"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider"

    def test_list_accounts_composes_the_accounts_subcommand(self, tmp_path):
        result = _run_harness(
            tmp_path,
            fields={"category": "llm_provider"},
            clicked="cc-prov-accounts",
            fetch_script=[{
                "status": 200, "contentType": "application/json; charset=utf-8",
                "body": json.dumps({"requires_confirmation": False, "result": "no accounts registered"}),
            }],
        )
        assert result["fetchCalls"][0]["body"]["command"] == "provider llm_provider accounts"

    def test_view_without_a_category_sends_no_fetch(self, tmp_path):
        result = _run_harness(tmp_path, fields={"category": ""}, clicked="cc-prov-view")
        assert result["fetchCalls"] == []


class TestMissingCategoryIsRefusedLocally:
    def test_no_category_sends_no_fetch(self, tmp_path):
        result = _run_harness(tmp_path, fields={"category": "", "backend": "anthropic", "account": "acc1", "action": "add", "credential": "sk-xyz"})
        assert result["fetchCalls"] == []
        assert "category" in result["outText"]
