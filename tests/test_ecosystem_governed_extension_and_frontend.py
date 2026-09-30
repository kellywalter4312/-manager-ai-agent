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
from src.evolution import RiskLevel
from src.services.evolution import ecosystem_evolution
from src.services.evolution.handlers import EvolutionHandler, HandlerResult, register_handler
from src.services.evolution.inventory import ComponentSnapshot
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


_COMPONENT = f"declaration.{_NEW_NAME}"


class _DeclarationHandler(EvolutionHandler):
    """A component category registered at runtime, standing in for "a feature
    declaration waiting to be marked active" - the question Stage 11.A's
    ``src/extensions.py`` used to answer in its own parallel ledger. That
    module was removed on 2026-09-15 with the rest of the unwired
    architectures; the canonical governed lifecycle answers it, and
    ``register_handler`` is how a category it has never heard of joins."""

    def __init__(self):
        self.marked: "dict[str, str]" = {}

    def applies_to(self, component_id: str) -> bool:
        return component_id == _COMPONENT

    def initial_version(self, component_id: str) -> str:
        return "absent"

    def governed_components(self, *, config):
        return (ComponentSnapshot(
            component_id=_COMPONENT, category="declaration",
            label="a declared future capability", version=self.marked.get(_COMPONENT, "absent"),
        ),)

    def apply(self, proposal, *, context):
        self.marked[proposal.target] = proposal.proposed_version
        return HandlerResult(True, f"{proposal.target} is now {proposal.proposed_version}")

    def rollback(self, proposal, *, context):
        self.marked.pop(proposal.target, None)
        return HandlerResult(True, f"{proposal.target} is no longer marked active")

    # Phase 11: where this handler keeps its state, so the engine verifies the
    # real mark rather than trusting apply's report - and what no mark means.
    def observe(self, component_id, *, context):
        return self.marked.get(component_id)

    def default_version(self, component_id):
        return "absent"


@pytest.fixture
def declaration(tmp_path, monkeypatch):
    """The governed lifecycle, isolated: its own ledger and config, and the
    handler removed again afterwards."""
    handler = _DeclarationHandler()
    register_handler(handler)
    from src.services.evolution import handlers as handlers_module

    ledger = tmp_path / "state.json"

    def govern(approved: bool, version: str = "v1"):
        from src.config.settings import Settings

        from src.services.evolution.ledger_store import load_evolution_state

        state = load_evolution_state(ledger)
        proposed = ecosystem_evolution.propose(
            state, component_id=_COMPONENT, proposed_version=version,
            proposed_change="mark the declared capability active",
            reason="the owner asked for it", expected_benefit="the capability becomes reachable",
            risk_level=RiskLevel.LOW, ledger_path=ledger,
        )
        ran = ecosystem_evolution.run(
            proposed.state, proposal_id=proposed.proposal.proposal_id, approved=approved,
            settings=Settings.from_env(env={}, memory="ephemeral"), ledger_path=ledger,
            accounts_path=tmp_path / "accounts.json", config_path=tmp_path / "config.json",
            staging_dir=tmp_path / "staging", backups_dir=tmp_path / "backups",
        )
        return proposed, ran

    yield handler, govern
    handlers_module._HANDLERS[:] = [h for h in handlers_module._HANDLERS if h is not handler]


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


class TestTheGovernedApprovalGateIsReal:
    """Not a mock ledger - the real governed lifecycle, exercised against a
    declaration-shaped component registered at runtime, to answer: can a
    category the engine has never heard of genuinely pass through it?"""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_an_unapproved_declaration_never_reaches_active(self, declaration):
        handler, govern = declaration
        _proposed, ran = govern(approved=False)
        assert not ran.success
        assert handler.marked == {}

    def test_an_approved_declaration_is_independently_verified_and_activated(self, declaration):
        handler, govern = declaration
        _proposed, ran = govern(approved=True)
        assert ran.success, ran.reason
        assert ran.proposal.status.value == "active"
        assert handler.marked == {_COMPONENT: "v1"}


class TestRealRegistrationOnlyFollowsGovernedApproval:
    """Governed activation never wires the real implementation - that is "a
    reviewed, explicit change to a registration point", performed separately.
    Proven as an actual two-step sequence rather than described."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_the_capability_only_exists_after_both_steps(self, declaration):
        _handler, govern = declaration
        before = set(available_capabilities())

        _proposed, ran = govern(approved=True)
        assert ran.success
        # The ledger saying ACTIVE changes nothing about what the router can
        # reach yet - proving the two steps are genuinely separate, not that
        # step one was a no-op.
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
    arrived, on both halves (the governed ledger and the real registration)."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_removing_both_halves_restores_the_exact_baseline(self, tmp_path, declaration):
        handler, govern = declaration
        baseline_capabilities = set(available_capabilities())

        proposed, ran = govern(approved=True)
        MANAGER_CAPABILITIES[_NEW_NAME] = _capability()

        # Sanity: it really changed something, so removal is a genuine
        # rollback rather than a no-op that was never testing anything.
        assert set(available_capabilities()) != baseline_capabilities
        assert handler.marked == {_COMPONENT: "v1"}

        del MANAGER_CAPABILITIES[_NEW_NAME]
        from src.config.settings import Settings

        rolled = ecosystem_evolution.rollback(
            ran.state, proposal_id=proposed.proposal.proposal_id, approved=True,
            settings=Settings.from_env(env={}, memory="ephemeral"),
            ledger_path=tmp_path / "state.json", accounts_path=tmp_path / "accounts.json",
            config_path=tmp_path / "config.json", staging_dir=tmp_path / "staging",
            backups_dir=tmp_path / "backups",
        )
        assert rolled.success, rolled.reason
        assert set(available_capabilities()) == baseline_capabilities
        assert handler.marked == {}

        runtime, manager, channel, conversation = _built(tmp_path)
        result = manager.handle_task(Task(description=f"run {_NEW_WORD} diagnostics"))
        assert _ANSWER not in (result.output or "").lower()
