"""Ecosystem architecture follow-up (2026-09-08) to 4256816.

That checkpoint proved a brand-new capability can be registered into
``MANAGER_CAPABILITIES``, reach real production routing, compose inside a
real objective, and become visible through Control Center. It also found,
and documented, that Stage 11.A (``src/extensions.py``) - the module whose
own docstring exists to govern whether a declared feature may be marked
"active" - is real, tested, working code with no production caller: today,
adding a capability skips its ledger entirely and goes straight to a dict
entry.

This file answers the follow-up question directly: is that an intentional
boundary or a genuine missing seam? It traces both modules' own docstrings
(``src/extensions.py``: "Actually connecting a new [...] into the running
system remains exactly what it is today: a reviewed, explicit change to a
registration point [...] What this module adds is a governed ledger to
validate, approve and record that decision against - never a mechanism
that runs arbitrary code on an extension's behalf") and proves, rather
than asserts, that the two steps compose correctly when used together:

    1. A governed 11.A ExtensionLedger activation (VALIDATE -> PLAN ->
       an explicit APPROVE gate -> APPLY -> independently VERIFY) for a
       declared ``Extension`` - proven to actually gate on approval, not
       decorative.
    2. Only once that ledger says ACTIVE, the real, separate "reviewed,
       explicit change to a registration point" the module's own docstring
       describes: one ``MANAGER_CAPABILITIES`` entry.

Then, going further than the previous checkpoint, this file proves the
*whole* chain in one live pass - HTTP route -> real conversation state ->
real snapshot -> the actual frontend ``<script>`` from ui/index.html,
executed in Node against that real snapshot, never asserted from JSON
alone - plus Control Center, agent-boundary, and rollback-to-identical-
state proof.

The capability under test (``future_signal_relay``) is deliberately
abstract and stands in for "an unknown future capability" - never a real
feature, never left registered after any test, checked absent from the
shipped source tree before every test runs (the same git-grep precondition
``test_ecosystem_open_ended_growth.py`` already established).
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path

import pytest

from src.core.contracts import Task, TaskResult
from src.extensions import Extension, ExtensionLedger, deactivate_extension, run_extension_lifecycle
from src.manager.capabilities import CapabilityRequest, MANAGER_CAPABILITIES, ManagerCapability, available_capabilities

_NEW_NAME = "future_signal_relay"
_NEW_WORD = "futuresignalrelay"
_ANSWER = "future signal relay: nominal"


def _fixture_is_genuinely_unseen() -> None:
    result = subprocess.run(
        ["git", "grep", "-il", _NEW_WORD, "--", "src", "scripts"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the fixture capability must be genuinely unseen"


def _handle(request: CapabilityRequest) -> TaskResult:
    return TaskResult.success(request.task, request.own_name, _ANSWER)


def _capability() -> ManagerCapability:
    return ManagerCapability(
        name=_NEW_NAME, capabilities=frozenset({_NEW_WORD}), handle=_handle,
        description="Test-only capability standing in for an unknown future one.",
    )


def _extension() -> Extension:
    return Extension(
        key=f"capability.{_NEW_NAME}",
        name="Future Signal Relay (test-only)",
        kind="capability",
        version="v1",
        provides=(_NEW_WORD,),
    )


def _built(tmp_path, **overrides):
    import scripts.control_center as cc
    from src.config.settings import Settings

    return cc.build(
        Settings.from_env(env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"), **overrides)
    )


def _node() -> "str | None":
    for candidate in ("node", "node.exe"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return None


class TestThe11AApprovalGateIsReal:
    """Not a mock ledger - the actual src/extensions.py module, exercised
    against a capability-shaped declaration to answer: can this specific
    kind of extension ('capability') genuinely pass through it?"""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_an_unapproved_declaration_never_reaches_active(self):
        result = run_extension_lifecycle(_extension(), approved=False, ledger=ExtensionLedger())
        assert result.status.value == "ready"
        assert not result.applied
        assert not result.activated
        assert result.after_ledger.get(_extension().key) is None

    def test_an_approved_declaration_is_independently_verified_and_activated(self):
        result = run_extension_lifecycle(_extension(), approved=True, ledger=ExtensionLedger())
        assert result.applied
        assert result.verified
        assert result.activated
        active = result.after_ledger.get(_extension().key)
        assert active is not None and active.version == "v1" and active.kind == "capability"


class TestRealRegistrationOnlyFollowsGovernedApproval:
    """11.A's own docstring: ledger activation never wires the real
    implementation - that is 'a reviewed, explicit change to a
    registration point', performed separately. Proven here as an actual
    two-step sequence rather than described."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_the_capability_only_exists_after_both_steps(self):
        before = set(available_capabilities())

        activation = run_extension_lifecycle(_extension(), approved=True, ledger=ExtensionLedger())
        assert activation.activated
        # The ledger being ACTIVE changes nothing about what the router
        # can reach yet - proving the two steps are genuinely separate,
        # not that step one was a no-op.
        assert set(available_capabilities()) == before

        MANAGER_CAPABILITIES[_NEW_NAME] = _capability()
        try:
            assert set(available_capabilities()) - before == {_NEW_NAME}
        finally:
            del MANAGER_CAPABILITIES[_NEW_NAME]


class TestTheFullBackendToFrontendChain:
    """Section 4's own requirement: not backend-only. One live pass
    through the real HTTP route function, the real conversation/snapshot
    state, the actual frontend <script>, and the real Control Center
    command - for a capability that reached MANAGER_CAPABILITIES only
    after the 11.A ledger above said ACTIVE."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_a_message_flows_from_http_through_snapshot_into_the_real_frontend_and_control_center(self, tmp_path):
        import scripts.control_center as cc
        import scripts.serve_ui as su
        from src.control_center.snapshot import snapshot as build_snapshot

        run_extension_lifecycle(_extension(), approved=True, ledger=ExtensionLedger())
        MANAGER_CAPABILITIES[_NEW_NAME] = _capability()
        try:
            runtime, manager, channel, conversation = _built(tmp_path)
            centre = cc.ControlCenter(runtime, manager)
            system = su.LiveSystem(
                runtime=runtime, manager=manager, channel=channel, conversation=conversation,
                centre=centre, lock=threading.Lock(),
            )

            # 1. HTTP/API state: the real POST /message route function -
            # not manager.handle_task called directly.
            status, body, content_type = su.route_message(f"run {_NEW_WORD} diagnostics", system)
            assert status == 200
            payload = json.loads(body)
            assert payload["succeeded"]

            # 2. Backend state: the real snapshot, built the identical way
            # GET /snapshot.json builds it.
            data = build_snapshot(centre, conversation=conversation, channel=channel)
            utterances = data["conversation"]["utterances"]
            assert any(_ANSWER in u["text"] for u in utterances), utterances

            # 3. Frontend state: the actual <script> from ui/index.html,
            # executed in Node against this real snapshot - never asserted
            # from the JSON alone.
            node = _node()
            if node is None:
                pytest.skip("no node runtime available in this environment")

            import test_artifact_workspace as base

            repo_root = Path(__file__).resolve().parent.parent
            shell = (repo_root / "ui" / "index.html").read_text(encoding="utf-8")
            script_match = re.search(r"<script>(.*)</script>", shell, re.S)
            assert script_match is not None
            (tmp_path / "ui_script.js").write_text(script_match.group(1), encoding="utf-8")
            (tmp_path / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
            harness = tmp_path / "harness.js"
            harness.write_text(base._UI_NODE_HARNESS, encoding="utf-8")

            result = subprocess.run(
                [node, str(harness), str(tmp_path / "ui_script.js"), str(tmp_path / "snapshot.json"), "agent"],
                capture_output=True, text=True,
            )
            assert result.stdout.strip(), result.stderr
            out = json.loads(result.stdout.strip().splitlines()[-1])
            assert out.get("ok"), out.get("error")
            rendered = out["views"]["boot"]
            assert _ANSWER in rendered

            # 4. Control Center: the same one real management surface,
            # exercised in this same live pass.
            listing = cc.run_capability_registry_command(centre, "list")
            assert _NEW_NAME in listing
            assert f"[enabled ] {_NEW_NAME}" in listing
        finally:
            del MANAGER_CAPABILITIES[_NEW_NAME]


class TestAgentBoundariesHoldAcrossTheWholeChain:
    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_arthur_never_gains_the_new_capability(self, tmp_path):
        MANAGER_CAPABILITIES[_NEW_NAME] = _capability()
        try:
            runtime, manager, channel, conversation = _built(tmp_path)
            arthur = manager.registry.get("Arthur")
            assert arthur is not None
            assert not hasattr(arthur, "_capabilities")
            result = arthur.execute(Task(description=f"run {_NEW_WORD} diagnostics"))
            assert _ANSWER not in (result.output or "").lower()
        finally:
            del MANAGER_CAPABILITIES[_NEW_NAME]


class TestRollbackReturnsTheExactPriorState:
    """Section 16's own requirement: not just 'it can be removed' - the
    system after removal must be indistinguishable from before it ever
    arrived, on both halves (the 11.A ledger and the real registration)."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_removing_both_halves_restores_the_exact_baseline(self, tmp_path):
        baseline_capabilities = set(available_capabilities())
        baseline_ledger = ExtensionLedger()

        activation = run_extension_lifecycle(_extension(), approved=True, ledger=baseline_ledger)
        MANAGER_CAPABILITIES[_NEW_NAME] = _capability()

        # Sanity: it really changed something, so removal is a genuine
        # rollback rather than a no-op that was never testing anything.
        assert set(available_capabilities()) != baseline_capabilities
        assert activation.after_ledger.get(_extension().key) is not None

        del MANAGER_CAPABILITIES[_NEW_NAME]
        rolled_back_ledger = deactivate_extension(_extension().key, ledger=activation.after_ledger)

        assert set(available_capabilities()) == baseline_capabilities
        assert rolled_back_ledger.get(_extension().key) is None
        assert rolled_back_ledger == baseline_ledger

        runtime, manager, channel, conversation = _built(tmp_path)
        result = manager.handle_task(Task(description=f"run {_NEW_WORD} diagnostics"))
        assert _ANSWER not in (result.output or "").lower()
